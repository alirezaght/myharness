"""Coordinator transport never exposes generic issuer publication operations."""

import socket
import struct

import pytest

from harness.execution_initialization_channel import receive, send


def test_bounded_strict_coordinator_frames():
    parent, child = socket.socketpair()
    try:
        send(parent, {"version": 1, "payload": {"safe": True}})
        assert receive(child) == {"version": 1, "payload": {"safe": True}}
        parent.sendall(struct.pack("!I", 1024 * 1024 + 1))
        with pytest.raises(ValueError, match="bound"):
            receive(child)
    finally:
        parent.close()
        child.close()


@pytest.mark.parametrize("sequence,phase", [(1, "request"), (2, "reserved"), (2, "resources")])
def test_eof_reports_phase_and_observed_outcome_without_inventing_reservation(
    tmp_path, sequence, phase
):
    from types import SimpleNamespace

    from harness.execution_initialization_channel import InitializationChannel, PreparationFailure

    channel = InitializationChannel({"request_id": "same-request"}, None, None, tmp_path)
    channel.phase = phase
    channel.child.close()
    try:
        with pytest.raises(PreparationFailure) as captured:
            channel._response(sequence, SimpleNamespace(poll=lambda: None))
        assert captured.value.diagnostic["stage"] == phase
        assert captured.value.diagnostic["observed_exit"] is None
        assert captured.value.diagnostic["code"] == "UNEXPECTED_EOF"
        assert "may exist" in str(captured.value)
    finally:
        channel.parent.close()


@pytest.mark.parametrize("change", [{}, {"request_id": "foreign"}, {"diagnostic": "secret-token"}])
def test_closed_preparation_error_envelope(tmp_path, change):
    from types import SimpleNamespace

    from al.installer.runtime_successor_channel import preparation_error
    from harness.execution_initialization_channel import InitializationChannel, PreparationFailure

    channel = InitializationChannel({"request_id": "same-request"}, None, None, tmp_path)
    value = preparation_error("same-request", 1, "preparation", "REFUSED")
    value["payload"].update(change)
    try:
        send(channel.child, value)
        expected = ValueError if change else PreparationFailure
        with pytest.raises(expected) as captured:
            channel._response(1, SimpleNamespace(poll=lambda: 1))
        if change:
            assert not isinstance(captured.value, PreparationFailure)
            assert "secret-token" not in str(captured.value)
    finally:
        channel.child.close()
        channel.parent.close()


def test_coordinator_failure_never_surfaces_raw_error_text(tmp_path, monkeypatch):
    from harness.execution_initialization_channel import InitializationChannel, PreparationFailure

    def failure(*args, **kwargs):
        raise ValueError("synthetic-secret-not-for-channel")

    monkeypatch.setattr("harness.execution_initialization_channel.run_owned", failure)
    channel = InitializationChannel({"request_id": "original-request"}, None, None, tmp_path)
    with pytest.raises(PreparationFailure) as captured:
        channel.run("synthetic-launcher", tmp_path, "codex", environment={})
    assert "synthetic-secret" not in str(captured.value)
    assert captured.value.diagnostic["owned_child_exit"] is None
    assert captured.value.diagnostic["request_id"] == "original-request"
    assert len(list(tmp_path.glob("attempt-diagnostic-*.json"))) == 1
