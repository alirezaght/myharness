"""Strict dashboard retry uses the durable request, never redacted client text."""

import http.client
import json
import threading
from pathlib import Path

import pytest

from harness.dashboard.server import DashboardServer
from harness.execution_contract import canonical
from harness.execution_decisions import record_execution_decision
from harness.execution_projection import ingest_event
from tests.execution_helpers import approved_execution, event, started


@pytest.fixture
def decision_fixture(controller, sample_repo, monkeypatch):
    import harness.execution_decisions as service

    tid, eid = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, eid)
    ingest_event(
        controller.conn,
        eid,
        event(
            controller.conn,
            eid,
            2,
            "task.registered",
            {
                "id": "A",
                "title": "A",
                "status": "Idle",
                "dependencies": [],
                "files": ["svc"],
                "criteria": ["AC-01"],
                "revision": 1,
            },
        ),
    )
    ingest_event(
        controller.conn,
        eid,
        event(
            controller.conn,
            eid,
            3,
            "blocker.raised",
            {
                "id": "environment",
                "revision": 1,
                "tasks": ["A"],
                "question": "Repair environment?",
                "context": "Discovery and attribution unavailable",
                "alternatives": ["repair", "wait"],
                "recommendation": "repair",
            },
        ),
    )
    original = service.durable_file

    def denied(*args, **kwargs):
        raise PermissionError("permission denied for synthetic Registry")

    monkeypatch.setattr(service, "durable_file", denied)
    with pytest.raises(PermissionError):
        record_execution_decision(
            controller,
            eid,
            "environment",
            blocker_revision=1,
            request_id="saved-request",
            verdict="alternative",
            decision_text="I will fix harness myself.",
            tasks=["A"],
            criteria=["AC-01"],
        )
    monkeypatch.setattr(service, "durable_file", original)
    row = dict(controller.conn.execute("SELECT * FROM execution_decisions").fetchone())
    return tid, eid, row


def post(server, body):
    client = http.client.HTTPConnection("127.0.0.1", server.port)
    try:
        client.request(
            "POST",
            "/api/execution/decision-retry",
            body=canonical(body),
            headers={"Origin": server.url.rstrip("/"), "Content-Type": "application/json"},
        )
        response = client.getresponse()
        return response.status, json.loads(response.read())
    finally:
        client.close()


def test_retry_http_preserves_frozen_bytes_and_rejects_foreign_identity(
    controller, db_path, decision_fixture
):
    tid, eid, before = decision_fixture
    server = DashboardServer(db_path, tid)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    body = {"task": tid, "execution": eid, "delivery": before["id"]}
    try:
        assert post(server, body | {"text": "altered"})[0] == 400
        assert post(server, body | {"delivery": "missing-request"})[0] == 404
        assert post(server, body | {"task": "foreign-task"})[0] == 404
        # No AL source exists yet, so delivery fails after successful Registry recovery.
        assert post(server, body)[0] == 503
        assert post(server, body)[0] == 503
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    after = dict(controller.conn.execute("SELECT * FROM execution_decisions").fetchone())
    assert after["request"] == before["request"]
    assert after["registry_bytes"] == before["registry_bytes"]
    assert Path(after["registry_path"]).read_text() == before["registry_bytes"]
    assert controller.conn.execute("SELECT count(*) FROM execution_decisions").fetchone()[0] == 1


def test_acknowledged_retry_does_not_rematerialize(
    controller, db_path, decision_fixture, monkeypatch
):
    import harness.execution_decisions as service
    from harness.execution_decisions import materialize_decision, retry_execution_decision

    _, eid, before = decision_fixture
    materialize_decision(controller, before["id"])
    controller.conn.execute(
        "UPDATE execution_decisions SET state='Resolved',error=NULL WHERE id=?", (before["id"],)
    )

    def forbidden(*args):
        raise AssertionError("terminal decision rematerialized")

    monkeypatch.setattr(service, "recover_decision_intent", forbidden)
    assert retry_execution_decision(controller, eid, before["id"])["state"] == "Resolved"


def test_concurrent_http_retry_publishes_one_frozen_delivery(
    controller, db_path, decision_fixture, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor

    import harness.dashboard.execution_view as view
    from harness.execution_handoff import read_handoff
    from harness.execution_hosts import al_source
    from harness.execution_store import get_execution

    tid, eid, before = decision_fixture
    saved = get_execution(controller.conn, eid)
    source = al_source(saved, read_handoff(saved["handoff_path"]))
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        canonical(
            {
                "run_id": saved["al_run_id"],
                "integration": {"binding": {"execution_id": eid}},
                "amendments": [],
                "task_package": {"sha256": "synthetic-original-package"},
            }
        )
    )
    # Delivery replay is separately covered; never launch a native provider in this fixture.
    monkeypatch.setattr(view, "reconnect_execution", lambda *args: None)
    server = DashboardServer(db_path, tid)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _: post(
                        server, {"task": tid, "execution": eid, "delivery": before["id"]}
                    ),
                    range(2),
                )
            )
        assert all(status == 200 and value["state"] == "Delivered" for status, value in results), (
            results
        )
        assert all(
            "registry_bytes" not in value and "delivery" not in value for _, value in results
        )
        after = dict(controller.conn.execute("SELECT * FROM execution_decisions").fetchone())
        delivery = Path(saved["handoff_path"]).parent / "inbox" / (before["id"] + ".json")
        raw = delivery.read_bytes()
        assert raw == after["delivery"].encode()
        assert json.loads(raw)["text"] == "I will fix harness myself."
        assert (
            after["registry_bytes"] == before["registry_bytes"]
            and after["request"] == before["request"]
        )
        assert post(server, {"task": tid, "execution": eid, "delivery": before["id"]})[0] == 200
        assert delivery.read_bytes() == raw
        assert (
            controller.conn.execute("SELECT count(*) FROM execution_decisions").fetchone()[0] == 1
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
