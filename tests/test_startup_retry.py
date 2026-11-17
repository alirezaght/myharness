# ruff: noqa: F811
"""Real approval, epoch and authenticated no-start receipts for startup recovery."""

import json
from pathlib import Path

import pytest

from harness.execution import start_execution
from harness.execution_models import ExecutionConflict
from harness.execution_recovery_settlement import record_not_started
from harness.execution_start_retry import startup_disposition
from harness.execution_store import get_execution
from harness.execution_worker import launch_worker
from tests.execution_helpers import approved_execution


@pytest.fixture
def failed(controller, sample_repo, monkeypatch):
    tid, eid = approved_execution(controller, sample_repo, monkeypatch)
    execution = get_execution(controller.conn, eid)
    controller.conn.execute(
        "UPDATE executions SET status='Unavailable',worker_token='failed',al_run_id='bound-run' "
        "WHERE id=?",
        (eid,),
    )
    controller.conn.execute(
        "INSERT INTO execution_attempts "
        "(id,execution_id,token,mode,status,epoch_id,created_at,updated_at) "
        "VALUES ('first',?,'failed','start','PreSpawnFailed',?,'now','now')",
        (eid, execution["epoch_id"]),
    )
    record_not_started(execution, "failed", stage="native-setup")
    return tid, eid


def test_receipts_refuse_missing_foreign_unknown_and_prior_native(controller, failed):
    _, eid = failed
    execution = get_execution(controller.conn, eid)
    assert startup_disposition(controller, execution)["retry_eligible"]
    path = Path(execution["handoff_path"]).parent / "settlements/unspawned-failed.json"
    original = path.read_bytes()
    path.unlink()
    assert not startup_disposition(controller, execution)["retry_eligible"]
    value = json.loads(original)
    value["execution_id"] = "foreign"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    assert not startup_disposition(controller, execution)["retry_eligible"]
    path.write_bytes(original)
    controller.conn.execute(
        "UPDATE execution_attempts SET status='Launching' WHERE execution_id=?", (eid,)
    )
    assert not startup_disposition(controller, execution)["retry_eligible"]
    controller.conn.execute(
        "UPDATE execution_attempts SET status='PreSpawnFailed',native_id='old-native' "
        "WHERE execution_id=?",
        (eid,),
    )
    assert not startup_disposition(controller, execution)["retry_eligible"]


def test_retry_requires_initialized_proof_and_stale_epoch_refuses(controller, failed, monkeypatch):
    _, eid = failed
    monkeypatch.setattr(
        "harness.execution_worker._spawn_worker", lambda *a: pytest.fail("must not spawn")
    )
    with pytest.raises(ExecutionConflict, match="stale_epoch"):
        launch_worker(
            controller.conn,
            eid,
            mode="retry-start",
            expected_epoch_id="old",
            expected_authority_generation=0,
        )
    # A recorded ID alone cannot forge protected AL initialization.
    with pytest.raises((OSError, ValueError)):
        launch_worker(controller.conn, eid, mode="retry-start")
    assert controller.conn.execute("SELECT COUNT(*) FROM execution_attempts").fetchone()[0] == 1


def test_preflight_refuses_before_new_handoff_or_reservation(controller, sample_repo, monkeypatch):
    from harness.launch_readiness import LaunchReadinessError
    from tests.test_dashboard_guidance import approved

    tid = approved.__wrapped__(controller, sample_repo, monkeypatch)
    monkeypatch.setattr(
        "harness.launch_readiness.readiness",
        lambda *a: {"checks": [{"status": "BLOCKED", "reason": "unsupported selected version"}]},
    )
    from harness.execution import publish

    monkeypatch.setattr("harness.execution.publish", lambda *a: pytest.fail("must not publish"))
    with pytest.raises(LaunchReadinessError, match="unsupported selected version"):
        start_execution(controller, tid)
    assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0
    monkeypatch.setattr("harness.execution.publish", publish)
    assert start_execution(controller, tid, launch=False)["created"]


