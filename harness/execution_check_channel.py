"""Private inherited channel: AL records checks, sibling sandboxes execute them."""

import array
import json
import os
import secrets
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path


def exact(stream, size):
    data = bytearray()
    while len(data) < size:
        raw = stream.recv(size - len(data))
        if not raw:
            raise EOFError
        data.extend(raw)
    return bytes(data)


class CheckChannel:
    def __init__(self, context_path, validate):
        self.server, self.client = socket.socketpair()
        self.server.settimeout(1)
        self.context_path, self.validate = context_path, validate
        self.process = None
        self.settled = False
        self.identifier = None
        self.closed = threading.Event()
        self.deadline = None
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    @property
    def descriptor(self):
        return self.client.fileno()

    def spawned(self):
        self.client.close()

    def stop(self):
        if self.process is not None and not self.settled:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait(timeout=5)
            self.settled = True

    def observe(self):
        code = self.process.poll() if self.process else None
        if code is not None:
            self.stop()
        return code

    def request(self, value, descriptors):
        action = value.get("action")
        if action in {"start", "poll"}:
            self.validate()
        if action == "start":
            if self.process is not None and self.process.poll() is None:
                raise ValueError("check channel already owns a running process")
            self.stop()
            argv, cwd = value["argv"], value["cwd"]
            if (
                not isinstance(argv, list)
                or not argv
                or len(argv) > 100
                or any(not isinstance(a, str) or len(a) > 65536 or "\x00" in a for a in argv)
            ):
                raise ValueError("bounded check argv required")
            if len(descriptors) != 2:
                raise ValueError("exactly stdout and stderr descriptors required")
            context = json.loads(Path(self.context_path).read_bytes())
            target = Path(cwd).resolve(strict=True)
            if not (
                target.is_relative_to(context["repository"])
                or target.is_relative_to(context["temporary"])
            ):
                raise ValueError("check cwd outside repository/disposable checkout")
            root = str(Path(__file__).resolve().parent.parent)
            program = (
                "import sys;sys.path.insert(0," + repr(root) + ");"
                "from harness.execution_check_process import main;main()"
            )
            from harness.command_safety import safe_environment

            env = safe_environment() | {"PATH": os.environ.get("PATH", os.defpath)}
            self.process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", program, str(self.context_path), *argv],
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=descriptors[0],
                stderr=descriptors[1],
                close_fds=True,
                start_new_session=True,
            )
            self.identifier = secrets.token_hex(24)
            self.settled = False
            self.deadline = time.monotonic() + 900
            return {"handle": self.identifier, "pid": self.process.pid}
        if value.get("handle") != self.identifier or self.process is None:
            raise ValueError("owned check handle mismatch")
        if action == "cancel":
            self.stop()
        if action in {"poll", "cancel"}:
            return {"returncode": self.observe()}
        if action == "completion":
            code = self.observe()
            absent = False
            errors = []
            if code is not None:
                try:
                    os.killpg(self.process.pid, 0)
                except ProcessLookupError:
                    absent = True
                except OSError as error:
                    errors.append(type(error).__name__)
            return {
                "exit": code,
                "group_absent": absent,
                "errors": errors,
                "unresolved": not absent,
                "complete": absent and not errors,
            }
        raise ValueError("unknown check action")

    def serve(self):
        try:
            while not self.closed.is_set():
                descriptors = []
                try:
                    header, ancillary, flags, _ = self.server.recvmsg(4, socket.CMSG_SPACE(32))
                    if not header:
                        break
                    if flags & socket.MSG_CTRUNC:
                        raise ValueError("truncated check descriptors")
                    for level, kind, raw in ancillary:
                        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                            fds = array.array("i")
                            fds.frombytes(raw[: len(raw) - len(raw) % fds.itemsize])
                            descriptors.extend(fds)
                    header += exact(self.server, 4 - len(header))
                    size = struct.unpack("!I", header)[0]
                    if not 0 < size <= 1048576:
                        raise ValueError("check request exceeds limit")
                    result = self.request(json.loads(exact(self.server, size)), descriptors)
                except TimeoutError:
                    if self.process is not None and self.process.poll() is None:
                        try:
                            self.validate()
                            if time.monotonic() > self.deadline:
                                self.stop()
                        except (ValueError, OSError, LookupError):
                            self.stop()
                    continue
                except (ValueError, OSError, KeyError, TypeError) as error:
                    self.stop()
                    result = {"error": str(error)[:2048]}
                finally:
                    for fd in descriptors:
                        os.close(fd)
                raw = json.dumps(result).encode()
                self.server.sendall(struct.pack("!I", len(raw)) + raw)
        except (EOFError, OSError):
            pass
        finally:
            self.stop()
            self.server.close()

    def close(self):
        self.closed.set()
        self.client.close()
        try:
            self.server.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.thread.join(timeout=6)
