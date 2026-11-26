# ruff: noqa: F811
"""Real warm-service and control-channel boundary proofs for sealed operations."""

import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.execution_hosts import al_argv
from harness.policy.control_channel import ControlChannel, receive, send
from tests.test_policy_admission import configure, document
from tests.test_policy_dispatch import operations, protected_policy_runtime  # noqa: F401
from tests.test_policy_retry_control import (
    active_writer_fixture,
    retry,
    setup_retry,
)


def _public(r, *arguments, existing_run=False):
    command = al_argv(
        r.operation.package["launcher"],
        r.operation.envelope["host"],
        r.operation.envelope["repository"],
        r.operation.envelope["al_session_id"],
        *arguments,
        existing_run=existing_run,
    )
    return subprocess.run(
        command,
        cwd=r.operation.envelope["repository"],
        env=dict(os.environ),
        capture_output=True,
        timeout=45,
    )


def _service_status(r):
    result = subprocess.run(
        [
            r.operation.package["launcher"],
            "service",
            "status",
            "--repository",
            r.operation.envelope["repository"],
        ],
        cwd=r.operation.envelope["repository"],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]


def _wait_service(r, pin_identity, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = _service_status(r)
        matches = [row for row in rows if row.get("pin_identity") == pin_identity]
        if len(matches) == 1 and matches[0].get("pid", 0) > 0:
            return matches[0]
        time.sleep(0.1)
    pytest.fail("the exact private AL service did not become ready")


def _service_identity(pid):
    from al.installer.activity_process import identity

    observed = identity(pid)
    assert observed is not None and not observed["zombie"], (
        "owned service exited before identity capture"
    )
    assert observed["pgid"] == observed["sid"] == pid, "service does not own its private session"
    return observed


def _wait_owned_service_exit(expected, timeout=15):
    from al.installer.activity_process import identity
    from al.installer.owned_process import group_members

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = identity(expected["pid"])
        if current is not None:
            assert {k: v for k, v in current.items() if k != "zombie"} == {
                k: v for k, v in expected.items() if k != "zombie"
            }, "owned service process identity changed"
        members = group_members(expected["pgid"])
        assert all(
            row["sid"] == expected["sid"] and row["start"] >= expected["start"] for row in members
        ), "foreign process found in the owned service group"
        if (current is None or current["zombie"]) and not any(not row["zombie"] for row in members):
            return
        time.sleep(0.05)
    pytest.fail(
        "owned service or its command/watch children did not settle within the bounded wait"
    )


def _stop_owned_service(r, pin_identity, ownership):
    launcher = r.operation.package["launcher"]
    repository = r.operation.envelope["repository"]
    expected = ownership.get("identity")
    if expected is None:
        matching = [row for row in _service_status(r) if row.get("pin_identity") == pin_identity]
        if len(matching) == 1:
            expected = _service_identity(matching[0]["pid"])
    result = subprocess.run(
        [launcher, "service", "stop", "--repository", repository],
        cwd=repository,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert expected is not None, (
        "public stop requested, but unobserved service identity leaves settlement unproven"
    )
    _wait_owned_service_exit(expected)


def _cache_state(cache_file):
    directory = cache_file.parent
    if not directory.exists():
        return {"directory": None, "entries": {}}
    info = directory.stat()
    entries = {}
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        item = path.lstat()
        entries[path.name] = {
            "bytes": path.read_bytes() if stat.S_ISREG(item.st_mode) else None,
            "inode": item.st_ino,
            "size": item.st_size,
            "mode": stat.S_IMODE(item.st_mode),
            "mtime_ns": item.st_mtime_ns,
            "ctime_ns": item.st_ctime_ns,
        }
    return {
        "directory": (
            info.st_dev,
            info.st_ino,
            stat.S_IMODE(info.st_mode),
            info.st_mtime_ns,
            info.st_ctime_ns,
        ),
        "entries": entries,
    }


def _cache_binding(r):
    from al.installer import runtime_pin_cache

    binding = json.loads(Path(r.operation.initialization["binding_path"]).read_bytes())
    pin = binding["pin"]
    pin_root = Path(r.operation.initialization["pin_root"])
    assert pin_root.name == pin["pin_identity"]
    pin_store = pin_root.parent
    path = runtime_pin_cache.record_path(pin_store, pin["pin_identity"])
    manifest = json.loads((pin_root / "manifest.json").read_bytes())
    return runtime_pin_cache, pin_store, pin, manifest, path


def _routes(r, run_storage):
    from al.installer.runtime_service_boot import services_root
    from al.installer.runtime_service_client import namespace

    root = services_root(run_storage) / "routes"
    rows = {} if not root.exists() else {path.name: path.read_bytes() for path in root.iterdir()}
    wire = namespace()
    route = wire["_al_service_route"](
        r.operation.envelope["repository"],
        r.operation.envelope["host"],
        r.operation.envelope["al_session_id"],
        os.environ,
    )
    return root, route, rows


def _control_facts(r):
    conn = r.controller.conn
    return {
        "attempts": {row[0] for row in conn.execute("SELECT id FROM policy_attempts")},
        "receipts": {row[0] for row in conn.execute("SELECT id FROM policy_control_receipts")},
        "helpers": {row[0] for row in conn.execute("SELECT id FROM policy_control_helpers")},
        "grants": {row[0] for row in conn.execute("SELECT grant_id FROM policy_retry_claims")},
    }


def _warm_service(r, ownership):
    from al.installer.runtime_service_boot import service_name

    _, pin_store, pin, _, cache_file = _cache_binding(r)
    run_storage = pin_store.parent
    assert not _service_status(r), "the disposable fixture unexpectedly already owns a service"
    ownership["started_attempted"] = True
    first = _public(r, "show", "--json")
    assert first.returncode == 0, first.stderr
    service = _wait_service(r, pin["pin_identity"])
    ownership["pid"] = service["pid"]
    ownership["identity"] = _service_identity(service["pid"])
    second = _public(r, "show", "--json")
    assert second.returncode == 0, second.stderr
    after = _wait_service(r, pin["pin_identity"])
    assert after["pid"] == service["pid"]
    assert after["commands_served"] > service["commands_served"]
    assert service["directory"].endswith(
        service_name(service["common_dir"], pin["pin_identity"], service["launcher_identity"])
    )
    route_root, route, route_rows = _routes(r, run_storage)
    assert route in route_rows and route_rows[route] == Path(service["directory"]).name.encode()
    return service, pin_store, pin, cache_file, run_storage, route_root, route, route_rows


@pytest.mark.parametrize("operations", ["codex", "claude"], indirect=True)
@pytest.mark.parametrize("action", ["retry", "writer-finish"])
@pytest.mark.parametrize("cache_mode", ["valid", "empty"])
def test_sealed_control_runs_with_warm_service_without_cache_or_route_changes(
    protected_policy_runtime, monkeypatch, action, cache_mode
):
    monkeypatch.setenv("AL_SERVICE", "1")
    r = protected_policy_runtime
    _, pin_store, pin, _, _ = _cache_binding(r)
    ownership = {"started_attempted": False, "pid": None}
    try:
        service, pin_store, pin, cache_file, run_storage, route_root, route, routes_before = (
            _warm_service(r, ownership)
        )
        owned_pid = service["pid"]
        if action == "retry":
            setup_retry(r)
        else:
            writer_principal, revision = active_writer_fixture(r)
            configure(r, document(r, maximum=20))
            patch_result = r.operation.dispatch(
                writer_principal,
                "apply_patch",
                {
                    "task_id": "finish-work",
                    "expected_revision": revision,
                    "patch": (
                        "*** Begin Patch\n*** Add File: release-owned/warm.txt\n"
                        "+warm contribution\n*** End Patch"
                    ),
                },
            )
            assert patch_result["changed"] == ["release-owned/warm.txt"]

        from al.installer import runtime_pin_cache

        if cache_mode == "empty":
            assert cache_file.parent.exists()
            assert all(path == cache_file for path in cache_file.parent.iterdir())
            cache_file.unlink(missing_ok=True)
            assert not list(cache_file.parent.iterdir())
        else:
            record = runtime_pin_cache._record(
                pin_store,
                pin,
                json.loads(
                    (Path(r.operation.initialization["pin_root"]) / "manifest.json").read_bytes()
                ),
            )
            assert cache_file.is_file() and record is not None
            assert record["quick_check"] == runtime_pin_cache.fingerprint(
                Path(r.operation.initialization["pin_root"])
            )
        cache_before = _cache_state(cache_file)
        service_before = _wait_service(r, pin["pin_identity"])
        assert service_before["pid"] == owned_pid
        assert route_root.is_dir() and _routes(r, run_storage)[2] == routes_before
        facts_before = _control_facts(r)

        if action == "retry":
            result = retry(r)
            assert result["returncode"] == 0, result
            state = json.loads(r.source.read_bytes())
            history = state["retry_history"][-1]
            attempt_row = r.controller.conn.execute(
                "SELECT * FROM policy_attempts WHERE id=?", (history["policy_attempt_id"],)
            ).fetchone()
            assert (
                attempt_row["state"] == "settled"
                and attempt_row["retry_claim"] == history["policy_grant_id"]
            )
            assert state["retries"]["al-engineer"] == 1
        else:
            result = r.operation.dispatch(
                r.principal,
                "al_command",
                {"arguments": ["writer", "finish", "--id", "owned-writer", "--outcome", "success"]},
            )
            assert result["returncode"] == 0, result
            state = json.loads(r.source.read_bytes())
            assert state["writers"]["owned-writer"]["state"] == "FINISHED"
            assert state["task_attribution"]["contributions"]

        facts_after = _control_facts(r)
        new_attempts = facts_after["attempts"] - facts_before["attempts"]
        new_receipts = facts_after["receipts"] - facts_before["receipts"]
        new_helpers = facts_after["helpers"] - facts_before["helpers"]
        assert len(new_attempts) == 1
        assert len(new_receipts) == 1
        receipt = r.controller.conn.execute(
            "SELECT * FROM policy_control_receipts WHERE id=?", (next(iter(new_receipts)),)
        ).fetchone()
        assert receipt["state"] == "closed" and receipt["helper_count"] == len(new_helpers)
        assert receipt["attempt_id"] in new_attempts
        controlled_attempt = r.controller.conn.execute(
            "SELECT state FROM policy_attempts WHERE id=?", (receipt["attempt_id"],)
        ).fetchone()
        assert controlled_attempt["state"] == "settled"
        assert new_helpers and all(
            row["state"] == "closed"
            for row in r.controller.conn.execute(
                "SELECT state FROM policy_control_helpers WHERE id IN ({})".format(
                    ",".join("?" for _ in new_helpers)
                ),
                tuple(new_helpers),
            )
        )
        if action == "retry":
            assert len(facts_after["grants"] - facts_before["grants"]) == 1
        else:
            assert not (facts_after["grants"] - facts_before["grants"])

        service_after = _wait_service(r, pin["pin_identity"])
        assert service_after["pid"] == service_before["pid"]
        assert service_after["pin_identity"] == service_before["pin_identity"]
        assert service_after["launcher_identity"] == service_before["launcher_identity"]
        assert service_after["commands_served"] == service_before["commands_served"]
        assert _routes(r, run_storage)[2] == routes_before
        assert _cache_state(cache_file) == cache_before

        served = service_after["commands_served"]
        ordinary = _public(r, "show", "--json")
        assert ordinary.returncode == 0, ordinary.stderr
        after_ordinary = _wait_service(r, pin["pin_identity"])
        assert after_ordinary["pid"] == owned_pid
        assert after_ordinary["commands_served"] > served
    finally:
        if ownership["started_attempted"]:
            _stop_owned_service(r, pin["pin_identity"], ownership)


def test_service_cleanup_waits_for_owned_child_after_listener_and_leader_exit(tmp_path):
    """An empty stop response cannot settle a real, still-live owned child."""
    from al.installer.activity_process import identity

    launcher = tmp_path / "stopped-launcher"
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(0o700)
    child_code = "import time; time.sleep(0.8)"
    parent_code = (
        "import subprocess,sys; "
        "child=subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "]); "
        "print(child.pid,flush=True); sys.stdin.read(1)"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        start_new_session=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    child = None
    try:
        child_pid = int(parent.stdout.readline())
        expected = _service_identity(parent.pid)
        child = identity(child_pid)
        assert child is not None and child["pgid"] == expected["pgid"]
        parent.stdin.close()
        parent.wait(timeout=3)
        still_running = identity(child_pid)
        assert still_running is not None and not still_running["zombie"]
        r = SimpleNamespace(
            operation=SimpleNamespace(
                package={"launcher": str(launcher)}, envelope={"repository": str(tmp_path)}
            )
        )
        _stop_owned_service(r, "private-fixture", {"identity": expected})
        settled = identity(child_pid)
        assert settled is None or settled["zombie"]
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=3)
        if child is not None:
            current = identity(child["pid"])
            if current is not None and current["start"] == child["start"] and not current["zombie"]:
                os.kill(child["pid"], signal.SIGKILL)
        if parent.stdin is not None and not parent.stdin.closed:
            parent.stdin.close()
        parent.stdout.close()


