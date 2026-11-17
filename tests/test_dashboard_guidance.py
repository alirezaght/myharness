"""Public launch and shared task guidance regressions."""

import http.client
import json
from pathlib import Path

import pytest

from harness.approval import ApprovalGate
from harness.dashboard.guidance import task_guidance
from harness.dashboard.view import dashboard_view
from harness.execution import start_execution
from harness.spec_store import SpecStore
from tests.complete_task import settle
from tests.test_dashboard import api, serve


@pytest.fixture
def approved(controller, sample_repo, monkeypatch):
    from tests.execution_helpers import isolated_launch_environment

    isolated_launch_environment(monkeypatch, sample_repo.parent / "launch-fixture")
    monkeypatch.chdir(sample_repo)
    tid = controller.create_task(
        "Implement safe <script>alert(1)</script> fixture changes " + "unbroken" * 30, "trivial"
    ).id
    settle(controller, tid)
    controller.advance(tid)
    ApprovalGate(
        controller, tid, SpecStore(sample_repo / "docs/specs" / tid), sample_repo
    ).approve()
    return tid


def post_start(server, body):
    client = http.client.HTTPConnection("127.0.0.1", server.port)
    try:
        client.request(
            "POST",
            "/api/execution/start",
            json.dumps(body),
            {"Content-Type": "application/json", "Origin": server.url.rstrip("/")},
        )
        response = client.getresponse()
        return response.status, json.loads(response.read())
    finally:
        client.close()


def test_current_approval_launch_and_lost_response_reuse(
    controller, approved, db_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        "harness.execution_worker.launch_worker",
        lambda conn, eid: calls.append(eid) or {"status": "Reserved"},
    )
    with serve(db_path, approved) as server:
        view = api(server)
        assert view["task"]["guidance"] == view["tasks"][0]["guidance"]
        assert "external-session" in view["task"]["guidance"]["activity"]
        assert not calls
        body = {
            "task": approved,
            "host": "codex",
            "approval_binding": view["task"]["launch"]["binding"],
        }
        first, result = post_start(server, body)
        second, retry = post_start(server, body)
        assert first == second == 200
        assert result["execution"]["id"] == retry["execution"]["id"]
        assert result["created"] and not retry["created"]
        assert post_start(server, body | {"host": "claude"})[0] == 409
        assert api(server)["task"]["execution"]["id"] == result["execution"]["id"]
    assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1
    assert controller.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1
    assert len(set(calls)) == 1


def test_stale_display_rejected_before_publication(controller, approved, db_path, monkeypatch):
    launches = []
    monkeypatch.setattr("harness.execution_worker.launch_worker", lambda *a: launches.append(a))
    view = dashboard_view(db_path, approved, None)
    old = view["task"]["launch"]["binding"]
    # A genuinely newly granted current approval has a different ID even for identical content.
    ApprovalGate(
        controller,
        approved,
        SpecStore(Path(controller.workspace(approved).latest_approval().path).parent),
        Path.cwd(),
    ).approve()
    monkeypatch.setattr(
        "harness.execution.publish", lambda *a: pytest.fail("stale request published a handoff")
    )
    with serve(db_path, approved) as server:
        status, result = post_start(
            server, {"task": approved, "host": "claude", "approval_binding": old}
        )
        assert status == 409 and result["reload"]
    assert not launches
    assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0


def test_closed_schema_and_wrong_binding(controller, approved, db_path, monkeypatch):
    monkeypatch.setattr(
        "harness.execution_worker.launch_worker", lambda *a: pytest.fail("invalid request launched")
    )
    with serve(db_path, approved) as server:
        binding = api(server)["task"]["launch"]["binding"]
        body = {"task": approved, "host": "codex", "approval_binding": binding}
        for invalid in (
            body | {"extra": 1},
            body | {"host": "other"},
            body | {"host": {}},
            body | {"task": "wrong"},
            body | {"approval_binding": binding | {"approval_id": True}},
        ):
            assert post_start(server, invalid)[0] == 400
        status, _ = post_start(
            server, body | {"approval_binding": binding | {"content_hash": "stale"}}
        )
        assert status == 409
    assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0


