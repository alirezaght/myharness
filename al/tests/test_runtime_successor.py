# ruff: noqa: F811
"""Genuine managed issuer and contained pinned start over disposable Harness authority."""

import sys

import json
import subprocess
from pathlib import Path

import pytest

from al.installer.runtime_successor_lineage import observe_candidate, revisions
from al.tests.runtime_run_helpers import launched  # noqa: F401
from harness.execution_bootstrap import prepared_authority_snapshot
from harness.execution_initialization_channel import InitializationChannel, build_start_resources
from tests.conftest import conn, controller, db_path, isolated_home  # noqa: F401
from tests.test_template_classification_integration import operations, registered  # noqa: F401


def prepare(registered, *, change=None, saved_request=None, after_request=None):
    operation, principal, controller, initialization, package, execution, envelope = registered[0]
    root = Path(envelope["repository"])
    old = json.loads(Path(initialization["source"]).read_bytes())
    authority = prepared_authority_snapshot(Path(execution["handoff_path"]).parent)
    result = subprocess.run(
        [package["launcher"], "runtime-successor", "distribution", "--platform", "codex"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-1000:])
    request = {
        "schema_version": 1,
        "request_id": "successor-fixture",
        "execution_id": execution["id"],
        "expected_epoch_id": "legacy-0",
        "expected_authority_generation": 0,
        "predecessor_subject": {"session_id": old["session_id"], "run_id": old["run_id"]},
        "approved_requirement_hash": revisions(old["task_package"]["sha256"], old["amendments"])[
            -1
        ],
        "expected_candidate_identity": observe_candidate(
            root, old["baseline_head"], [str(root / "app/.env.example")]
        ),
        "distribution_identity": json.loads(result.stdout)["distribution_identity"],
        "authority_snapshot_ref": authority,
        "reason": "Repair implementation continuity without changing approved requirements",
    }

    if saved_request is not None:
        request = saved_request
    if after_request is not None:
        after_request(request)

    def resources(notification):
        value = build_start_resources(controller, execution, notification["plan_ref"], notification)
        if change:
            change(value, notification)
        return value

    persisted = []
    channel = InitializationChannel(
        request,
        resources,
        persisted.append,
        Path(execution["handoff_path"]).parent / "runtime-preparations/successor-fixture",
        timeout=90,
    )
    import os

    receipt = channel.run(package["launcher"], root, "codex", environment=dict(os.environ))
    assert persisted == [receipt]
    return receipt, request


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS prepared-start containment")
def test_genuine_prepared_start_is_fresh_and_non_authoritative(registered):  # noqa: F811
    operation, _, _, initialization, package, execution, envelope = registered[0]
    original = Path(initialization["source"]).read_bytes()
    receipt, _ = prepare(registered)
    assert receipt["state"] == "PREPARED" and receipt["product_authority"] is False
    assert Path(initialization["source"]).read_bytes() == original
    state = json.loads(Path(receipt["successor_state_ref"]["stored_path"]).read_bytes())
    prior = json.loads(original)
    assert state["baseline_head"] == prior["baseline_head"]
    assert state["preexisting_changes"] == prior["preexisting_changes"]
    assert state["writers"] == {} and state["evidence"]["entries"] == []
    assert state["integration"]["tasks"] == {} and state["integration"]["deliveries"] == {}
    assert state["proof_admission"]["status"] == "pending"
    assert len(state["task_attribution"]["snapshot"]["sha256"]) == 64
    for args in [
        ("writer", "start", "--id", "forbidden", "--role", "al-engineer", "--files", "app"),
        ("preflight", "--name", "forbidden", "--check", "cmd:git"),
    ]:
        result = subprocess.run(
            [
                package["launcher"],
                "run",
                "--repository",
                envelope["repository"],
                "--platform",
                "codex",
                "--session",
                receipt["successor_subject"]["session_id"],
                "--existing-run",
                "--",
                *args,
            ],
            cwd=envelope["repository"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0 and "no product authority" in result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS prepared-start containment")
def test_changed_prepared_grants_refuse_before_capture(registered):  # noqa: F811
    def change(value, notification):
        value["isolation_descriptor"]["successor_runtime"] = notification["scratch"]

    with pytest.raises((ValueError, EOFError, OSError)):
        prepare(registered, change=change)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS prepared-start containment")
def test_prepared_response_retry_reuses_reservation_and_receipt(registered):
    first, request = prepare(registered)
    second, retried = prepare(registered)
    assert retried == request and second == first


def test_kernel_census_refuses_a_genuine_managed_session_waiter(registered, monkeypatch):
    import fcntl
    import os
    import signal
    import time

    from al.installer.runtime_successor_census import require_quiescent
    from harness.execution_hosts import al_argv

    _, _, _, initial, package, execution, envelope = registered[0]
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules, "_al_validator_context", SimpleNamespace(request={"mode": "snapshot"})
    )
    state = json.loads(Path(initial["source"]).read_bytes())
    transaction = {
        "repository_identity": {"root": envelope["repository"]},
        "session_id": execution["al_session_id"],
        "platform": "codex",
        "run_id": state["run_id"],
    }
    lock = Path(initial["source"]).parent / ("." + execution["al_session_id"] + ".lock")
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        process = subprocess.Popen(
            al_argv(
                package["launcher"],
                "codex",
                envelope["repository"],
                execution["al_session_id"],
                "status",
                existing_run=True,
            ),
            cwd=envelope["repository"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 5
            while True:
                assert process.poll() is None
                try:
                    require_quiescent(envelope["repository"], transaction=transaction, state=state)
                except ValueError as error:
                    assert "managed operation" in str(error)
                    break
                assert time.monotonic() < deadline
                time.sleep(0.05)
        finally:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS prepared-start containment")
def test_repaired_coordinator_retains_original_payload_and_reservation(registered, launched):
    source, home, repo, launcher, environment = launched
    prior = Path(registered[0][3]["source"]).read_bytes()

    def replace_coordinator(request):
        module = source / "installer/runtime_successor_payload.py"
        module.write_bytes(module.read_bytes() + b"\n# Fixture coordinator replacement.\n")
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                str(source / "alctl.py"),
                "bootstrap",
                "--source-root",
                str(source),
                "--destination",
                str(launcher),
                "--state-root",
                str(home / "installer"),
            ],
            cwd=repo,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr[-1000:]

    first, request = prepare(registered, after_request=replace_coordinator)
    second, replay = prepare(registered, saved_request=request)
    assert second == first and replay == request
    assert Path(registered[0][3]["source"]).read_bytes() == prior
    assert first["distribution_identity"] == request["distribution_identity"]
