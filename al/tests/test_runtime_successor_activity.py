"""Task ownership and immutable protected payload selection regression cases."""

import os
import sys
from types import SimpleNamespace

import pytest

from al.installer import runtime_successor_census as census
from al.installer import runtime_successor_payload as payload


@pytest.fixture
def observed(tmp_path, monkeypatch):
    current = os.getpid()
    task = {
        "repository_identity": {"root": str(tmp_path)},
        "session_id": "task-session",
        "run_id": "task-run",
        "platform": "codex",
    }
    state = {"writers": {}, "evidence": {"entries": []}, "integration": {}}
    identities = {pid: {"pid": pid, "start": [pid], "zombie": False} for pid in (current, 45678)}
    argv = {current: ["authenticated-coordinator"], 45678: ["desktop-helper"]}
    cwd = {current: tmp_path, 45678: tmp_path}
    monkeypatch.setitem(
        sys.modules, "_al_validator_context", SimpleNamespace(request={"mode": "snapshot"})
    )
    monkeypatch.setattr(census, "identity", lambda pid: identities.get(pid))
    monkeypatch.setattr(census, "process_arguments", lambda pid: argv[pid])
    monkeypatch.setattr(census, "process_cwd", lambda pid: cwd[pid])
    monkeypatch.setattr(
        census.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout="".join(f"{pid} {os.getuid()}\n" for pid in identities).encode()
        ),
    )
    return tmp_path, task, state, argv, cwd, identities


def test_ambient_working_directory_has_no_task_authority(observed):
    root, task, state, *_ = observed
    assert (
        census.require_quiescent(root, transaction=task, state=state)["kind"]
        == "kernel-task-census-v2"
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ["python", "-c", "sys.modules['_al_run_pin_context']=context;task-session"],
        ["alctl", "run", "--session", "task-session"],
        ["python", "al_state.py", "--session=task-session"],
    ],
)
def test_managed_and_queued_owners_outside_repository_refuse(observed, arguments):
    root, task, state, argv, cwd, _ = observed
    argv[45678] = arguments
    cwd[45678] = root.parent
    with pytest.raises(ValueError, match="managed operation"):
        census.require_quiescent(root, transaction=task, state=state)


@pytest.mark.parametrize("fact", ["writer", "evidence", "agent", "outbox"])
def test_operation_records_are_required_even_without_native_pid(observed, fact):
    root, task, state, *_ = observed
    if fact == "writer":
        state["writers"] = {"writer": {"state": "ACTIVE"}}
    elif fact == "evidence":
        state["evidence"]["entries"] = [{"id": "E1", "status": "RUNNING"}]
    elif fact == "agent":
        state["integration"]["agents"] = {"agent": {"status": "Running"}}
    else:
        state["integration"]["pending"] = {"operation": "queued"}
    with pytest.raises(ValueError, match="unsettled"):
        census.require_quiescent(root, transaction=task, state=state)


def test_unstable_process_identity_refuses(observed, monkeypatch):
    root, task, state, _, _, identities = observed
    original = census.process_arguments

    def moved(pid):
        result = original(pid)
        if pid == 45678:
            identities[pid] = {**identities[pid], "start": [999]}
        return result

    monkeypatch.setattr(census, "process_arguments", moved)
    with pytest.raises(ValueError, match="identity changed"):
        census.require_quiescent(root, transaction=task, state=state)


def test_payload_resolves_original_pin_after_coordinator_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(payload, "storage", lambda: tmp_path)
    old = {
        "manifest": {
            "platform": "codex",
            "closure_digest": "old",
            "interpreter": {"sha256": "same"},
        }
    }
    new = {
        "manifest": {
            "platform": "codex",
            "closure_digest": "new",
            "interpreter": {"sha256": "same"},
        }
    }
    monkeypatch.setattr(payload, "verify_pin", lambda directory, binding: old)
    predecessor = {"platform": "codex", "pin": {"original": True}}
    request = {"distribution_identity": "old"}
    current = ({"new": True}, new)
    assert payload.requested_distribution("codex", request, current, predecessor=predecessor) == (
        predecessor["pin"],
        old,
    )
    assert payload.requested_distribution(
        "codex", request, current, predecessor=predecessor, reservation=predecessor
    ) == (predecessor["pin"], old)
    old["manifest"]["interpreter"] = {"sha256": "other"}
    with pytest.raises(ValueError, match="interpreter"):
        payload.requested_distribution("codex", request, current, predecessor=predecessor)
    old["manifest"]["interpreter"] = new["manifest"]["interpreter"]
    with pytest.raises(ValueError, match="payload differs"):
        payload.requested_distribution(
            "codex",
            {"distribution_identity": "new"},
            current,
            predecessor=predecessor,
            reservation=predecessor,
        )
    old["manifest"]["platform"] = "claude"
    with pytest.raises(ValueError, match="platform"):
        payload.requested_distribution("codex", request, current, predecessor=predecessor)