@pytest.mark.parametrize(
    "execution, expected",
    [
        ({"status": "Running"}, "Work in progress"),
        ({"status": "Launching"}, "Starting implementation"),
        (
            {"status": "Interrupted", "agents": [{"status": "Running"}]},
            "Work stopped · action needed",
        ),
        ({"status": "Completed", "verification": {"status": "incomplete"}}, "Checks incomplete"),
        ({"status": "Completed", "verification": {"status": "complete"}}, "Verified complete"),
        ({"status": "Unavailable", "error": "Actual prerequisite missing"}, "Status unavailable"),
        ({"status": "Running", "developer_decisions": [{"state": "Open"}]}, "Your decision needed"),
    ],
)
def test_guidance_states(execution, expected):
    assert task_guidance(execution, {})["status"] == expected


def test_optional_binding_preserves_existing_callers(controller, approved):
    result = start_execution(controller, approved, launch=False)
    assert result["created"]


def test_header_sidebar_agree_for_inactive_and_verified_runs(controller, approved, db_path):
    result = start_execution(controller, approved, launch=False)
    eid = result["execution"]["id"]
    for status, result_text, expected in (
        ("Running", None, "Work in progress"),
        ("Interrupted", None, "Work stopped · action needed"),
        ("Completed", None, "Checks incomplete"),
        ("Completed", json.dumps({"status": "APPROVED"}), "Verified complete"),
        ("Unavailable", None, "Status unavailable"),
    ):
        controller.conn.execute(
            "UPDATE executions SET status=?,result=? WHERE id=?", (status, result_text, eid)
        )
        view = dashboard_view(db_path, approved, None)
        assert view["task"]["guidance"] == view["tasks"][0]["guidance"]
        assert view["task"]["guidance"]["status"] == expected


def test_decision_ids_use_projected_blockers():
    view = {
        "status": "Interrupted",
        "developer_decisions": ["D1"],
        "blockers": [{"id": "D1", "state": "Open"}],
    }
    assert task_guidance(view, {})["status"] == "Your decision needed"
    view["blockers"][0]["state"] = "Resolved"
    assert task_guidance(view, {})["status"] == "Work stopped · action needed"


def test_start_missing_database_creates_nothing(controller, approved, db_path, tmp_path):
    binding = dashboard_view(db_path, approved, None)["task"]["launch"]["binding"]
    missing = tmp_path / "missing" / "state.db"
    with serve(missing, approved) as server:
        status, _ = post_start(
            server, {"task": approved, "host": "codex", "approval_binding": binding}
        )
    assert status == 404
    assert not missing.parent.exists()


def test_specification_progress_is_distinct_from_stale_approval(controller, db_path):
    tid = controller.create_task("Describe ordinary requirements", "trivial").id
    view = dashboard_view(db_path, tid, None)
    assert view["task"]["guidance"]["status"] == "Specification in progress"
    assert view["task"]["guidance"] == view["tasks"][0]["guidance"]
    assert (
        task_guidance(
            None,
            {"binding": None, "diagnostic": "Changed current approval"},
            phase="approval",
            has_approval=True,
        )["status"]
        == "Status unavailable"
    )


def test_actual_projected_decision_ids_render_header_and_list(controller, approved, db_path):
    from harness.execution_projection import ingest_event
    from tests.execution_helpers import event, started

    eid = start_execution(controller, approved, launch=False)["execution"]["id"]
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
                "title": "Fixture work",
                "status": "Idle",
                "dependencies": [],
                "files": ["fixture"],
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
                "id": "design1",
                "revision": 1,
                "tasks": ["A"],
                "kind": "developer_engineering_decision",
                "question": "Which format?",
                "context": "An approved scope choice remains.",
                "alternatives": ["Current", "Indexed"],
                "recommendation": "Current",
            },
        ),
    )
    with serve(db_path, approved) as server:
        view = api(server)
    assert view["task"]["execution"]["developer_decisions"] == ["design1"]
    assert view["task"]["guidance"]["status"] == "Your decision needed"
    assert view["task"]["guidance"] == view["tasks"][0]["guidance"]
