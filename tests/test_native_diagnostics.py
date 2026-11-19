"""Closed diagnostics through actual synthetic native supervision and persistence."""

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from harness.execution_contract import MAX_RECORD
from harness.execution_hosts import native_diagnostic
from harness.execution_store import get_execution
from harness.native_diagnostics import CATALOG, message, preserve
from tests.test_execution_hosts import test_real_external_host_exit_zero_is_incomplete as run_host


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("category", ["authentication", "permission", "generic", "structured"])
def test_failure_envelopes_project_only_catalog(host, category):
    code, prose = {
        "authentication": ("NATIVE_AUTHENTICATION_REQUIRED", "authentication required AUTH_CANARY"),
        "permission": ("NATIVE_PERMISSION_DENIED", "permission denied PERMISSION_CANARY"),
        "generic": ("NATIVE_PROVIDER_FAILED", "PROVIDER_PRIVATE_CANARY"),
        "structured": ("NATIVE_USAGE_LIMIT", {"code": "rate_limit", "message": "RESET_CANARY"}),
    }[category]
    record = (
        {"type": "turn.failed", "error": prose}
        if host == "codex"
        else {"type": "result", "is_error": True, "subtype": "success", "errors": [prose]}
    )
    if host == "claude" and category == "structured":
        record["error"] = prose
    assert native_diagnostic(host, json.dumps(record).encode()) == message(code)
    ordinary = {"type": "assistant", "message": {"content": prose}}
    assert native_diagnostic(host, json.dumps(ordinary).encode()) is None


@pytest.mark.parametrize(
    "raw", [b'{"PRIVATE_PARSE_CANARY":', b"[]", b'{"type":[]}', b"x" * (MAX_RECORD + 1)]
)
def test_malformed_protocol_uses_fixed_message(raw):
    assert native_diagnostic("codex", raw) == message("NATIVE_STREAM_INVALID")


def test_unknown_codes_and_exception_text_cannot_escape():
    with pytest.raises(ValueError, match="^unknown native diagnostic code$"):
        message("UNKNOWN_PRIVATE_CANARY")
    assert preserve(ValueError("EXCEPTION_PRIVATE_CANARY")) == message("NATIVE_SUPERVISION_FAILED")
    for code in CATALOG:
        assert preserve(ValueError(message(code))) == message(code)


@pytest.mark.parametrize("emission", ["stderr", "fragmented", "oversized", "malformed"])
def test_external_native_canaries_never_persist(
    controller, sample_repo, monkeypatch, tmp_path, launched, emission
):
    from tests import native_provider_fixture

    install = native_provider_fixture.install_fake_claude
    canary = "NATIVE_PRIVATE_" + emission.upper() + "_CANARY"
    if emission == "oversized":
        raw = (canary + "x" * MAX_RECORD + "\n").encode()[: MAX_RECORD + 2]
    elif emission == "malformed":
        raw = ('{"' + canary + '":\n').encode()
    else:
        raw = (canary + "\n" + canary + "\n").encode()

    def synthetic(binary):
        executable = install(binary)
        channel = 1 if emission == "malformed" else 2
        chunks = [raw[:7], raw[7:]] if emission == "fragmented" else [raw]
        with executable.open("a") as stream:
            stream.write(
                "\n" + "\n".join(f"os.write({channel}, {chunk!r})" for chunk in chunks) + "\n"
            )
        return executable

    monkeypatch.setattr(native_provider_fixture, "install_fake_claude", synthetic)
    expected = (
        "[NATIVE_STREAM_INVALID]" if emission in {"oversized", "malformed"} else "no guarded AL"
    )
    run_host(
        controller,
        sample_repo,
        monkeypatch,
        tmp_path,
        launched,
        {"type": "assistant", "message": {"content": "ORDINARY_PROSE_CANARY"}},
        expected,
    )
    execution = dict(
        controller.conn.execute("SELECT * FROM executions ORDER BY rowid DESC LIMIT 1").fetchone()
    )
    logs = controller.conn.execute(
        "SELECT channel,text FROM execution_logs WHERE execution_id=?", (execution["id"],)
    ).fetchall()
    retained = json.dumps([execution["error"], [dict(row) for row in logs]])
    assert canary not in retained and "ORDINARY_PROSE_CANARY" not in retained
    assert hashlib.sha256(raw).hexdigest() not in retained
    stderr = [row["text"] for row in logs if row["channel"] == "stderr"]
    assert len(stderr) <= 1
    assert all(value == message("NATIVE_STDERR_SUPPRESSED") for value in stderr)
    assert execution["status"] == "Interrupted" and execution["result"] is None
    for path in Path(execution["handoff_path"]).parent.glob("worker-*.log"):
        assert canary.encode() not in path.read_bytes()


