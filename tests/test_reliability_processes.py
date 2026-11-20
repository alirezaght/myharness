# ruff: noqa: F811
"""Real contained subprocesses, immutable parents, typed outcomes and publication faults."""

import hashlib
import json
import sys
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.command_safety import safe_environment
from harness.db import connect
from harness.execution_isolation import prepare_operation_isolation
from harness.execution_processes import ProcessManager, bounded_capture
from harness.reliability import store
from harness.reliability.process_context import CURRENT, dispatch_scope
from tests.native_model_fixture import operation_sandbox_unavailable
from tests.test_reliability_store import observed  # noqa: F401


@pytest.fixture
def processes(observed, tmp_path):
    if operation_sandbox_unavailable():
        pytest.skip("operation sandbox capability unavailable")
    operation, principal, controller = observed
    manager = ProcessManager()
    operation.processes = manager
    operation.setup_outcomes, operation.setup_pending = {}, {}
    operation.tooling = SimpleNamespace(
        environment=lambda cwd, temporary: (
            safe_environment()
            | {
                "TMPDIR": temporary,
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
    )
    root = Path(operation.envelope["repository"])
    counter = iter(range(1000))
    operation.profile = lambda writable: prepare_operation_isolation(
        root,
        writable=writable,
        protected=[operation.database],
        hidden=[],
        directory=tmp_path / f"sandbox-{next(counter)}",
    )
    yield operation, principal, controller
    manager.close()


def start_process(processes, code="print('done')", *, timeout=5):
    operation, principal, _ = processes
    oid = store.begin(operation, principal, "run_command")
    with dispatch_scope(operation, oid, principal["thread_id"]):
        result = operation.processes.start(
            principal["thread_id"],
            operation.profile(()),
            [sys.executable, "-c", code],
            operation.envelope["repository"],
            task=None,
            revision=None,
            timeout=timeout,
        )
    store.finish(operation, oid, "RETURNED", result)
    return oid, result


def settled(processes, result):
    operation, principal, _ = processes
    record = operation.processes._owned(principal["thread_id"], result["handle"])
    record["thread"].join(10)
    assert not record["thread"].is_alive()
    return operation.processes.poll(principal["thread_id"], result["handle"])


def spans(processes):
    with closing(connect(processes[0].database)) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM reliability_process_spans")]


def test_real_public_dispatch_correlates_process_and_preserves_redacted_result(processes):
    operation, principal, _ = processes
    result = operation.dispatch(
        principal,
        "run_command",
        {
            "argv": ["git", "status", "--short"],
            "cwd": ".",
            "task_id": None,
            "expected_revision": "private-untrusted-revision",
            "timeout": 5,
        },
    )
    outcome = settled(processes, result)
    assert outcome["returncode"] == 0
    span = spans(processes)[0]
    assert span["launch_state"] == "started" and span["terminal_state"] == "exited"
    assert span["revision"] is None and span["duration_ms"] >= 0
    assert span["principal_id"] == principal["thread_id"]
    assert CURRENT.get() is None
    with closing(connect(operation.database)) as conn:
        parent = conn.execute(
            "SELECT * FROM reliability_observations WHERE observation_id=?",
            (span["parent_observation_id"],),
        ).fetchone()
        assert parent["tool"] == "run_command" and parent["state"] == "succeeded"
        kinds = {r[0] for r in conn.execute("SELECT event_kind FROM reliability_timeline_links")}
        assert {"process.launch_pending", "process.started", "process.exited"} <= kinds


@pytest.mark.parametrize("mode", ["nonzero", "cancel", "timeout"])
def test_nonzero_cancel_and_timeout_are_distinct(processes, mode):
    operation, principal, _ = processes
    code = "import sys; sys.exit(7)" if mode == "nonzero" else "import time; time.sleep(10)"
    _, result = start_process(processes, code, timeout=1 if mode == "timeout" else 5)
    if mode == "cancel":
        operation.processes.cancel(principal["thread_id"], result["handle"])
    settled(processes, result)
    span = spans(processes)[0]
    assert (
        span["stop_reason"]
        == {
            "nonzero": None,
            "cancel": "cancel_requested",
            "timeout": "deadline_exceeded",
        }[mode]
    )
    if mode == "nonzero":
        assert span["returncode"] == 7
    assert span["cleanup_status"] == "observed" and span["duration_ms"] is not None


def test_initial_recording_failure_prevents_spawn(processes, monkeypatch):
    operation, principal, controller = processes
    controller.conn.execute(
        "CREATE TRIGGER reject_span BEFORE INSERT ON reliability_process_spans "
        "BEGIN SELECT RAISE(ABORT,'private-trigger'); END"
    )
    effects = []
    monkeypatch.setattr(
        "harness.execution_processes.operation_popen", lambda *a, **k: effects.append(1)
    )
    with pytest.raises(ValueError, match="recording unavailable"):
        start_process(processes)
    assert not effects and not spans(processes) and operation.processes.launching == 0


@pytest.mark.parametrize("boundary", ["started", "terminal"])
def test_recording_failure_after_spawn_preserves_owned_process_without_replay(processes, boundary):
    operation, _, controller = processes
    condition = (
        "NEW.launch_state='started'" if boundary == "started" else "NEW.terminal_state IS NOT NULL"
    )
    controller.conn.execute(
        "CREATE TRIGGER reject_publication BEFORE UPDATE ON "
        "reliability_process_spans WHEN "
        + condition
        + " BEGIN SELECT RAISE(ABORT,'private-fault'); END"
    )
    _, result = start_process(processes, "print('one-effect')")
    outcome = settled(processes, result)
    assert "one-effect" in outcome["output"] and outcome["telemetry_status"] == "unavailable"
    assert len(operation.processes.records) == 1
    assert spans(processes)[0]["terminal_state"] is None
    assert spans(processes)[0]["duration_ms"] is None


def test_process_callback_runs_outside_manager_lock(processes, monkeypatch):
    from harness.reliability.process_store import ProcessObserver

    operation, _, _ = processes
    original = ProcessObserver.publish
    calls = []

    def publish(observer, *a, **k):
        assert not operation.processes.lock._is_owned()
        calls.append(a[0])
        return original(observer, *a, **k)

    monkeypatch.setattr(ProcessObserver, "publish", publish)
    _, result = start_process(processes)
    settled(processes, result)
    assert "process.started" in calls and "process.exited" in calls


def test_private_command_output_and_hashes_are_absent_from_new_facts(processes):
    secret = "private-r2-command-output-7139"
    _, result = start_process(processes, f"print({secret!r})")
    assert secret in settled(processes, result)["output"]
    with closing(connect(processes[0].database)) as conn:
        facts = json.dumps(
            {
                table: [dict(row) for row in conn.execute("SELECT * FROM " + table)]
                for table in (
                    "reliability_traces",
                    "reliability_process_spans",
                    "reliability_timeline_links",
                )
            }
        )
    assert secret not in facts and hashlib.sha256(secret.encode()).hexdigest() not in facts


def test_synchronous_bounded_capture_uses_same_parent_and_typed_timeout(processes):
    import subprocess

    from harness.execution_isolation import operation_popen
    from harness.reliability.process_store import pending_process

    operation, principal, _ = processes
    oid = store.begin(operation, principal, "al_command")
    with dispatch_scope(operation, oid, principal["thread_id"]):
        observer = pending_process(principal["thread_id"], None, None)
    profile = operation.profile(())
    before = time.monotonic()
    proc = operation_popen(
        profile,
        [sys.executable, "-c", "import time; time.sleep(5)"],
        cwd=operation.envelope["repository"],
        env=safe_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    observer.notify("process.started")
    with pytest.raises(ValueError, match="deadline"):
        bounded_capture(proc, timeout=0.1, lifecycle=observer, monotonic_start=before)
    assert spans(processes)[0]["stop_reason"] == "deadline_exceeded"


def test_spawn_failure_is_not_started_and_never_records_private_exception(processes, monkeypatch):
    def refuse(*a, **k):
        raise OSError("private-r2-spawn-error-1538")

    monkeypatch.setattr("harness.execution_processes.operation_popen", refuse)
    with pytest.raises(OSError, match="private-r2-spawn-error"):
        start_process(processes)
    span = spans(processes)[0]
    assert span["launch_state"] == "not_started" and span["terminal_state"] == "observation_failed"
    assert "private-r2" not in str(span) and span["stop_reason"] == "io_error"


def test_cleanup_failure_is_explicit_and_exit_is_not_cleanup_proof(processes, monkeypatch):
    operation, _, _ = processes
    original = operation.processes._kill

    def uncertain(proc):
        original(proc)
        raise OSError("private-r2-cleanup-error-3185")

    monkeypatch.setattr(operation.processes, "_kill", uncertain)
    _, result = start_process(processes)
    settled(processes, result)
    span = spans(processes)[0]
    assert span["cleanup_status"] == "unconfirmed"
    assert "private-r2-cleanup" not in str(span)
    with closing(connect(operation.database)) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM reliability_timeline_links WHERE "
                "event_kind='process.cleanup_unconfirmed'"
            ).fetchone()[0]
            == 1
        )


def test_terminal_snapshot_is_idempotent_and_conflicts_refuse(processes):
    _, result = start_process(processes)
    settled(processes, result)
    operation, principal, _ = processes
    observer = operation.processes._owned(principal["thread_id"], result["handle"])["observer"]
    span = spans(processes)[0]
    snapshot = {
        key: span[key]
        for key in (
            "returncode",
            "duration_ms",
            "stop_reason",
            "cleanup_status",
        )
    }
    observer.publish("process.exited", **snapshot)
    with pytest.raises(ValueError, match="conflicting process terminal"):
        observer.publish("process.exited", **(snapshot | {"returncode": 9}))
    assert spans(processes)[0]["returncode"] == span["returncode"]


def test_internal_stream_failure_remains_typed_and_cleans_up(processes):
    operation, principal, _ = processes
    secret = "private-r2-stream-exception-9314"

    class Report:
        closed = False

        def consume(self, stream, data):
            if data:
                raise RuntimeError(secret)

        def close(self):
            self.closed = True

    report = Report()
    oid = store.begin(operation, principal, "run_command")
    with dispatch_scope(operation, oid, principal["thread_id"]):
        result = operation.processes.start(
            principal["thread_id"],
            operation.profile(()),
            [sys.executable, "-c", "print('stream')"],
            operation.envelope["repository"],
            task=None,
            revision=None,
            report=report,
        )
    outcome = settled(processes, result)
    assert outcome["status"] == "Failed" and report.closed
    span = spans(processes)[0]
    assert span["stop_reason"] == "internal_error" and span["cleanup_status"] == "observed"
    assert secret not in str(span) and hashlib.sha256(secret.encode()).hexdigest() not in str(span)
