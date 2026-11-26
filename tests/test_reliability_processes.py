# ruff: noqa: F811
"""Real contained subprocesses, immutable parents, typed outcomes and publication faults."""

import errno
import hashlib
import json
import os
import signal
import subprocess
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
from tests.test_reliability_store import install_statement_fault, observed  # noqa: F401


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
    operation, principal, _ = processes
    hits = install_statement_fault(
        monkeypatch,
        store,
        lambda sql, _parameters: (
            "insert into reliability_process_spans" in " ".join(sql.lower().split())
        ),
    )
    effects = []
    monkeypatch.setattr(
        "harness.execution_processes.operation_popen", lambda *a, **k: effects.append(1)
    )
    with pytest.raises(ValueError, match="recording unavailable"):
        start_process(processes)
    assert len(hits) == 1
    assert not effects and not spans(processes) and operation.processes.launching == 0


@pytest.mark.parametrize("boundary", ["started", "terminal"])
def test_recording_failure_after_spawn_preserves_owned_process_without_replay(
    processes, boundary, monkeypatch
):
    operation, _, _ = processes
    def intended(sql, _parameters):
        normalized = " ".join(sql.lower().split())
        return (
            "update reliability_process_spans set launch_state='started'" in normalized
            if boundary == "started"
            else "update reliability_process_spans set terminal_state=?" in normalized
        )

    hits = install_statement_fault(monkeypatch, store, intended)
    _, result = start_process(processes, "print('one-effect')")
    outcome = settled(processes, result)
    assert len(hits) == 1
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


@pytest.fixture
def exited_child():
    with subprocess.Popen(
        [sys.executable, "-c", "pass"],
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:
        assert proc.wait(timeout=3) == 0
        yield proc


def deny_group_signal(monkeypatch, proc, code=errno.EPERM):
    original = os.killpg
    calls = []

    def denied(group, sig):
        if group == proc.pid:
            calls.append((group, sig))
            raise PermissionError(code, "fixture group signal refused")
        return original(group, sig)

    monkeypatch.setattr(os, "killpg", denied)
    return calls


def test_denied_signal_settles_actual_completed_child_and_empty_group(exited_child, monkeypatch):
    calls = deny_group_signal(monkeypatch, exited_child)
    ProcessManager._kill(exited_child)
    assert exited_child.returncode == 0
    assert calls == [(exited_child.pid, signal.SIGKILL)]


def test_dead_leader_does_not_prove_its_live_descendant_settled(monkeypatch):
    from harness.execution_processes import _group_has_no_live_members

    original_kill = os.killpg
    with subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess,sys; "
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(3)'],"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);print(p.pid)",
        ],
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:
        try:
            output, _ = proc.communicate(timeout=3)
            assert int(output.strip()) != proc.pid and proc.returncode == 0
            assert not _group_has_no_live_members(proc.pid)
            deny_group_signal(monkeypatch, proc)
            with pytest.raises(PermissionError) as refused:
                ProcessManager._kill(proc)
            assert refused.value.errno == errno.EPERM
        finally:
            try:
                original_kill(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # The bounded child also exits naturally if the host denies teardown signals.
                pass
            deadline = time.monotonic() + 4
            while not _group_has_no_live_members(proc.pid) and time.monotonic() < deadline:
                time.sleep(0.02)
            assert _group_has_no_live_members(proc.pid)


@pytest.mark.parametrize(
    "failure",
    [
        "denied",
        "timeout",
        "exit",
        "empty",
        "malformed",
        "status",
        "duplicate",
        "oversized",
        "rows",
        "stderr",
        "unterminated",
        "large-pid",
        "large-group",
        "pid-range",
        "blank-row",
    ],
)
def test_denied_signal_requires_complete_known_group_observation(
    exited_child, monkeypatch, failure
):
    deny_group_signal(monkeypatch, exited_child)

    def observation():
        if failure == "denied":
            raise PermissionError(errno.EPERM, "fixture inspection refused")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("ps", 1)
        if failure == "exit":
            raise subprocess.CalledProcessError(1, "ps")
        output = {
            "empty": b"",
            "malformed": b"not numeric\n",
            "status": b"1 1 ?\n",
            "duplicate": b"1 1 S\n1 1 S\n",
            "oversized": b"1" * 1048577,
            "rows": b"1 1 S\n" * 10001,
            "stderr": None,
            "unterminated": b"1 1 S",
            "large-pid": b"9" * 5000 + b" 1 S\n",
            "large-group": b"1 " + b"9" * 5000 + b" S\n",
            "pid-range": b"2147483648 1 S\n",
            "blank-row": b"1 1 S\n\n",
        }[failure]
        return output

    monkeypatch.setattr("harness.execution_processes._capture_census", observation)
    with pytest.raises(PermissionError) as refused:
        ProcessManager._kill(exited_child)
    assert refused.value.errno == errno.EPERM


@pytest.mark.parametrize("failure", ["stdout-overflow", "stderr", "exit", "timeout"])
def test_actual_inspection_refusal_reaps_child_and_bounds_reads(exited_child, monkeypatch, failure):
    import harness.execution_processes as execution_processes

    code = {
        "stdout-overflow": "import os,time;os.write(1,b'1 1 S\\n'*262144);time.sleep(5)",
        "stderr": "import os,time;os.write(2,b'private-inspection-canary'*65536);time.sleep(5)",
        "exit": "import sys;print('1 1 S');sys.exit(7)",
        "timeout": "import time;print('1 1 S',flush=True);time.sleep(5)",
    }[failure]
    command = (sys.executable, "-c", code)
    monkeypatch.setattr(execution_processes, "_CENSUS_ARGV", command)
    inspections, consumed = [], []
    initialize, read = subprocess.Popen.__init__, os.read

    def launched(proc, argv, *args, **kwargs):
        initialize(proc, argv, *args, **kwargs)
        if argv == command:
            inspections.append(proc)

    def measured(fd, amount):
        raw = read(fd, amount)
        if inspections and fd in {
            stream.fileno()
            for stream in (inspections[0].stdout, inspections[0].stderr)
            if not stream.closed
        }:
            consumed.append(len(raw))
        return raw

    monkeypatch.setattr(subprocess.Popen, "__init__", launched)
    monkeypatch.setattr(os, "read", measured)
    calls = deny_group_signal(monkeypatch, exited_child)
    try:
        with pytest.raises(PermissionError) as refused:
            ProcessManager._kill(exited_child)
        assert refused.value.errno == errno.EPERM
        assert len(inspections) == 1
        child = inspections[0]
        assert child.returncode is not None and child.wait(timeout=0.1) == child.returncode
        assert child.stdout.closed and child.stderr.closed
        assert sum(consumed) <= 1048577
        if failure == "stdout-overflow":
            assert sum(consumed) == 1048577
        assert calls == [(exited_child.pid, signal.SIGKILL)]
    finally:
        for child in inspections:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)


