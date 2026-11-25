"""Sanitized census failures preserve evidence without exposing process contents."""

import errno
import os
import sys
from types import SimpleNamespace

import pytest

from al.installer import runtime_successor_census as census
from al.installer.runtime_successor_channel import preparation_error


@pytest.fixture
def failed_observation(tmp_path, monkeypatch):
    current, subject = os.getpid(), 45678
    own = {"pid": current, "start": [1], "zombie": False}
    before = {"pid": subject, "start": [2], "zombie": False}
    after = {"value": before.copy()}
    task = {"repository_identity": {"root": str(tmp_path)}, "session_id": "session", "run_id": "run", "platform": "codex"}
    state = {"writers": {}, "evidence": {"entries": []}, "integration": {}}
    calls = []
    def identity(pid):
        if pid == current:
            return own
        if not calls:
            return before.copy()
        if isinstance(after["value"], BaseException):
            raise after["value"]
        return after["value"]
    def arguments(pid):
        if pid == current:
            return ["coordinator"]
        calls.append(pid)
        raise PermissionError(errno.EPERM, "private-argv-token", "/private/secret-file")
    monkeypatch.setitem(sys.modules, "_al_validator_context", SimpleNamespace(request={"mode": "snapshot"}))
    monkeypatch.setattr(census, "identity", identity)
    monkeypatch.setattr(census, "process_arguments", arguments)
    monkeypatch.setattr(census, "process_cwd", lambda pid: tmp_path.parent)
    monkeypatch.setattr(census.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=f"{current} {os.getuid()} 1\n{subject} {os.getuid()} 1\n".encode()))
    return tmp_path, task, state, before, after, calls


@pytest.mark.parametrize("state_name", ["live", "zombie", "changed", "unavailable"])
def test_failed_argv_records_sanitized_pid_error_and_identity_recheck(failed_observation, state_name):
    root, task, state, before, after, calls = failed_observation
    if state_name == "zombie":
        after["value"] = {**before, "zombie": True}
    elif state_name == "changed":
        after["value"] = {**before, "start": [999]}
    elif state_name == "unavailable":
        after["value"] = PermissionError(errno.EACCES, "private-recheck-detail")
    with pytest.raises(ValueError, match="successor process ownership unavailable") as captured:
        census.require_quiescent(root, transaction=task, state=state)
    assert captured.value.observation == {
        "schema_version": 1, "pid": 45678, "operation": "argv",
        "error_kind": "os_error", "error_number": errno.EPERM,
        "identity_after": state_name,
    }
    assert calls == [45678]
    assert "private" not in str(captured.value)
    assert "secret" not in repr(captured.value.observation)


def test_exit_between_enumeration_and_argv_still_requires_observed_absence(failed_observation):
    root, task, state, _, after, calls = failed_observation
    after["value"] = None
    assert census.require_quiescent(root, transaction=task, state=state)["kind"] == "kernel-task-census-v2"
    assert calls == [45678]


def test_sanitized_diagnostic_survives_closed_parent_channel(failed_observation, tmp_path):
    from harness.execution_initialization_channel import InitializationChannel, PreparationFailure, send
    root, task, state, *_ = failed_observation
    with pytest.raises(ValueError) as captured:
        census.require_quiescent(root, transaction=task, state=state)
    value = preparation_error("same-request", 1, "preparation", "OWNERSHIP_ARGV_UNAVAILABLE", observation=captured.value.observation)
    channel = InitializationChannel({"request_id": "same-request"}, None, None, tmp_path)
    try:
        send(channel.child, value)
        with pytest.raises(PreparationFailure) as received:
            channel._response(1, SimpleNamespace(poll=lambda: 1))
        assert received.value.diagnostic["observation"] == captured.value.observation
    finally:
        channel.child.close()
        channel.parent.close()


@pytest.mark.parametrize("change", [
    {"pid": True}, {"pid": -1}, {"pid": 2**40}, {"error_number": "secret"},
    {"error_number": True}, {"error_number": 65536}, {"error_kind": "private exception"},
    {"identity_after": "exited and safe"}, {"operation": "argv secret"}, {"argv": ["secret"]},
    {"schema_version": True},
])
def test_parent_refuses_arbitrary_observation_details(tmp_path, change):
    from harness.execution_initialization_channel import InitializationChannel, PreparationFailure, send
    value = preparation_error("same-request", 1, "preparation", "OWNERSHIP_ARGV_UNAVAILABLE")
    observation = {"schema_version": 1, "pid": 45678, "operation": "argv", "error_kind": "os_error", "error_number": errno.EPERM, "identity_after": "live"}
    observation.update(change)
    value["payload"]["observation"] = observation
    channel = InitializationChannel({"request_id": "same-request"}, None, None, tmp_path)
    try:
        send(channel.child, value)
        with pytest.raises(ValueError) as captured:
            channel._response(1, SimpleNamespace(poll=lambda: 1))
        assert not isinstance(captured.value, PreparationFailure)
        assert "secret" not in str(captured.value)
    finally:
        channel.child.close()
        channel.parent.close()


def test_parent_persists_sanitized_observation_when_preparation_fails(tmp_path, monkeypatch):
    import json
    from harness.execution_initialization_channel import InitializationChannel, PreparationFailure
    observation = {"schema_version": 1, "pid": 45678, "operation": "argv", "error_kind": "os_error", "error_number": errno.EPERM, "identity_after": "zombie"}
    payload = preparation_error("same-request", 1, "preparation", "OWNERSHIP_ARGV_UNAVAILABLE", observation=observation)["payload"]
    def fail(*args, **kwargs):
        raise PreparationFailure(payload)
    monkeypatch.setattr("harness.execution_initialization_channel.run_owned", fail)
    channel = InitializationChannel({"request_id": "same-request"}, None, None, tmp_path)
    with pytest.raises(PreparationFailure) as captured:
        channel.run("synthetic-launcher", tmp_path, "codex", environment={})
    paths = list(tmp_path.glob("attempt-diagnostic-*.json"))
    assert len(paths) == 1
    assert json.loads(paths[0].read_text())["observation"] == observation
    assert captured.value.diagnostic["observation"] == observation
    assert captured.value.diagnostic["owned_child_exit"] is None


@pytest.mark.parametrize("code", ["REFUSED", "OWNERSHIP_UNAVAILABLE", "OWNERSHIP_CWD_UNAVAILABLE"])
def test_observation_cannot_relabel_a_different_failure(code):
    observation = {"schema_version": 1, "pid": 45678, "operation": "argv", "error_kind": "os_error", "error_number": errno.EPERM, "identity_after": "live"}
    with pytest.raises(ValueError, match="protocol refused"):
        preparation_error("same-request", 1, "preparation", code, observation=observation)