@pytest.mark.parametrize("stage", ["setup", "spawn"])
def test_setup_spawn_exceptions_keep_no_spawn_proof_and_fixed_diagnostics(
    controller, sample_repo, monkeypatch, tmp_path, stage
):
    from harness.execution_start_retry import startup_disposition
    from harness.execution_worker import supervise
    from tests.execution_helpers import approved_execution, isolated_launch_environment

    isolated_launch_environment(monkeypatch, tmp_path / "setup-tooling")
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    from harness.execution_worker import launch_worker

    monkeypatch.setattr("harness.execution_worker._spawn_worker", Mock())
    reservation = launch_worker(controller.conn, eid)
    token = get_execution(controller.conn, eid)["worker_token"]
    if stage == "setup":
        monkeypatch.setattr(
            "harness.execution_worker.installed_package",
            Mock(side_effect=ValueError("SETUP_PRIVATE_CANARY")),
        )
        code = "NATIVE_SETUP_FAILED"
    else:
        original = subprocess.Popen

        def popen(argv, *args, **kwargs):
            if "--session-id" in argv:
                raise OSError("SPAWN_PRIVATE_CANARY")
            return original(argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", popen)
        code = "NATIVE_SPAWN_FAILED"
    supervise(controller.conn, eid, token)
    saved = get_execution(controller.conn, eid)
    assert saved["status"] == "Unavailable" and saved["error"] == message(code)
    row = controller.conn.execute(
        "SELECT * FROM execution_attempts WHERE execution_id=?", (eid,)
    ).fetchone()
    assert row is not None and row["id"] == reservation["attempt_id"]
    assert row["status"] == "PreSpawnFailed" and row["child_pid"] is None
    disposition = startup_disposition(controller, saved)
    assert disposition["diagnostic"].startswith(
        "Startup failed before native spawn: [" + code + "]"
    )
    assert "PRIVATE_CANARY" not in json.dumps(disposition)
    if stage == "setup":
        assert disposition["retry_eligible"]
    else:
        assert not disposition["retry_eligible"]


def test_managed_worker_streams_have_no_fallback_log_capture(monkeypatch, tmp_path):
    from harness.execution_worker import _spawn_worker

    execution = {"handoff_path": str(tmp_path / "handoff.json")}
    monkeypatch.setattr("harness.execution_bridge.bridge_root", lambda _: tmp_path)
    popen = Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    _spawn_worker(["synthetic-worker"], execution, "synthetic-attempt")
    kwargs = popen.call_args.kwargs
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL and kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["start_new_session"] and kwargs["close_fds"]
    assert not list(tmp_path.glob("worker-*.log"))


@pytest.mark.parametrize(
    "host,record,code",
    [
        (
            "claude",
            {
                "type": "result",
                "is_error": True,
                "subtype": "error_PRIVATE_CANARY",
                "errors": [{"auth": "NESTED_PRIVATE_CANARY"}],
            },
            "NATIVE_PROVIDER_FAILED",
        ),
        (
            "codex",
            {"type": "thread.failed", "error": {"message": {"reasoning": "NESTED_PRIVATE_CANARY"}}},
            "NATIVE_PROVIDER_FAILED",
        ),
        (
            "claude",
            {"type": "result", "subtype": {"auth": "NESTED_PRIVATE_CANARY"}},
            "NATIVE_STREAM_INVALID",
        ),
    ],
)
def test_nested_failure_values_and_unknown_subtypes_are_not_diagnostics(host, record, code):
    raw = json.dumps(record).encode()
    result = native_diagnostic(host, raw)
    assert result == message(code)
    assert "PRIVATE_CANARY" not in result and hashlib.sha256(raw).hexdigest() not in result


def test_managed_worker_spawn_failure_retains_no_spawn_evidence(
    controller, sample_repo, monkeypatch
):
    from harness.execution_recovery_settlement import _not_started
    from harness.execution_worker import launch_worker
    from tests.execution_helpers import approved_execution

    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    monkeypatch.setattr(
        "harness.execution_worker._spawn_worker", Mock(side_effect=OSError("WORKER_PRIVATE_CANARY"))
    )
    result = launch_worker(controller.conn, eid)
    execution = get_execution(controller.conn, eid)
    attempt = dict(
        controller.conn.execute(
            "SELECT * FROM execution_attempts WHERE execution_id=?", (eid,)
        ).fetchone()
    )
    assert result == {"status": "Unavailable", "error": message("WORKER_SPAWN_FAILED")}
    assert execution["status"] == "Unavailable" and execution["error"] == message(
        "WORKER_SPAWN_FAILED"
    )
    assert attempt["status"] == "PreSpawnFailed" and attempt["child_pid"] is None
    assert _not_started(execution, attempt, required_stage="worker-spawn")
    logs = [
        row[0]
        for row in controller.conn.execute(
            "SELECT text FROM execution_logs WHERE execution_id=?", (eid,)
        )
    ]
    assert any(
        value.startswith("Startup failed before native spawn: [WORKER_SPAWN_FAILED]")
        for value in logs
    )
    assert "WORKER_PRIVATE_CANARY" not in json.dumps([result, logs])


def seed_native_attempt(monkeypatch):
    from harness import execution_worker
    from tests import test_execution_hosts

    original = execution_worker.supervise

    def supervised(conn, eid, token):
        execution = get_execution(conn, eid)
        conn.execute(
            "INSERT INTO execution_attempts "
            "(id,execution_id,token,mode,status,epoch_id,created_at,updated_at) "
            "VALUES ('cleanup-attempt',?,?,'start','Reserved',?,'now','now')",
            (eid, token, execution["epoch_id"]),
        )
        return original(conn, eid, token)

    monkeypatch.setattr(test_execution_hosts, "supervise", supervised)


@pytest.mark.parametrize("stage", ["signal", "runtime-close", "closure-record"])
def test_cleanup_failures_do_not_skip_independent_disposal(
    controller, sample_repo, monkeypatch, tmp_path, launched, stage
):
    from harness.execution_settlement import closed_context
    from harness.native_diagnostics import NativeStageFailure
    from harness.native_runtime import NativeRuntime

    seed_native_attempt(monkeypatch)
    observed = []
    original_close = NativeRuntime.close

    def close(runtime):
        observed.append("runtime-close")
        original_close(runtime)
        if stage == "runtime-close":
            raise RuntimeError("CLOSE_PRIVATE_CANARY")

    monkeypatch.setattr(NativeRuntime, "close", close)

    import os

    original_signal = os.killpg
    original_popen = subprocess.Popen
    native_pids = set()

    def popen(argv, *args, **kwargs):
        process = original_popen(argv, *args, **kwargs)
        if "--session-id" in argv:
            native_pids.add(process.pid)
        return process

    def signal_group(pid, sig):
        if pid not in native_pids:
            return original_signal(pid, sig)
        if stage == "signal":
            raise PermissionError("SIGNAL_PRIVATE_CANARY")
        raise ProcessLookupError

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr("os.killpg", signal_group)
    from harness.execution_settlement import record_closed_context

    closure_publish = Mock(wraps=record_closed_context)
    monkeypatch.setattr("harness.execution_settlement.record_closed_context", closure_publish)
    if stage == "closure-record":
        monkeypatch.setattr(
            "harness.execution_settlement.record_closed_context",
            Mock(side_effect=OSError("CLOSURE_PRIVATE_CANARY")),
        )
    with pytest.raises(NativeStageFailure) as error:
        run_host(controller, sample_repo, monkeypatch, tmp_path, launched, None, "no guarded AL")
    receipt = error.value.receipt
    assert "runtime-close" in observed
    execution = dict(
        controller.conn.execute("SELECT * FROM executions ORDER BY rowid DESC LIMIT 1").fetchone()
    )
    attempt = controller.conn.execute(
        "SELECT * FROM execution_attempts WHERE execution_id=?", (execution["id"],)
    ).fetchone()
    assert execution["status"] == "RecoveryRequired" and execution["result"] is None
    assert attempt["status"] == "Exited"
    assert receipt["returncode_known"] and receipt["status_publication_reached"]
    actual_closure = closed_context(execution, attempt)
    assert receipt["closure_validated"] is actual_closure
    if stage == "signal":
        # An exited leader and closed stdio do not settle a denied process group.
        assert actual_closure is False
        closure_publish.assert_not_called()
        assert any(
            row["stage"] == "final-signal" and row["category"] == "os-error"
            for row in receipt["failures"]
        )
    else:
        assert actual_closure is False
        assert any(row["stage"] == stage for row in receipt["failures"])
    logs = [
        row[0]
        for row in controller.conn.execute(
            "SELECT text FROM execution_logs WHERE execution_id=?", (execution["id"],)
        )
    ]
    assert "PRIVATE_CANARY" not in json.dumps([receipt, execution["error"], logs])


def test_callback_failure_releases_reader_and_supervisor(
    controller, sample_repo, monkeypatch, tmp_path, launched
):
    from harness import execution_worker
    from harness.execution_settlement import closed_context

    seed_native_attempt(monkeypatch)
    original = execution_worker._drain
    streams = []

    class CallbackQueue:
        def __init__(self, queue):
            self.queue = queue

        def put(self, value):
            raise RuntimeError("CALLBACK_PRIVATE_CANARY")

        def control_put(self, value):
            return self.queue.put(value)

    def drain(stream, channel, queue):
        streams.append(stream)
        return original(stream, channel, CallbackQueue(queue))

    def absent_group(*args):
        raise ProcessLookupError

    monkeypatch.setattr(execution_worker, "_drain", drain)
    monkeypatch.setattr("os.killpg", absent_group)
    run_host(
        controller,
        sample_repo,
        monkeypatch,
        tmp_path,
        launched,
        None,
        "[NATIVE_SUPERVISION_FAILED]",
    )
    assert streams and all(stream.closed for stream in streams)
    execution = get_execution(
        controller.conn,
        controller.conn.execute("SELECT id FROM executions ORDER BY rowid DESC LIMIT 1").fetchone()[
            0
        ],
    )
    attempt = controller.conn.execute(
        "SELECT * FROM execution_attempts WHERE execution_id=?", (execution["id"],)
    ).fetchone()
    assert execution["status"] == "Interrupted" and execution["result"] is None
    assert attempt["status"] == "Exited" and closed_context(execution, attempt)
    assert "PRIVATE_CANARY" not in execution["error"]


def test_stage_carrier_rejects_unselected_fields():
    from harness.native_diagnostics import NativeStageFailure

    with pytest.raises(ValueError, match="^closed native stage receipt required$"):
        NativeStageFailure({"prose": "STAGE_PRIVATE_CANARY"})


def test_cleanup_cannot_publish_closure_after_ownership_loss(
    controller, sample_repo, monkeypatch, tmp_path, launched
):
    from harness.native_diagnostics import NativeStageFailure
    from harness.native_runtime import NativeRuntime

    seed_native_attempt(monkeypatch)
    original_close = NativeRuntime.close

    def close(runtime):
        original_close(runtime)
        controller.conn.execute(
            "UPDATE executions SET worker_token='replacement' WHERE status='Running'"
        )

    def absent_group(*args):
        raise ProcessLookupError

    publish = Mock(side_effect=AssertionError("foreign owner must not publish closure"))
    monkeypatch.setattr(NativeRuntime, "close", close)
    monkeypatch.setattr("os.killpg", absent_group)
    monkeypatch.setattr("harness.execution_settlement.record_closed_context", publish)
    with pytest.raises(NativeStageFailure) as error:
        run_host(controller, sample_repo, monkeypatch, tmp_path, launched, None, "no guarded AL")
    publish.assert_not_called()
    assert error.value.receipt["returncode_known"]
    assert error.value.receipt["closure_validated"] is False
    assert error.value.receipt["status_publication_reached"] is False
    execution = controller.conn.execute(
        "SELECT status,worker_token FROM executions ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert execution["status"] == "Running" and execution["worker_token"] == "replacement"
