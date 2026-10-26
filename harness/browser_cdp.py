"""Deadline-bounded Chrome DevTools pipe transport retaining protocol events."""

import json
import os
import select
import sys
import time


class CDPConnection:
    def __init__(self, read_fd, write_fd):
        self.read_fd, self.write_fd = read_fd, write_fd
        self.sequence = 0
        self.buffer = b""
        self.responses = {}
        self.events = []
        self.handler = None

    def _send(self, value, deadline):
        raw = json.dumps(value).encode() + b"\0"
        while raw:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [self.write_fd], [], remaining)[1]:
                raise TimeoutError("browser protocol write timeout")
            size = os.write(self.write_fd, raw)
            if size <= 0:
                raise OSError("browser protocol closed")
            raw = raw[size:]

    def pump(self, deadline):
        while b"\0" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.read_fd], [], [], remaining)[0]:
                raise TimeoutError("browser protocol timeout")
            block = os.read(self.read_fd, 65536)
            if not block:
                raise OSError("browser protocol disconnected")
            self.buffer += block
            if len(self.buffer) > 16 * 1024 * 1024:
                raise ValueError("browser message exceeds limit")
        raw, self.buffer = self.buffer.split(b"\0", 1)
        value = json.loads(raw)
        if "id" in value:
            self.responses[value["id"]] = value
        elif self.handler:
            self.handler(value)
        else:
            self.events.append(value)

    def call(self, method, params=None, *, deadline, session=None):
        self.sequence += 1
        identity = self.sequence
        request = {"id": identity, "method": method, "params": params or {}}
        if session:
            request["sessionId"] = session
        self._send(request, deadline)
        while identity not in self.responses:
            self.pump(deadline)
        result = self.responses.pop(identity)
        if "error" in result:
            raise ValueError("browser protocol rejected " + method)
        return result.get("result", {})


PIPE_BOOTSTRAP = """import os,resource,sys
read_fd,write_fd=int(sys.argv[1]),int(sys.argv[2])
os.dup2(read_fd,3,inheritable=True)
os.dup2(write_fd,4,inheritable=True)
os.closerange(5,resource.getrlimit(resource.RLIMIT_NOFILE)[0])
os.execv(sys.argv[3],sys.argv[3:])
"""


def pipe_command(read_fd, write_fd, argv):
    """Remap only after subprocess has closed unrelated descriptors."""
    return [sys.executable, "-I", "-c", PIPE_BOOTSTRAP, str(read_fd), str(write_fd), *argv]