def test_exact_native_version_refuses_substrings(tmp_path):
    from harness.native_tool_policy import executable_identity

    executable = tmp_path / "native"
    for version, accepted in (
        ("2.1.325", True),
        ("2.1.310", True),
        ("2.1.3250", False),
        ("prefix 2.1.325", False),
    ):
        executable.write_text("#!/bin/sh\nprintf '%s\\n' '" + version + " (Claude Code)'\n")
        executable.chmod(0o700)
        if accepted:
            assert executable_identity(executable, "claude")["version"] == version
        else:
            with pytest.raises(ValueError, match="unsupported"):
                executable_identity(executable, "claude")


@pytest.fixture
def initialized_failure(operations, monkeypatch):
    from harness.execution_hosts import package_fingerprint

    _, _, controller, initialization, package, execution, envelope = operations
    package = dict(package, sha256=package_fingerprint(package["package"]))
    monkeypatch.setattr("harness.launch_readiness.installed_package", lambda *a: package)
    monkeypatch.setattr("harness.execution_hosts.installed_package", lambda *a: package)
    monkeypatch.setattr("harness.execution_hosts.managed_launcher", lambda: package["launcher"])
    conn = controller.conn
    conn.execute(
        "UPDATE executions SET status='Unavailable',worker_token='failed',"
        "error='Specific startup failure' WHERE id=?",
        (execution["id"],),
    )
    conn.execute(
        "INSERT INTO execution_attempts "
        "(id,execution_id,token,mode,status,epoch_id,created_at,updated_at) "
        "VALUES ('first',?,'failed','start','PreSpawnFailed',?,'now','now')",
        (execution["id"], execution["epoch_id"]),
    )
    record_not_started(execution, "failed", stage="native-setup")
    return controller, execution, initialization, envelope


def test_retry_same_protected_run_and_duplicate_refused(initialized_failure, monkeypatch):
    controller, execution, initialization, _ = initialized_failure
    calls = []
    monkeypatch.setattr("harness.execution_worker._spawn_worker", lambda *a: calls.append(a))
    eid = execution["id"]
    first = launch_worker(controller.conn, eid, mode="retry-start")
    assert first["status"] == "WorkerOwned"
    with pytest.raises(ExecutionConflict):
        launch_worker(controller.conn, eid, mode="retry-start")
    current = get_execution(controller.conn, eid)
    assert len(calls) == 1
    assert current["approval_id"] == execution["approval_id"]
    assert current["al_run_id"] == initialization["al_run_id"]
    assert current["al_session_id"] == execution["al_session_id"]
    assert current["native_id"] is None
    assert (
        controller.conn.execute(
            "SELECT status FROM execution_attempts WHERE id='first'"
        ).fetchone()[0]
        == "PreSpawnFailed"
    )
    assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1
    assert controller.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1


def test_refresh_preserves_specific_failure_and_history(initialized_failure, monkeypatch):
    from harness.execution_logs import append_log
    from harness.execution_worker import _reconnect

    controller, execution, _, _ = initialized_failure
    append_log(
        controller.conn,
        execution["id"],
        "worker",
        "Startup failed before native spawn: api_key=private Specific startup failure",
    )
    for index in range(2):
        token = f"refresh-{index}"
        controller.conn.execute(
            "UPDATE executions SET worker_token=? WHERE id=?", (token, execution["id"])
        )
        controller.conn.execute(
            "INSERT INTO execution_attempts "
            "(id,execution_id,token,mode,status,epoch_id,created_at,updated_at) "
            "VALUES (?,?,?,'reconnect','Reserved',?,'now','now')",
            (token, execution["id"], token, execution["epoch_id"]),
        )
        current = get_execution(controller.conn, execution["id"])
        _reconnect(controller, current, token)
        assert (
            get_execution(controller.conn, execution["id"])["error"] == "Specific startup failure"
        )
    value = startup_disposition(controller, get_execution(controller.conn, execution["id"]))
    assert "private" not in value["diagnostic"] and "[redacted]" in value["diagnostic"]


from tests.test_execution_operations import operations  # noqa: E402,F401


