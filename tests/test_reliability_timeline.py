# ruff: noqa: F811
"""Trace reservation atomicity and validated semantic source references."""

import copy

import pytest

from harness.execution_projection import ingest_event
from harness.execution_store import get_execution, reserve_execution
from tests.execution_helpers import event, started
from tests.test_execution_operations import operations  # noqa: F401
from tests.test_reliability_store import observed  # noqa: F401


def test_reservation_allocates_trace_before_launch_and_duplicate_keeps_it(observed):
    operation, _, controller = observed
    execution = get_execution(controller.conn, operation.execution_id)
    trace = controller.conn.execute("SELECT * FROM reliability_traces").fetchone()
    assert trace["execution_id"] == execution["id"]
    _, created = reserve_execution(
        controller.conn,
        execution_id=execution["id"],
        task_id=execution["task_id"],
        approval_id=execution["approval_id"],
        host=execution["host"],
        binding=execution["binding"],
        handoff_path=execution["handoff_path"],
        handoff_hash=execution["handoff_hash"],
        al_session_id=execution["al_session_id"],
    )
    assert not created
    assert (
        controller.conn.execute("SELECT trace_id FROM reliability_traces").fetchone()[0]
        == trace["trace_id"]
    )


def test_reservation_rollback_removes_execution_and_trace(controller, sample_repo, monkeypatch):
    from tests.execution_helpers import approved_execution

    # The real reservation is inside start_execution's transaction. Abort its trace insert.
    controller.conn.execute(
        "CREATE TRIGGER reject_trace BEFORE INSERT ON reliability_traces "
        "BEGIN SELECT RAISE(ABORT,'trace-fault'); END"
    )
    with pytest.raises(Exception, match="trace-fault"):
        approved_execution(controller, sample_repo, monkeypatch, legacy=True)
    assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0
    assert controller.conn.execute("SELECT COUNT(*) FROM reliability_traces").fetchone()[0] == 0


def test_validated_semantic_links_are_atomic_replay_safe_and_provenance_typed(observed):
    operation, _, controller = observed
    eid = operation.execution_id
    started(controller.conn, eid)
    payload = {
        "id": "task-safe",
        "title": "private-semantic-title-821",
        "status": "Idle",
        "dependencies": [],
        "files": ["svc"],
        "criteria": ["AC-01"],
        "revision": 1,
    }
    record = event(controller.conn, eid, 2, "task.registered", payload)
    assert ingest_event(controller.conn, eid, record)
    assert not ingest_event(controller.conn, eid, record)
    link = controller.conn.execute(
        "SELECT * FROM reliability_timeline_links WHERE source_id=?", (record["event_id"],)
    ).fetchone()
    assert link["provenance"] == "validated_semantic_report" and link["reference_id"] == "task-safe"
    assert "private-semantic" not in str(dict(link))
    changed = copy.deepcopy(record)
    changed["payload"]["title"] = "changed"
    with pytest.raises(ValueError, match="duplicate"):
        ingest_event(controller.conn, eid, changed)
    controller.conn.execute(
        "CREATE TRIGGER reject_link BEFORE INSERT ON reliability_timeline_links "
        "BEGIN SELECT RAISE(ABORT,'link-fault'); END"
    )
    with pytest.raises(Exception, match="link-fault"):
        ingest_event(
            controller.conn, eid, event(controller.conn, eid, 3, "run.ended", {"status": "RUNNING"})
        )
    assert get_execution(controller.conn, eid)["cursor"] == 2
    assert controller.conn.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0] == 2


def test_actual_authorized_query_links_only_returned_bundle_receipt(observed):
    import subprocess
    from pathlib import Path

    from harness.knowledge.policy import operator_scope
    from harness.knowledge.refresh import refresh
    from tests.test_reliability_timeline_fencing import child_principal

    operation, _, controller = observed
    principal = child_principal(operation)
    root = Path(operation.envelope["repository"])
    (root / "context.py").write_text("import private_context_canary\n")
    subprocess.run(["git", "-C", str(root), "add", "context.py"], check=True)
    refresh(controller.conn, operator_scope(root))
    operation.state = lambda execution: {
        "run_id": execution["al_run_id"],
        "phase": "PLANNING",
        "integration": {"agents": {}},
    }
    result = operation.dispatch(principal, "knowledge_query", {"query": "private_context_canary"})
    assert result["assertions"]
    row = controller.conn.execute(
        "SELECT * FROM reliability_timeline_links WHERE event_kind='retrieval.bundle'"
    ).fetchone()
    assert row["bundle_hash"] == result["bundle_hash"] and row["receipt_id"]
    assert "private_context_canary" not in str(dict(row))
    assert row["principal_id"] == principal["thread_id"]
    operation.dispatch(
        principal,
        "knowledge_explain",
        {
            "bundle_hash": result["bundle_hash"],
            "assertion_id": result["assertions"][0]["assertion_id"],
        },
    )
    assert (
        controller.conn.execute(
            "SELECT COUNT(*) FROM reliability_timeline_links WHERE event_kind='retrieval.bundle'"
        ).fetchone()[0]
        == 2
    )


