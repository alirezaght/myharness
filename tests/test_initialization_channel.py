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