@pytest.fixture
def pending_bridge(initialized_failure, monkeypatch, tmp_path):
    import shutil

    import harness.execution_bootstrap as bootstrap
    from harness.execution_bridge import upgrade_bridge

    controller, execution, initial, envelope = initialized_failure
    source = tmp_path / "candidate" / "harness"
    shutil.copytree(
        Path(bootstrap.__file__).parent, source, ignore=shutil.ignore_patterns("__pycache__")
    )
    with (source / "__init__.py").open("a") as stream:
        stream.write("\n# Independent startup maintenance candidate.\n")
    monkeypatch.setattr(bootstrap, "__file__", str(source / "execution_bootstrap.py"))
    before = {
        key: Path(initial[key]).read_bytes()
        for key in ("source", "binding_path", "initialization_path")
    }
    result = upgrade_bridge(
        controller,
        execution["id"],
        reason="Qualified startup retry bridge with first native binding",
        startup_retry=True,
    )
    assert result["status"] == "Upgraded"
    assert all(Path(initial[key]).read_bytes() == raw for key, raw in before.items())
    return controller, execution, result, before


def test_pending_bridge_binds_first_identity_immutably(pending_bridge, monkeypatch):
    from harness.execution_bridge import bridge_root
    from harness.execution_store import bind_host

    controller, execution, upgraded, _ = pending_bridge
    assert json.loads(Path(upgraded["receipt"]).read_bytes())["schema_version"] == 2
    assert bridge_root(get_execution(controller.conn, execution["id"])) == Path(upgraded["bridge"])
    monkeypatch.setattr("harness.execution_worker._spawn_worker", lambda *a: None)
    attempt = launch_worker(controller.conn, execution["id"], mode="retry-start")
    current = get_execution(controller.conn, execution["id"])
    controller.conn.execute(
        "UPDATE execution_attempts SET status='Launching' WHERE id=?", (attempt["attempt_id"],)
    )
    bind_host(controller.conn, execution["id"], "assigned-native-id", current["worker_token"])
    known = get_execution(controller.conn, execution["id"])
    assert bridge_root(known) == Path(upgraded["bridge"])
    paths = list(Path(upgraded["receipt"]).parent.glob("first-bindings/*.json"))
    assert len(paths) == 1
    original = paths[0].read_bytes()
    assert json.loads(original)["meaning"] == "identity-assigned-not-spawn-proof"
    bind_host(controller.conn, execution["id"], "assigned-native-id", current["worker_token"])
    assert paths[0].read_bytes() == original
    with pytest.raises(ExecutionConflict):
        bind_host(controller.conn, execution["id"], "other-native-id", current["worker_token"])
    controller.conn.execute(
        "UPDATE executions SET worker_token='later-worker' WHERE id=?", (execution["id"],)
    )
    assert bridge_root(get_execution(controller.conn, execution["id"])) == Path(upgraded["bridge"])
    assert not startup_disposition(controller, get_execution(controller.conn, execution["id"]))[
        "retry_eligible"
    ]
    paths[0].unlink()
    with pytest.raises((ValueError, OSError)):
        bridge_root(get_execution(controller.conn, execution["id"]))


def test_binding_receipt_survives_rollback_without_authorizing_identity(
    pending_bridge, monkeypatch
):
    from harness.db import transaction
    from harness.execution_bridge import bridge_root
    from harness.execution_store import bind_host

    controller, execution, upgraded, _ = pending_bridge
    monkeypatch.setattr("harness.execution_worker._spawn_worker", lambda *a: None)
    attempt = launch_worker(controller.conn, execution["id"], mode="retry-start")
    current = get_execution(controller.conn, execution["id"])
    controller.conn.execute(
        "UPDATE execution_attempts SET status='Launching' WHERE id=?", (attempt["attempt_id"],)
    )
    with pytest.raises(RuntimeError):
        with transaction(controller.conn):
            bind_host(
                controller.conn, execution["id"], "observed-codex-id", current["worker_token"]
            )
            raise RuntimeError("lost database commit")
    assert get_execution(controller.conn, execution["id"])["native_id"] is None
    assert bridge_root(get_execution(controller.conn, execution["id"])) == Path(upgraded["bridge"])
    bind_host(controller.conn, execution["id"], "observed-codex-id", current["worker_token"])
    assert bridge_root(get_execution(controller.conn, execution["id"])) == Path(upgraded["bridge"])


