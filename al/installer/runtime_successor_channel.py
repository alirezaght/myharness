"""Bounded data-only preparation transport; it cannot select registry operations."""

import array
import hashlib
import os
import socket
import struct
import time

from .validator_origin import canonical, decode

MAX_FRAME = 1024 * 1024
ERROR = "prepared initialization protocol refused"


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _receive(sock, size, deadline):
    result = bytearray()
    while len(result) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(ERROR)
        sock.settimeout(remaining)
        raw, ancillary, flags, _ = sock.recvmsg(size - len(result), socket.CMSG_SPACE(256))
        if ancillary or flags & socket.MSG_CTRUNC:
            for level, kind, data in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    descriptors = array.array("i")
                    descriptors.frombytes(data[: len(data) - len(data) % descriptors.itemsize])
                    for descriptor in descriptors:
                        os.close(descriptor)
            raise ValueError(ERROR)
        if not raw:
            raise EOFError(ERROR)
        result.extend(raw)
    return bytes(result)


def receive(sock, *, timeout=10):
    deadline = time.monotonic() + timeout
    length = struct.unpack("!I", _receive(sock, 4, deadline))[0]
    if not 0 < length <= MAX_FRAME:
        raise ValueError(ERROR)
    value = decode(_receive(sock, length, deadline))
    if not isinstance(value, dict):
        raise ValueError(ERROR)
    return value


def send(sock, value, *, timeout=10):
    raw = canonical(value)
    if not 0 < len(raw) <= MAX_FRAME:
        raise ValueError(ERROR)
    sock.settimeout(timeout)
    sock.sendall(struct.pack("!I", len(raw)) + raw)


class InitializationChannel:
    """Issuer-owned server. Callbacks are fixed trusted functions, never request data."""

    def __init__(self, sock, nonce, preparation_sha256, validate_start, initialize):
        self.sock = sock
        self.nonce = nonce
        self.preparation_sha256 = preparation_sha256
        self.callbacks = {"validate-start": validate_start, "initialize": initialize}
        self.responses = {}
        self.next_sequence = 1
        os.set_inheritable(sock.fileno(), False)

    def service_one(self):
        request = receive(self.sock)
        fields = {
            "version",
            "attempt_nonce",
            "preparation_sha256",
            "sequence",
            "operation",
            "payload",
        }
        if (
            set(request) != fields
            or type(request["version"]) is not int
            or request["version"] != 1
            or request["attempt_nonce"] != self.nonce
            or request["preparation_sha256"] != self.preparation_sha256
            or type(request["sequence"]) is not int
            or request["sequence"] < 1
            or request["operation"] not in self.callbacks
            or not isinstance(request["payload"], dict)
        ):
            raise ValueError(ERROR)
        sequence = request["sequence"]
        sha = fingerprint(request)
        prior = self.responses.get(sequence)
        if prior:
            if prior["request_sha256"] != sha:
                raise ValueError(ERROR)
            send(self.sock, prior)
            return request["operation"]
        expected = "validate-start" if sequence == 1 else "initialize" if sequence == 2 else None
        if sequence != self.next_sequence or request["operation"] != expected:
            raise ValueError(ERROR)
        result = self.callbacks[request["operation"]](request["payload"])
        response = {key: request[key] for key in fields - {"payload"}}
        response.update(request_sha256=sha, result=result)
        self.responses[sequence] = response
        self.next_sequence += 1
        send(self.sock, response)
        return request["operation"]

    def close(self):
        self.sock.close()


class InitializationClient:
    def __init__(self, descriptor, nonce, preparation_sha256):
        self.sock = socket.socket(fileno=descriptor)
        if self.sock.family != socket.AF_UNIX or self.sock.type != socket.SOCK_STREAM:
            raise ValueError(ERROR)
        os.set_inheritable(descriptor, False)
        self.nonce, self.preparation_sha256 = nonce, preparation_sha256
        self.sequence = 0
        self.initialization = None
        self.transaction = None

    def request(self, operation, payload):
        self.sequence += 1
        request = {
            "version": 1,
            "attempt_nonce": self.nonce,
            "preparation_sha256": self.preparation_sha256,
            "sequence": self.sequence,
            "operation": operation,
            "payload": payload,
        }
        send(self.sock, request)
        response = receive(self.sock)
        expected = {key: request[key] for key in request if key != "payload"}
        if (
            set(response) != set(expected) | {"request_sha256", "result"}
            or any(response[key] != value for key, value in expected.items())
            or response["request_sha256"] != fingerprint(request)
        ):
            raise ValueError(ERROR)
        return response["result"]
