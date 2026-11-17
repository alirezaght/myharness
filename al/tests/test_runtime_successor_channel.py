"""Real socket framing, replay and descriptor isolation for prepared initialization."""

import os
import socket
import struct
import threading

import pytest

from al.installer.runtime_successor_channel import (
    InitializationChannel,
    InitializationClient,
    receive,
    send,
)


def message(sequence=1, operation="validate-start", **changes):
    return {
        "version": 1,
        "attempt_nonce": "attempt",
        "preparation_sha256": "a" * 64,
        "sequence": sequence,
        "operation": operation,
        "payload": {"digest": "b" * 64},
    } | changes


def server():
    parent, child = socket.socketpair()
    observed = []

    def callback(payload):
        observed.append(payload)
        return {"reserved": True}

    return InitializationChannel(parent, "attempt", "a" * 64, callback, callback), child, observed


def test_real_channel_and_noninheritable_client():
    channel, child, observed = server()
    errors = []

    def service():
        try:
            channel.service_one()
            channel.service_one()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=service)
    thread.start()
    client = InitializationClient(child.detach(), "attempt", "a" * 64)
    try:
        assert not os.get_inheritable(client.sock.fileno())
        assert client.request("validate-start", {"digest": "b" * 64}) == {"reserved": True}
        assert client.request("initialize", {"supplied": True}) == {"reserved": True}
    finally:
        client.sock.close()
        thread.join(timeout=3)
        channel.close()
    assert not errors and not thread.is_alive() and len(observed) == 2


def test_identical_retry_is_cached_but_altered_replay_is_refused():
    channel, child, observed = server()
    try:
        first = None
        for _ in range(2):
            send(child, message())
            channel.service_one()
            result = receive(child)
            if first is None:
                first = result
            assert result == first
        assert len(observed) == 1
        send(child, message(payload={"digest": "foreign"}))
        with pytest.raises(ValueError, match="protocol refused"):
            channel.service_one()
    finally:
        child.close()
        channel.close()


@pytest.mark.parametrize(
    "incoming",
    [
        message(sequence=2),
        message(operation="activate"),
        message(attempt_nonce="foreign"),
        message(preparation_sha256="foreign"),
        message(extra="path"),
        message(sequence=True),
    ],
)
def test_closed_request_shapes_refuse(incoming):
    channel, child, observed = server()
    try:
        send(child, incoming)
        with pytest.raises(ValueError, match="protocol refused"):
            channel.service_one()
        assert observed == []
    finally:
        child.close()
        channel.close()


def test_oversized_frame_and_eof_refuse():
    for raw in [struct.pack("!I", 1024 * 1024 + 1), b"\x00\x00"]:
        parent, child = socket.socketpair()
        try:
            child.sendall(raw)
            child.close()
            with pytest.raises((ValueError, EOFError)):
                receive(parent)
        finally:
            parent.close()
            child.close()


def test_safe_preparation_error_has_closed_schema_and_bounded_text():
    from al.installer.runtime_successor_channel import preparation_error

    value = preparation_error("original-request", 1, "preparation", "REFUSED")
    assert set(value) == {"version", "sequence", "phase", "payload"}
    assert set(value["payload"]) == {"request_id", "stage", "code", "diagnostic"}
    assert len(value["payload"]["diagnostic"]) <= 128
    with pytest.raises(ValueError):
        preparation_error("original-request", 3, "raw secret", "unknown")


@pytest.mark.parametrize(
    "message,code",
    [
        ("successor process ownership unavailable", "OWNERSHIP_ARGV_UNAVAILABLE"),
        ("task process cwd unavailable", "OWNERSHIP_CWD_UNAVAILABLE"),
    ],
)
def test_ownership_error_has_static_actionable_reason(message, code):
    from al.installer.runtime_successor_channel import classify_preparation_error, preparation_error

    assert classify_preparation_error(ValueError(message)) == code
    value = preparation_error("original-request", 1, "preparation", code)
    assert set(value["payload"]) == {"request_id", "stage", "code", "diagnostic"}
    diagnostic = value["payload"]["diagnostic"]
    assert len(diagnostic) <= 128
    assert "retry the same protected request" in diagnostic
    assert "No settlement is implied" in diagnostic
    assert classify_preparation_error(ValueError("secret unrecognized details")) == "REFUSED"