def test_binding_publish_failure_and_foreign_token_fail_closed(pending_bridge, monkeypatch):
    from harness.execution_bridge import bridge_root
    from harness.execution_store import bind_host

    controller, execution, upgraded, _ = pending_bridge
    monkeypatch.setattr("harness.execution_worker._spawn_worker", lambda *a: None)
    result = launch_worker(controller.conn, execution["id"], mode="retry-start")
    current = get_execution(controller.conn, execution["id"])
    controller.conn.execute(
        "UPDATE execution_attempts SET status='Launching' WHERE id=?", (result["attempt_id"],)
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            "harness.execution_bridge_binding.atomic_file",
            lambda *a: (_ for _ in ()).throw(OSError("before publication")),
        )
        with pytest.raises(OSError, match="before publication"):
            bind_host(controller.conn, execution["id"], "native-id", current["worker_token"])
    assert get_execution(controller.conn, execution["id"])["native_id"] is None
    with pytest.raises(ExecutionConflict):
        bind_host(controller.conn, execution["id"], "native-id", "foreign-worker")
    bind_host(controller.conn, execution["id"], "native-id", current["worker_token"])
    path = next(Path(upgraded["receipt"]).parent.glob("first-bindings/*.json"))
    original = path.read_text()
    for key, value in (
        ("worker_token", "foreign-worker"),
        ("parent", "sha256:" + "0" * 64),
        ("epoch_id", "foreign-epoch"),
    ):
        changed = json.loads(original)
        changed[key] = value
        path.write_text(json.dumps(changed))
        with pytest.raises((ValueError, OSError)):
            bridge_root(get_execution(controller.conn, execution["id"]))
        path.write_text(original)


