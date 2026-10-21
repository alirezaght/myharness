"""Real approval and SQLite setup shared by focused execution tests."""

import time

from harness.approval import ApprovalGate
from harness.execution import start_execution
from harness.execution_store import get_execution
from harness.spec_store import SpecStore
from tests.complete_task import settle


def approved_execution(controller, sample_repo, monkeypatch, *, host="claude"):
    monkeypatch.chdir(sample_repo)
    tid = controller.create_task("Implement a safe and verifiable fixture change", "trivial").id
    settle(controller, tid)
    controller.advance(tid)
    ApprovalGate(
        controller, tid, SpecStore(sample_repo / "docs/specs" / tid), sample_repo
    ).approve()
    result = start_execution(controller, tid, host=host, launch=False)
    return tid, result["execution"]["id"]


def binding(conn, eid, *, run="run123"):
    execution = get_execution(conn, eid)
    return execution["binding"] | {
        "handoff_hash": execution["handoff_hash"],
        "al_session_id": execution["al_session_id"],
        "al_run_id": run,
    }


def event(conn, eid, seq, kind, payload):
    return {
        "schema_version": 1,
        "event_id": f"event-{seq}",
        "sequence": seq,
        "timestamp": time.time(),
        "binding": binding(conn, eid),
        "kind": kind,
        "payload": payload,
    }


def claimed(conn, eid):
    conn.execute("UPDATE executions SET worker_token='test-owner' WHERE id=?", (eid,))


def started(conn, eid):
    from harness.execution_projection import ingest_event

    claimed(conn, eid)
    ingest_event(conn, eid, event(conn, eid, 1, "run.started", {"status": "RUNNING"}))