@pytest.mark.parametrize("operations", ["codex", "claude"], indirect=True)
@pytest.mark.parametrize("attack", ["wrong-nonce", "replayed-sequence", "closed-channel"])
def test_control_channel_rejects_wrong_nonce_replay_and_closed_requests(
    protected_policy_runtime, attack
):
    r = protected_policy_runtime
    setup_retry(r)
    from harness.policy.resources import prepare
    from harness.reliability import store
    from harness.reliability.process_context import dispatch_scope

    state_before = r.source.read_bytes()
    arguments = ["retry", "al-engineer", "--reason", "Inspected the genuine failure."]
    observation = store.begin(r.operation, r.principal, "al_command")
    with dispatch_scope(r.operation, observation, r.principal["thread_id"]):
        policy_attempt = prepare(
            r.operation,
            r.controller,
            r.principal,
            "al_command",
            {"arguments": arguments},
            json.loads(state_before),
        )
    profile = policy_attempt.control
    assert profile is not None and profile.method == "sealed-al-retry-v1"
    channel = ControlChannel(policy_attempt, profile)
    channel.client.settimeout(3)
    before = _control_facts(r)
    consumed = r.controller.conn.execute(
        "SELECT consumed,reserved FROM policy_pools WHERE dimension='mutating_operations'"
    ).fetchone()[:]

    def activation(sequence, nonce):
        return {
            "protocol": "sealed-al-control-v1",
            "nonce": nonce,
            "sequence": sequence,
            "method": "activate",
            "operands": {
                "stage": "launcher",
                "profile": profile.method,
                "repository": profile.repository,
                "platform": profile.platform,
                "session": r.operation.envelope["al_session_id"],
                "argv": list(profile.args),
                "pin": None,
            },
        }

    try:
        if attack == "wrong-nonce":
            send(channel.client, activation(1, "0" * len(channel.nonce)))
            with pytest.raises((EOFError, OSError)):
                receive(channel.client)
        elif attack == "replayed-sequence":
            request = activation(1, channel.nonce)
            send(channel.client, request)
            response = receive(channel.client)
            assert response["sequence"] == 1
            send(channel.client, request)
            with pytest.raises((EOFError, OSError)):
                receive(channel.client)
        else:
            channel.close()
            with pytest.raises(OSError):
                send(channel.client, activation(1, channel.nonce))
        channel.thread.join(5)
        assert not channel.thread.is_alive()
        after = _control_facts(r)
        assert after["receipts"] == before["receipts"]
        assert after["helpers"] == before["helpers"]
        assert after["grants"] == before["grants"]
        assert after["attempts"] == before["attempts"]
        assert (
            r.controller.conn.execute(
                "SELECT consumed,reserved FROM policy_pools WHERE dimension='mutating_operations'"
            ).fetchone()[:]
            == consumed
        )
        assert not (Path(r.operation.envelope["repository"]) / "svc/new.txt").exists()
        assert r.source.read_bytes() == state_before
    finally:
        channel.close()
        if not policy_attempt.started:
            policy_attempt.release_unstarted()