def test_actual_old_worker_to_upgraded_worker_dispatch(controller, monkeypatch, launched, tmp_path):
    import os
    import shutil
    import subprocess
    from types import SimpleNamespace

    import harness.execution_bootstrap as bootstrap
    import harness.execution_worker as worker
    from harness.execution_bridge import bridge_root
    from tests.native_provider_fixture import install_fake_claude
    from tests.test_execution_operations import operations as operation_fixture

    current_file = bootstrap.__file__
    old_source = tmp_path / "retained-old" / "harness"
    shutil.copytree(
        Path(current_file).parent, old_source, ignore=shutil.ignore_patterns("__pycache__")
    )
    old_worker = old_source / "execution_worker.py"
    old_worker.write_text(
        old_worker.read_text().replace(
            '["start", "resume", "reconnect", "retry-start"]', '["start", "resume", "reconnect"]'
        )
    )
    old_policy = old_source / "native_tool_policy.py"
    old_policy.write_text(old_policy.read_text().replace('"2.1.310", "2.1.325"', '"2.1.310"'))
    monkeypatch.setattr(bootstrap, "__file__", str(old_source / "execution_bootstrap.py"))
    fixture = operation_fixture.__wrapped__(
        controller, monkeypatch, launched, SimpleNamespace(param="claude")
    )
    data = next(fixture)
    try:
        _, _, controller, initial, package, execution, envelope = data
        monkeypatch.setattr(bootstrap, "__file__", current_file)
        source, home, _, launcher, _ = launched
        shutil.copytree(source / "claude/skills/al", home / ".claude/skills/al")
        shutil.copytree(source / "claude/agents", home / ".claude/agents/al")
        native_bin = tmp_path / "explicit-native-bin"
        executable = install_fake_claude(native_bin)
        executable.write_text(executable.read_text().replace("2.1.310", "2.1.325"))
        monkeypatch.setenv(
            "PATH", os.pathsep.join((str(launcher.parent), str(native_bin), os.environ["PATH"]))
        )
        old_root = bridge_root(execution)
        old_bytes = {p: p.read_bytes() for p in old_root.rglob("*") if p.is_file()}
        protected = {
            key: Path(initial[key]).read_bytes()
            for key in ("source", "binding_path", "initialization_path")
        }
        processes = []
        original_spawn = worker._spawn_worker
        original_popen = subprocess.Popen

        def observed_spawn(argv, selected, attempt):
            def capture(*args, **kwargs):
                process = original_popen(*args, **kwargs)
                processes.append(process)
                return process

            with monkeypatch.context() as patch:
                patch.setattr(subprocess, "Popen", capture)
                original_spawn(argv, selected, attempt)

        monkeypatch.setattr(worker, "_spawn_worker", observed_spawn)
        try:
            # Initialization replay has already marked the run active. Model the
            # initial worker's admitted reservation, then execute its real old code.
            controller.conn.execute(
                "INSERT INTO execution_attempts "
                "(id,execution_id,token,mode,status,epoch_id,created_at,updated_at) "
                "VALUES ('original-start',?,?,'start','Reserved',?,'now','now')",
                (execution["id"], execution["worker_token"], execution["epoch_id"]),
            )
            observed_spawn(
                worker.module_argv("harness.execution_worker")
                + [
                    "--db",
                    envelope["database"],
                    "--execution",
                    execution["id"],
                    "--token",
                    execution["worker_token"],
                    "--mode",
                    "start",
                ],
                execution,
                "original-start",
            )
            assert processes[-1].wait(timeout=45) == 0
            first = get_execution(controller.conn, execution["id"])
            assert first["status"] == "Unavailable"
            assert "unsupported native executable version" in first["error"]
            assert first["native_id"] is None
            assert startup_disposition(controller, first)["retry_eligible"]
            result = worker.launch_worker(controller.conn, execution["id"], mode="retry-start")
            assert processes[-1].wait(timeout=45) == 0
            current = get_execution(controller.conn, execution["id"])
            attempt = controller.conn.execute(
                "SELECT * FROM execution_attempts WHERE id=?", (result["attempt_id"],)
            ).fetchone()
            assert attempt["child_pid"] is not None, current
            assert current["native_id"] is not None, current
            assert bridge_root(current) != old_root
            assert current["approval_id"] == execution["approval_id"]
            assert current["al_run_id"] == initial["al_run_id"]
            assert all(path.read_bytes() == raw for path, raw in old_bytes.items())
            assert all(Path(initial[key]).read_bytes() == raw for key, raw in protected.items())
            assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1
            assert list(
                Path(execution["handoff_path"]).parent.glob("bridge-upgrades/first-bindings/*.json")
            )
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
    finally:
        fixture.close()


def test_dashboard_observes_selected_readiness_once(initialized_failure, monkeypatch):
    import harness.launch_readiness as launch
    from harness.dashboard.view import dashboard_view

    controller, execution, _, envelope = initialized_failure
    observed = []
    original = launch.readiness

    def observe(*args):
        observed.append(args)
        return original(*args)

    monkeypatch.setattr(launch, "readiness", observe)
    view = dashboard_view(Path(envelope["database"]), execution["task_id"], None)
    assert len(observed) == 1
    assert view["task"]["guidance"] == view["tasks"][0]["guidance"]
    assert view["task"]["guidance"]["status"] == "Agent did not start"