def test_passive_cleanup_requires_owned_completion_and_preserves_other_denials(
    exited_child, monkeypatch
):
    deny_group_signal(monkeypatch, exited_child, errno.EACCES)
    with pytest.raises(PermissionError) as refused:
        ProcessManager._kill(exited_child)
    assert refused.value.errno == errno.EACCES
    deny_group_signal(monkeypatch, exited_child)
    with pytest.raises(PermissionError):
        ProcessManager._kill(SimpleNamespace(pid=exited_child.pid))

    def incomplete(**kwargs):
        raise subprocess.TimeoutExpired("owned child", kwargs["timeout"])

    with monkeypatch.context() as patch:
        patch.setattr(exited_child, "wait", incomplete)
        with pytest.raises(PermissionError):
            ProcessManager._kill(exited_child)


def test_missing_group_preserves_ordinary_lookup_behavior(exited_child, monkeypatch):
    def absent(*args):
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", absent)
    ProcessManager._kill(exited_child)


def test_bounded_capture_preserves_report_failure_after_passive_settlement(monkeypatch):
    class Report:
        def consume(self, stream, data):
            if data:
                assert proc.wait(timeout=3) == 0
                raise RuntimeError("private-passive-capture-canary")

    events = []
    with subprocess.Popen(
        [sys.executable, "-c", "print('one effect')"],
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:
        calls = deny_group_signal(monkeypatch, proc)
        lifecycle = SimpleNamespace(notify=lambda event, **data: events.append((event, data)))
        with pytest.raises(RuntimeError, match="private-passive-capture-canary"):
            bounded_capture(proc, report=Report(), lifecycle=lifecycle)
    assert calls == [(proc.pid, signal.SIGKILL)]
    assert len(events) == 1
    event, snapshot = events[0]
    assert event == "process.exited" and snapshot["returncode"] == 0
    assert snapshot["duration_ms"] >= 0
    assert snapshot["stop_reason"] == "internal_error"
    assert snapshot["cleanup_status"] == "observed"
    assert "private-passive" not in str(events)
