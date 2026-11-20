# ruff: noqa: F811
"""Concurrent immutable process parents and current-generation-only lifecycle callbacks."""

import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from harness.db import connect
from harness.execution_models import ExecutionConflict
from harness.reliability import store
from harness.reliability.process_context import dispatch_scope
from harness.reliability.process_store import pending_process
from tests.test_reliability_processes import processes, settled, spans, start_process  # noqa: F401
from tests.test_reliability_store import observed  # noqa: F401
from tests.test_template_classification_integration import operations, registered  # noqa: F401


def child_principal(operation, role="al-scout"):
    aid = str(uuid.uuid4())
    directory = operation.registry.native_root / "sessions/2026/11/19"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ("rollout-" + aid + ".jsonl")).write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": aid,
                    "session_id": aid,
                    "cli_version": "0.165.0",
                    "source": {
                        "subagent": {
                            "thread_spawn": {
                                "parent_thread_id": operation.registry.root,
                                "agent_role": role,
                            }
                        }
                    },
                },
            }
        )
        + "\n"
    )
    return operation.registry.enroll(aid)


def test_concurrent_call_scopes_keep_distinct_principals_and_parents(processes):
    operation, principal, controller = processes
    child = child_principal(operation)
    # Profiles are prepared serially; subprocesses and context scopes overlap in real threads.
    profiles = [operation.profile(()), operation.profile(())]

    def launch(index):
        owner = [principal, child][index]
        oid = store.begin(operation, owner, "run_command")
        with dispatch_scope(operation, oid, owner["thread_id"]):
            handle = operation.processes.start(
                owner["thread_id"],
                profiles[index],
                [sys.executable, "-c", "import time; time.sleep(.1)"],
                operation.envelope["repository"],
                task=None,
                revision=None,
            )
        return oid, handle, owner

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(launch, range(2)))
    for oid, handle, owner in results:
        settled((operation, owner, controller), handle)
        span = next(row for row in spans(processes) if row["parent_observation_id"] == oid)
        assert span["principal_id"] == owner["thread_id"]
    assert len({row["attempt_id"] for row in spans(processes)}) == 2


def test_generation_change_preserves_incomplete_span_and_refuses_old_publication(processes):
    operation, principal, controller = processes
    oid = store.begin(operation, principal, "run_command")
    with dispatch_scope(operation, oid, principal["thread_id"]):
        observer = pending_process(principal["thread_id"], None, None)
    controller.conn.execute(
        "UPDATE executions SET authority_generation=1 WHERE id=?", (operation.execution_id,)
    )
    with pytest.raises(ExecutionConflict, match="stale_epoch"):
        observer.publish("process.started")
    assert spans(processes)[0]["launch_state"] == "pending"
    assert spans(processes)[0]["duration_ms"] is None


@pytest.mark.skipif(sys.platform != "darwin", reason="genuine macOS active runtime transition")
def test_active_runtime_retirement_rejects_terminal_process_callback(
    registered, monkeypatch, tmp_path
):
    from harness.execution_runtime_recovery import activate_runtime, prepare_runtime
    from harness.execution_store import bind_host

    operation, _, controller, _, _, execution, _ = registered[0]
    from tests.test_execution_operations import observed_principal

    root = operation.registry.root
    principal = observed_principal(operation, root, "orchestrator")
    bind_host(controller.conn, execution["id"], root, "test-owner")
    monkeypatch.chdir(tmp_path)
    prepared = prepare_runtime(
        controller,
        execution["id"],
        expected_epoch_id="legacy-0",
        expected_authority_generation=0,
        request_id="timeline-retire",
        reason="Retire disposable fixture while its owned operation remains observed",
    )
    oid = store.begin(operation, principal, "run_command")
    with dispatch_scope(operation, oid, principal["thread_id"]):
        observer = pending_process(principal["thread_id"], None, None)
    observer.publish("process.started")
    activated = activate_runtime(
        controller,
        execution["id"],
        request_id="timeline-retire",
        preparation_sha256=prepared["preparation_sha256"],
        expected_epoch_id="legacy-0",
        expected_authority_generation=0,
    )
    assert activated["state"] == "ACTIVE"
    observer.notify("process.exited", returncode=0, duration_ms=100, cleanup_status="observed")
    assert not observer.available
    with closing(connect(operation.database)) as conn:
        span = conn.execute("SELECT * FROM reliability_process_spans").fetchone()
        assert span["terminal_state"] is None and span["epoch_id"] == "legacy-0"
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM reliability_timeline_links WHERE epoch_id=?",
                (activated["epoch_id"],),
            ).fetchone()[0]
            == 0
        )


def test_epoch_change_before_ingest_claim_cannot_write_successor_diagnostics(observed, monkeypatch):
    from contextlib import contextmanager

    from harness import execution_epoch_lock as locks
    from harness.execution_projection import ingest_event
    from harness.execution_store import get_execution
    from tests.execution_helpers import event, started

    operation, _, controller = observed
    eid = operation.execution_id
    started(controller.conn, eid)
    record = event(controller.conn, eid, 2, "run.ended", {"status": "RUNNING"})
    before = get_execution(controller.conn, eid)
    count = controller.conn.execute("SELECT COUNT(*) FROM execution_logs").fetchone()[0]
    original = locks.epoch_action_lock

    @contextmanager
    def transitioned(execution):
        monkeypatch.setattr(locks, "epoch_action_lock", original)
        controller.conn.execute("UPDATE executions SET authority_generation=1 WHERE id=?", (eid,))
        with original(execution):
            yield

    monkeypatch.setattr(locks, "epoch_action_lock", transitioned)
    with pytest.raises(ExecutionConflict, match="stale_epoch"):
        ingest_event(controller.conn, eid, record)
    after = get_execution(controller.conn, eid)
    assert after["cursor"] == before["cursor"] and after["error"] == before["error"]
    assert controller.conn.execute("SELECT COUNT(*) FROM execution_logs").fetchone()[0] == count