def test_start_replay_recovers_dispatched_run_despite_readiness_drift(
    controller, sample_repo, db_path, monkeypatch
):
    from tests.test_dashboard import api, serve
    from tests.test_dashboard_guidance import approved, post_start

    tid = approved.__wrapped__(controller, sample_repo, monkeypatch)
    dispatched = []

    def launch(conn, eid):
        dispatched.append(eid)
        conn.execute("UPDATE executions SET status='WorkerOwned' WHERE id=?", (eid,))
        return {"status": "WorkerOwned"}

    monkeypatch.setattr("harness.execution_worker.launch_worker", launch)
    with serve(db_path, tid) as server:
        body = {
            "task": tid,
            "host": "claude",
            "approval_binding": api(server)["task"]["launch"]["binding"],
        }
        status, first = post_start(server, body)
        assert status == 200
        # Treat the first reply as lost. A replay must recover identity before readiness.
        monkeypatch.setattr(
            "harness.launch_readiness.readiness",
            lambda *a: pytest.fail("dispatched replay must not preflight"),
        )
        status, replay = post_start(server, body)
        assert status == 200 and replay["created"] is False
        assert replay["execution"]["id"] == first["execution"]["id"]
        assert post_start(server, body | {"host": "codex"})[0] == 409
        stale = body | {"approval_binding": body["approval_binding"] | {"content_hash": "stale"}}
        assert post_start(server, stale)[0] == 409
    assert len(dispatched) == 1
    assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1
    assert controller.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1


def test_reserved_start_readiness_failure_preserves_host_binding(
    controller, sample_repo, db_path, monkeypatch
):
    from tests.test_dashboard import api, serve
    from tests.test_dashboard_guidance import approved, post_start

    tid = approved.__wrapped__(controller, sample_repo, monkeypatch)
    reserved = start_execution(controller, tid, host="claude", launch=False)["execution"]
    monkeypatch.setattr(
        "harness.execution_worker.launch_worker", lambda *a: pytest.fail("blocked dispatch")
    )
    monkeypatch.setattr(
        "harness.launch_readiness.readiness",
        lambda *a: {"checks": [{"status": "BLOCKED", "reason": "selected host changed"}]},
    )
    with serve(db_path, tid) as server:
        body = {
            "task": tid,
            "host": "claude",
            "approval_binding": api(server)["task"]["launch"]["binding"],
        }
        status, result = post_start(server, body)
        assert status == 409 and result["host_switchable"] is False
        assert result["execution_id"] == reserved["id"] and result["reload"] is False
        assert post_start(server, body | {"host": "codex"})[0] == 409
    current = get_execution(controller.conn, reserved["id"])
    assert current["status"] == "Reserved" and current["host"] == "claude"
    assert current["binding"] == reserved["binding"]


def test_initialized_runtime_recovers_missing_binding_without_restarting(operations, monkeypatch):
    import harness.execution_initialization as service
    from harness.execution_initialization import initialize_execution

    _, _, controller, initial, package, execution, envelope = operations
    marker = Path(execution["handoff_path"]).parent / "runtime-binding.json"
    binding = marker.read_bytes()
    source = Path(initial["source"])
    previous = source.read_bytes()
    marker.unlink()
    original = service.run_owned

    def no_second_start(argv, **kwargs):
        assert "start" not in argv, "initialized runtime must never be restarted"
        return original(argv, **kwargs)

    monkeypatch.setattr(service, "run_owned", no_second_start)
    recovered = initialize_execution(controller, execution, package, envelope)
    assert recovered == initial
    assert marker.read_bytes() == binding
    assert source.read_bytes() == previous


@pytest.mark.parametrize(
    "change", ["execution", "epoch", "run", "source", "corrupt-state", "unpublished", "ambiguous"]
)
def test_missing_binding_recovery_refuses_changed_origin_without_start_or_publication(
    operations, monkeypatch, change
):
    import harness.execution_initialization as service
    from harness.execution_initialization import initialize_execution
    from harness.execution_runtime_recovery import _launcher_call

    _, _, controller, initial, package, execution, envelope = operations
    marker = Path(execution["handoff_path"]).parent / "runtime-binding.json"
    origin = _launcher_call(
        package["launcher"],
        envelope["repository"],
        "origin",
        "--repository",
        envelope["repository"],
        "--platform",
        execution["host"],
        "--session",
        execution["al_session_id"],
    )
    marker.unlink()
    source = Path(initial["source"])
    if change == "execution":
        origin["transaction"]["harness_binding"]["execution_id"] = "foreign"
    elif change == "epoch":
        origin["transaction"]["harness_binding"]["epoch_id"] = "foreign-epoch"
    elif change == "run":
        origin["transaction"]["run_id"] = "foreign-run"
    elif change == "source":
        origin["source"] = str(source.parent / "foreign.json")
    elif change == "unpublished":
        origin["transaction"]["status"] = "RESERVED"
    elif change == "ambiguous":
        binding_path = Path(initial["binding_path"])
        duplicate = binding_path.parent / "ambiguous-fixture.json"
        duplicate.write_bytes(binding_path.read_bytes())
        duplicate.chmod(0o600)
    else:
        source.write_text("{}")
    previous = source.read_bytes()
    monkeypatch.setattr("harness.execution_runtime_recovery._launcher_call", lambda *a: origin)
    original = service.run_owned

    def no_second_start(argv, **kwargs):
        assert "start" not in argv, "foreign or corrupt initialized runtime must not be restarted"
        return original(argv, **kwargs)

    monkeypatch.setattr(service, "run_owned", no_second_start)
    with pytest.raises(ExecutionConflict):
        initialize_execution(controller, execution, package, envelope)
    assert not marker.exists()
    assert source.read_bytes() == previous