def test_trace_survives_launch_failure_and_recorded_cancellation(observed, monkeypatch):
    from harness.execution import start_execution

    operation, _, controller = observed
    old = controller.conn.execute("SELECT trace_id FROM reliability_traces").fetchone()[0]
    monkeypatch.setattr("harness.launch_readiness.require_launch_ready", lambda *a: None)

    def refuse(*a, **k):
        raise OSError("disposable fixture launch unavailable")

    monkeypatch.setattr("harness.execution_worker.launch_worker", refuse)
    with pytest.raises(OSError, match="launch unavailable"):
        start_execution(controller, operation.envelope["task_id"], host="codex", launch=True)
    controller.conn.execute(
        "UPDATE executions SET status='Interrupted' WHERE id=?", (operation.execution_id,)
    )
    assert controller.conn.execute("SELECT trace_id FROM reliability_traces").fetchone()[0] == old


def test_actual_assignment_result_links_issued_receipt_without_content(observed):
    import subprocess
    from pathlib import Path

    from harness.knowledge.integration import assignment_context
    from harness.knowledge.policy import operator_scope
    from harness.knowledge.refresh import refresh
    from tests.test_reliability_timeline_fencing import child_principal

    operation, _, controller = observed
    principal = child_principal(operation, "al-engineer")
    root = Path(operation.envelope["repository"])
    (root / "assignment.py").write_text("import assignment_private_canary\n")
    subprocess.run(["git", "-C", str(root), "add", "assignment.py"], check=True)
    refresh(controller.conn, operator_scope(root))
    operation.assignments = {principal["thread_id"]: {"task": "assigned"}}
    operation.state = lambda execution: {
        "run_id": execution["al_run_id"],
        "phase": "IMPLEMENTATION",
        "integration": {
            "agents": {},
            "tasks": {"assigned": {"title": "assignment_private_canary"}},
        },
    }
    result = assignment_context(operation, controller, principal)
    assert result["status"] == "available"
    row = controller.conn.execute(
        "SELECT * FROM reliability_timeline_links WHERE event_kind='retrieval.bundle'"
    ).fetchone()
    assert row["receipt_id"] == result["receipt_id"] and row["bundle_hash"] == result["bundle_hash"]
    assert "assignment_private_canary" not in str(dict(row))

    from urllib.parse import urlencode

    from harness.dashboard.reliability_view import read_timeline

    status, data = read_timeline(
        operation.database,
        urlencode(
            {
                "task": operation.envelope["task_id"],
                "execution": operation.execution_id,
                "epoch": operation.epoch_id,
                "principal": principal["thread_id"],
            }
        ),
    )
    assert status == 200 and data["items"][0]["receipt_id"] == result["receipt_id"]


def test_real_foreground_al_dispatch_links_its_process_without_changing_result(operations):

    operation, _, controller, _, _, _, _ = operations
    from tests.test_execution_operations import observed_principal

    root = operation.registry.root
    principal = observed_principal(operation, root, "orchestrator")
    result = operation.dispatch(principal, "al_command", {"arguments": ["show"]})
    assert result["returncode"] == 0 and "stdout" in result
    span = controller.conn.execute("SELECT * FROM reliability_process_spans").fetchone()
    assert span["launch_state"] == "started" and span["returncode"] == 0
    assert span["cleanup_status"] == "observed" and span["duration_ms"] >= 0
    parent = controller.conn.execute(
        "SELECT * FROM reliability_observations WHERE observation_id=?",
        (span["parent_observation_id"],),
    ).fetchone()
    assert parent["tool"] == "al_command" and parent["state"] == "succeeded"


SEMANTIC_PRIVATE_TEXT = "Authorization: Bearer r2-semantic-private-8641"


def semantic_privacy_values():
    import hashlib

    raw_hash = hashlib.sha256(SEMANTIC_PRIVATE_TEXT.encode()).hexdigest()
    return [
        SEMANTIC_PRIVATE_TEXT,
        raw_hash,
        "sha256:" + raw_hash,
        {"private": SEMANTIC_PRIVATE_TEXT},
        [SEMANTIC_PRIVATE_TEXT],
    ]


