"""Synthetic historical invalid requests; trusted CLI/backend recovery without AL mutation."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from harness.cli import build_parser
from harness.cli_execution import cmd_execution
from harness.controller import HarnessController
from harness.db import connect
from harness.execution_contract import canonical, digest
from harness.execution_decisions import (
    record_execution_decision,
    recover_decision_intent,
    retire_invalid_decision,
)
from harness.execution_handoff import read_handoff
from harness.execution_hosts import al_source
from harness.execution_projection import ingest_event
from harness.execution_store import entity, get_execution
from harness.execution_worker import worker_lock
from tests.execution_helpers import approved_execution, event, started

REASON = (
    "Retire this structurally invalid undelivered synthetic request; preserve all frozen evidence."
)


@pytest.fixture
def execution_fixture(controller, sample_repo, monkeypatch, tmp_path):
    tid, eid = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, eid)
    task = {
        "id": "A",
        "title": "Synthetic planning",
        "status": "Idle",
        "dependencies": [],
        "files": ["svc"],
        "criteria": ["AC-01"],
        "revision": 1,
    }
    blocker = {
        "id": "environment",
        "revision": 1,
        "tasks": ["A"],
        "affected": ["A"],
        "question": "Restore environment?",
        "context": "Synthetic environment recovery",
        "alternatives": ["repair", "wait"],
        "recommendation": "repair",
    }
    ingest_event(controller.conn, eid, event(controller.conn, eid, 2, "task.registered", task))
    ingest_event(controller.conn, eid, event(controller.conn, eid, 3, "blocker.raised", blocker))
    saved = get_execution(controller.conn, eid)
    source = al_source(saved, read_handoff(saved["handoff_path"]))
    source.parent.mkdir(parents=True, exist_ok=True)
    binding = saved["binding"] | {
        "handoff_hash": saved["handoff_hash"],
        "al_session_id": saved["al_session_id"],
        "al_run_id": saved["al_run_id"],
    }
    source.write_text(
        canonical(
            {
                "run_id": saved["al_run_id"],
                "session_id": saved["al_session_id"],
                "integration": {
                    "binding": binding,
                    "handoff": saved["handoff_path"],
                    "blockers": {"environment": blocker | {"state": "Open"}},
                    "deliveries": {},
                    "outbox": [],
                    "sequence": 0,
                },
                "amendments": [],
                "task_package": {"sha256": "synthetic-original-package"},
            }
        )
    )
    coordination = tmp_path / "canonical-source.lock"
    coordination.touch()
    # Only the protected-origin lookup is a fixture boundary. Source binding, history,
    # Registry verification, worker/decision locks, persistence and CLI remain real.
    monkeypatch.setattr("harness.execution_hosts.managed_launcher", lambda: "synthetic-launcher")
    monkeypatch.setattr(
        "harness.execution_initialization.validate_existing_origin",
        lambda *args: {
            "source": str(source),
            "pin_root": str(tmp_path / ("a" * 64)),
            "coordination_lock": str(coordination),
        },
    )
    monkeypatch.setattr("harness.cli_execution.reconnect_execution", lambda *args: None)
    return tid, eid, source


def request_values():
    return {
        "blocker_revision": 1,
        "request_id": "valid-request-" + uuid.uuid4().hex,
        "verdict": "alternative",
        "decision_text": (
            "The synthetic environment is verified; proceed through the existing guarded workflow."
        ),
        "tasks": ["A"],
        "criteria": [],
    }


@pytest.fixture
def invalid_ready(controller, execution_fixture, monkeypatch):
    import harness.execution_decisions as service

    tid, eid, source = execution_fixture

    def old_failure(*args):
        raise ValueError("synthetic historical delivery validation failure")

    with monkeypatch.context() as context:
        context.setattr(service, "deliver_decision", old_failure)
        with pytest.raises(ValueError):
            record_execution_decision(controller, eid, "environment", **request_values())
    row = dict(controller.conn.execute("SELECT * FROM execution_decisions").fetchone())
    request = json.loads(row["request"])
    request["criteria"] = [""]
    controller.conn.execute(
        "UPDATE execution_decisions SET request=?,error='invalid decision criteria' WHERE id=?",
        (canonical(request), row["id"]),
    )
    row = dict(controller.conn.execute("SELECT * FROM execution_decisions").fetchone())
    return tid, eid, source, row


def retire(controller, fixture, **changes):
    _, eid, _, row = fixture
    return retire_invalid_decision(
        controller,
        changes.get("execution", eid),
        row["id"],
        expected_request_digest=changes.get("digest", digest(row["request"].encode())),
        reason=changes.get("reason", REASON),
    )


def cli_args(db_path, tid, eid, criteria="", tasks="A"):
    return build_parser().parse_args(
        [
            "--db",
            str(db_path),
            "execution",
            "decision",
            tid,
            eid,
            "environment",
            "--revision",
            "1",
            "--request-id",
            "public-cli-" + uuid.uuid4().hex,
            "--verdict",
            "alternative",
            "--text",
            "The synthetic environment has been independently verified for guarded continuation.",
            "--tasks",
            tasks,
            "--criteria",
            criteria,
        ]
    )


def test_public_cli_exact_empty_criteria_reaches_delivery(
    controller, execution_fixture, db_path, capsys
):
    tid, eid, _ = execution_fixture
    assert cmd_execution(cli_args(db_path, tid, eid)) == 0
    row = dict(controller.conn.execute("SELECT * FROM execution_decisions").fetchone())
    assert row["state"] == "Delivered"
    assert json.loads(row["request"])["criteria"] == []
    assert json.loads(row["delivery"])["criteria"] == []
    assert json.loads(capsys.readouterr().out)["state"] == "Delivered"


@pytest.mark.parametrize(
    "criteria,tasks",
    [("AC-01,", "A"), (",AC-01", "A"), ("AC-01,,AC-02", "A"), (" ", "A"), ("", "A,"), ("", "")],
)
def test_malformed_csv_creates_nothing(
    controller, execution_fixture, db_path, sample_repo, criteria, tasks
):
    tid, eid, _ = execution_fixture
    before = list((sample_repo / "docs/decisions").glob("DEC-*.md"))
    with pytest.raises(ValueError):
        cmd_execution(cli_args(db_path, tid, eid, criteria, tasks))
    assert controller.conn.execute("SELECT count(*) FROM execution_decisions").fetchone()[0] == 0
    assert list((sample_repo / "docs/decisions").glob("DEC-*.md")) == before


@pytest.mark.parametrize("criteria", [[""], [" "], ["AC-01", "AC-01"], [1], None])
def test_structured_invalid_criteria_precedes_intent_and_registry(
    controller, execution_fixture, sample_repo, criteria
):
    _, eid, _ = execution_fixture
    before = list((sample_repo / "docs/decisions").glob("DEC-*.md"))
    with pytest.raises(ValueError):
        record_execution_decision(
            controller, eid, "environment", **(request_values() | {"criteria": criteria})
        )
    assert controller.conn.execute("SELECT count(*) FROM execution_decisions").fetchone()[0] == 0
    assert list((sample_repo / "docs/decisions").glob("DEC-*.md")) == before


def test_public_retirement_preserves_frozen_evidence_and_repeats(
    controller, invalid_ready, db_path, capsys
):
    tid, eid, source, before = invalid_ready
    source_bytes = source.read_bytes()
    registry_bytes = Path(before["registry_path"]).read_bytes()
    command = [
        "--db",
        str(db_path),
        "execution",
        "retire-invalid-decision",
        tid,
        eid,
        before["id"],
        "--request-sha256",
        digest(before["request"].encode()),
        "--reason",
        REASON,
    ]
    assert cmd_execution(build_parser().parse_args(command)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == retire(controller, invalid_ready)
    after = dict(controller.conn.execute("SELECT * FROM execution_decisions").fetchone())
    assert after == before | {"state": "Rejected", "updated_at": after["updated_at"]}
    assert source.read_bytes() == source_bytes
    assert Path(before["registry_path"]).read_bytes() == registry_bytes
    receipt = Path(result["receipt"]["path"]).read_bytes()
    assert digest(receipt) == result["receipt"]["sha256"]
    assert json.loads(receipt)["kind"] == "invalid-decision-retirement-v1"
    assert entity(controller.conn, eid, "blocker", "environment")["state"] == "Open"
    assert (
        sum(
            e.kind == "execution.decision.invalid_retired"
            for e in controller.workspace(tid).events()
        )
        == 1
    )
    with pytest.raises(ValueError):
        recover_decision_intent(controller, before["id"])
    # A new valid request goes through the ordinary Registry/delivery path.
    delivered = record_execution_decision(controller, eid, "environment", **request_values())
    assert delivered["state"] == "Delivered" and delivered["id"] != before["id"]
    assert Path(before["registry_path"]).read_bytes() == registry_bytes


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "foreign",
        "hash",
        "delivered",
        "payload",
        "inbox",
        "inbox-symlink",
        "ack",
        "persisted-ack",
        "resolved",
        "source-unreadable",
        "registry-changed",
    ],
)
def test_unsafe_retirements_are_refused(controller, invalid_ready, case):
    _, eid, source, row = invalid_ready
    changes = {}
    if case == "valid":
        request = json.loads(row["request"])
        request["criteria"] = []
        controller.conn.execute(
            "UPDATE execution_decisions SET request=? WHERE id=?", (canonical(request), row["id"])
        )
        changes["digest"] = digest(canonical(request).encode())
    elif case == "foreign":
        changes["execution"] = "foreign-execution"
    elif case == "hash":
        changes["digest"] = "sha256:" + "0" * 64
    elif case == "delivered":
        controller.conn.execute(
            "UPDATE execution_decisions SET state='Delivered' WHERE id=?", (row["id"],)
        )
    elif case == "payload":
        controller.conn.execute(
            "UPDATE execution_decisions SET delivery='{}' WHERE id=?", (row["id"],)
        )
    elif case.startswith("inbox"):
        path = (
            Path(get_execution(controller.conn, eid)["handoff_path"]).parent
            / "inbox"
            / (row["id"] + ".json")
        )
        path.parent.mkdir(exist_ok=True)
        if case == "inbox-symlink":
            path.symlink_to(path.parent / "missing")
        else:
            path.write_text("{}")
    elif case in {"ack", "resolved"}:
        state = json.loads(source.read_bytes())
        if case == "ack":
            state["integration"]["deliveries"][row["id"]] = {"state": "Resolved"}
        else:
            state["integration"]["blockers"]["environment"]["state"] = "Resolved"
        source.write_text(canonical(state))
    elif case == "persisted-ack":
        ack = event(
            controller.conn,
            eid,
            4,
            "decision.acknowledged",
            {
                "delivery_id": row["id"],
                "blocker_id": "environment",
                "state": "Resolved",
                "decision_hash": "sha256:" + "0" * 64,
            },
        )
        controller.conn.execute(
            "INSERT INTO execution_events(event_id,execution_id,sequence,kind,record) "
            "VALUES (?,?,?,?,?)",
            (ack["event_id"], eid, 4, ack["kind"], canonical(ack)),
        )
    elif case == "source-unreadable":
        source.unlink()
    else:
        Path(row["registry_path"]).write_text("changed synthetic Registry")
    with pytest.raises((ValueError, LookupError, OSError)):
        retire(controller, invalid_ready, **changes)
    assert (
        dict(controller.conn.execute("SELECT * FROM execution_decisions").fetchone())["state"]
        != "Rejected"
    )


def test_competing_worker_and_concurrent_retry_cannot_revive(controller, invalid_ready, db_path):
    _, eid, _, row = invalid_ready
    with (
        worker_lock(get_execution(controller.conn, eid)),
        pytest.raises(ValueError, match="live worker"),
    ):
        retire(controller, invalid_ready)

    def run(action):
        with closing(connect(db_path)) as conn:
            current = HarnessController(conn)
            if action == "retire":
                return retire(current, invalid_ready)
            try:
                recover_decision_intent(current, row["id"])
            except ValueError:
                return "refused"
            raise AssertionError("invalid retry unexpectedly delivered")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ["retry", "retire"]))
    assert results[0] == "refused" and results[1]["state"] == "Rejected"
    after = dict(controller.conn.execute("SELECT * FROM execution_decisions").fetchone())
    assert after["delivery"] is None and after["error"] == row["error"]


def test_orphan_receipt_is_not_retirement(controller, invalid_ready, monkeypatch):
    from harness.state import TaskWorkspace

    original = TaskWorkspace.record_event

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic retirement transaction failure")

    monkeypatch.setattr(TaskWorkspace, "record_event", fail)
    with pytest.raises(RuntimeError):
        retire(controller, invalid_ready)
    _, eid, _, row = invalid_ready
    assert (
        dict(controller.conn.execute("SELECT * FROM execution_decisions").fetchone())["state"]
        == "Ready"
    )
    assert list(
        (
            Path(get_execution(controller.conn, eid)["handoff_path"]).parent
            / "decision-retirements"
        ).glob("*.json")
    )
    monkeypatch.setattr(TaskWorkspace, "record_event", original)
    assert retire(controller, invalid_ready)["state"] == "Rejected"


def test_all_request_contract_checks_precede_persistence(controller, execution_fixture):
    _, eid, _ = execution_fixture
    for change in [
        {"verdict": "reject", "amendment": True},
        {"tasks": ["A", "A"]},
        {"tasks": ["../escape"]},
        {"tasks": ["foreign"]},
        {"blocker_revision": 2},
        {"decision_text": "yes"},
    ]:
        with pytest.raises(ValueError):
            record_execution_decision(controller, eid, "environment", **(request_values() | change))
    assert controller.conn.execute("SELECT count(*) FROM execution_decisions").fetchone()[0] == 0


def test_ambiguous_source_and_replay_changes_fail_closed(controller, invalid_ready):
    _, _, source, _ = invalid_ready
    before = source.read_bytes()
    source.write_text("[]")
    with pytest.raises(ValueError, match="ambiguous"):
        retire(controller, invalid_ready)
    source.write_bytes(before)
    result = retire(controller, invalid_ready)
    with pytest.raises(ValueError):
        retire(controller, invalid_ready, digest="sha256:" + "0" * 64)
    with pytest.raises(ValueError):
        retire(
            controller,
            invalid_ready,
            reason="Changed operator reason must not replace the immutable receipt.",
        )
    assert Path(result["receipt"]["path"]).is_file()


def test_retired_request_projection_has_no_false_acknowledgment(controller, invalid_ready):
    from harness.execution_projection import execution_view

    _, eid, _, row = invalid_ready
    retire(controller, invalid_ready)
    decision = execution_view(controller.conn, eid)["decisions"][0]
    assert decision["id"] == row["id"] and decision["state"] == "Rejected"
    assert decision["delivery_status"] == "not_delivered" and not decision["retryable"]
    assert "retired before delivery" in decision["outcome_notice"]
    assert "no AL acknowledgment" in decision["outcome_notice"]
    assert "delivery" not in decision


def test_actual_al_rejection_keeps_acknowledged_projection(controller, execution_fixture):
    from harness.execution_projection import execution_view

    _, eid, _ = execution_fixture
    delivered = record_execution_decision(controller, eid, "environment", **request_values())
    payload = json.loads(delivered["delivery"])
    acknowledgment = event(
        controller.conn,
        eid,
        4,
        "decision.acknowledged",
        {
            "delivery_id": delivered["id"],
            "blocker_id": "environment",
            "state": "Rejected",
            "decision_hash": digest(payload),
        },
    )
    ingest_event(controller.conn, eid, acknowledgment)
    decision = execution_view(controller.conn, eid)["decisions"][0]
    assert decision["state"] == "Rejected" and decision["delivery_status"] == "acknowledged"
    assert (
        not decision["retryable"] and "Recorded AL decision outcome" in decision["outcome_notice"]
    )
    assert "delivery" not in decision
    assert delivered["delivery"] not in canonical(decision)