def test_selected_runtime_missing_protected_transaction_is_not_a_first_start(
    operations, monkeypatch
):
    import harness.execution_initialization as service

    _, _, controller, initial, package, execution, envelope = operations
    marker = Path(execution["handoff_path"]).parent / "runtime-binding.json"
    expected = marker.read_bytes()
    source = Path(initial["source"])
    previous = source.read_bytes()
    Path(initial["binding_path"]).unlink()

    def refuse(*args, **kwargs):
        pytest.fail("missing selected authority must not invoke first-start or origin recovery")

    monkeypatch.setattr(service, "run_owned", refuse)
    monkeypatch.setattr("harness.execution_runtime_recovery._launcher_call", refuse)
    with pytest.raises(ExecutionConflict, match="selected durable runtime is unavailable"):
        service.initialize_execution(controller, execution, package, envelope)
    assert marker.read_bytes() == expected
    assert source.read_bytes() == previous


@pytest.mark.parametrize(
    "reason", ["issuer permission denied", "corrupt registry", "unsupported option"]
)
def test_origin_call_refusal_never_admits_first_start(operations, monkeypatch, reason):
    import harness.execution_initialization as service

    _, _, controller, initial, package, execution, envelope = operations
    marker = Path(execution["handoff_path"]).parent / "runtime-binding.json"
    marker.unlink()
    previous = Path(initial["source"]).read_bytes()

    def refused(*args):
        raise ExecutionConflict(reason)

    def forbidden(*args, **kwargs):
        pytest.fail("issuer refusal must not fall through to first start")

    monkeypatch.setattr("harness.execution_runtime_recovery._launcher_call", refused)
    monkeypatch.setattr(service, "run_owned", forbidden)
    with pytest.raises(ExecutionConflict, match="upgrade AL"):
        service.initialize_execution(controller, execution, package, envelope)
    assert not marker.exists()
    assert Path(initial["source"]).read_bytes() == previous


@pytest.mark.parametrize(
    "field", ["repository", "platform", "session_id", "schema_version", "extra"]
)
def test_first_start_requires_exact_closed_absence_subject(operations, monkeypatch, field):
    import harness.execution_initialization as service

    _, _, controller, initial, package, execution, envelope = operations
    marker = Path(execution["handoff_path"]).parent / "runtime-binding.json"
    marker.unlink()
    observed = {
        "schema_version": 1,
        "kind": "runtime-origin-absence-v1",
        "state": "ABSENT",
        "repository": envelope["repository"],
        "platform": execution["host"],
        "session_id": execution["al_session_id"],
    }
    observed[field] = True if field == "schema_version" else "foreign"
    monkeypatch.setattr("harness.execution_runtime_recovery._launcher_call", lambda *a: observed)

    def forbidden(*args, **kwargs):
        pytest.fail("foreign absence observation must not admit first start")

    monkeypatch.setattr(service, "run_owned", forbidden)
    with pytest.raises(ExecutionConflict, match="absence observation differs"):
        service.initialize_execution(controller, execution, package, envelope)
    assert not marker.exists()