def semantic_privacy_event(observed, kind, extra):
    import json

    from harness.db import utcnow
    from harness.execution_contract import canonical, digest

    operation, _, controller = observed
    conn, eid = controller.conn, operation.execution_id
    started(conn, eid)
    if kind in {"run.started", "run.ended"}:
        return event(conn, eid, 2, kind, {"status": "RUNNING", **extra}), None
    task = {
        "id": "safe-task",
        "title": "Safe task",
        "status": "Idle",
        "dependencies": [],
        "files": ["svc"],
        "criteria": ["AC-01"],
        "revision": 1,
    }
    ingest_event(conn, eid, event(conn, eid, 2, "task.registered", task))
    if kind == "task.updated":
        return event(
            conn, eid, 3, kind, {"id": "safe-task", "status": "Running", "revision": 1, **extra}
        ), "safe-task"
    if kind == "output.invalidated":
        return event(
            conn, eid, 3, kind, {"task_id": "safe-task", "reason": "Fixture invalidation", **extra}
        ), "safe-task"
    if kind == "test.result":
        return event(
            conn,
            eid,
            3,
            kind,
            {
                "id": "safe-check",
                "area": "safe-area",
                "status": "PASS",
                "scope": ["svc"],
                "validity": "VALID",
                **extra,
            },
        ), "safe-check"
    assert kind == "decision.acknowledged"
    blocker = {
        "id": "safe-blocker",
        "revision": 1,
        "tasks": ["safe-task"],
        "question": "Choose fixture option?",
        "context": "Disposable decision fixture",
        "alternatives": ["a", "b"],
        "recommendation": "a",
    }
    ingest_event(conn, eid, event(conn, eid, 3, "blocker.raised", blocker))
    delivery = {"fixture": "already persisted decision delivery"}
    now = utcnow()
    # Seed the authoritative delivered row required by the real acknowledgement projection.
    conn.execute(
        "INSERT INTO execution_decisions "
        "(id,execution_id,epoch_id,blocker_id,blocker_revision,request_id,request,state,"
        "delivery,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "safe-delivery",
            eid,
            operation.epoch_id,
            "safe-blocker",
            1,
            "fixture-request",
            "{}",
            "Delivered",
            canonical(delivery),
            now,
            now,
        ),
    )
    assert (
        json.loads(conn.execute("SELECT delivery FROM execution_decisions").fetchone()[0])
        == delivery
    )
    return event(
        conn,
        eid,
        4,
        kind,
        {
            "delivery_id": "safe-delivery",
            "blocker_id": "safe-blocker",
            "state": "Rejected",
            "decision_hash": digest(delivery),
            **extra,
        },
    ), "safe-delivery"


def semantic_privacy_extra(kind, value):
    if kind in {"task.updated", "test.result"}:
        return {"delivery_id": value, "task_id": value}
    if kind == "output.invalidated":
        return {"id": value, "delivery_id": value}
    if kind == "decision.acknowledged":
        return {"id": value, "task_id": value}
    return {"id": value, "delivery_id": value, "task_id": value}


@pytest.mark.parametrize(
    "kind",
    [
        "run.started",
        "run.ended",
        "task.updated",
        "output.invalidated",
        "test.result",
        "decision.acknowledged",
    ],
)
@pytest.mark.parametrize("extra_value", semantic_privacy_values())
def test_semantic_privacy_only_validated_kind_reference_is_projected(observed, kind, extra_value):
    import hashlib
    import json

    record, expected = semantic_privacy_event(
        observed, kind, semantic_privacy_extra(kind, extra_value)
    )
    operation, _, controller = observed
    assert ingest_event(controller.conn, operation.execution_id, record)
    row = controller.conn.execute(
        "SELECT * FROM reliability_timeline_links WHERE source_id=?", (record["event_id"],)
    ).fetchone()
    assert row["reference_id"] == expected
    safe = json.dumps(dict(row))
    raw_hash = hashlib.sha256(SEMANTIC_PRIVATE_TEXT.encode()).hexdigest()
    assert SEMANTIC_PRIVATE_TEXT not in safe and raw_hash not in safe
    original = controller.conn.execute(
        "SELECT record FROM execution_events WHERE event_id=?", (record["event_id"],)
    ).fetchone()[0]
    assert json.loads(original)["payload"] == record["payload"]
    assert not ingest_event(controller.conn, operation.execution_id, record)
    changed = copy.deepcopy(record)
    changed["payload"]["legacy_extra"] = "different"
    with pytest.raises(ValueError, match="duplicate"):
        ingest_event(controller.conn, operation.execution_id, changed)
