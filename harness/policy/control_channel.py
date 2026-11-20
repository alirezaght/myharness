"""Independent fixed-method helper broker for one sealed retry; owns every Git helper."""

import array
import base64
import os
import secrets
import socket
import struct
import subprocess
import threading
from contextlib import closing

from harness.db import connect, transaction, utcnow
from harness.execution_contract import canonical
from harness.execution_isolation import operation_popen
from harness.policy import control_git as git
from harness.policy.contracts import require

LIMIT = 12 * 1024 * 1024


def receive(stream):
    def exact(size):
        data = bytearray()
        while len(data) < size:
            raw, ancillary, flags, _ = stream.recvmsg(size - len(data), socket.CMSG_SPACE(256))
            if ancillary or flags & socket.MSG_CTRUNC:
                for level, kind, value in ancillary:
                    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                        descriptors = array.array("i")
                        descriptors.frombytes(
                            value[: len(value) - len(value) % descriptors.itemsize]
                        )
                        for descriptor in descriptors:
                            os.close(descriptor)
                raise ValueError("control descriptor refused")
            if not raw:
                raise EOFError()
            data.extend(raw)
        return bytes(data)

    size = struct.unpack("!I", exact(4))[0]
    require(0 < size <= LIMIT)
    from harness.execution_contract import loads

    return loads(exact(size), max_bytes=LIMIT)


def send(stream, value):
    raw = canonical(value).encode()
    require(len(raw) <= LIMIT)
    stream.sendall(struct.pack("!I", len(raw)) + raw)


class ControlChannel:
    def __init__(self, attempt, profile):
        self.attempt, self.profile = attempt, profile
        profile.channel = self
        self.nonce = secrets.token_hex(32)
        self.server, self.client = socket.socketpair()
        self.server.settimeout(300)
        self.completed = False
        self.exit_code = None
        self.phase = None
        self.sequence = 0
        self.helpers = 0
        self.bytes = 0
        self.current_process = None
        self.closed = False
        self.isolation = profile.isolation
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    @property
    def descriptor(self):
        return self.client.fileno()

    def spawned(self):
        self.client.close()

    def run_helper(self, method, args, input_bytes=b""):
        require(
            self.helpers < 1024 and self.bytes < 64 * 1024 * 1024,
            "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
        )
        require(
            git.file_state(self.profile.git) == self.profile.git_state,
            "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
        )
        require(
            git.configuration(self.profile.repository) == self.profile.config,
            "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
        )
        require(
            git.file_state(self.isolation.policy) == self.profile.isolation_state,
            "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
        )
        self.helpers += 1
        identifier = secrets.token_hex(16)
        with closing(connect(self.attempt.operation.database)) as conn, transaction(conn):
            conn.execute(
                "INSERT INTO policy_control_helpers(id,attempt_id,method,state,created_at) "
                "VALUES (?,?,?,'pending',?)",
                (identifier, self.attempt.intent.id, method, utcnow()),
            )
        process = None
        closed = False
        try:
            process = operation_popen(
                self.isolation,
                [self.profile.git, *git.OPTIONS, *args],
                cwd=self.profile.repository,
                env=git.environment(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            self.current_process = process
            with closing(connect(self.attempt.operation.database)) as conn, transaction(conn):
                conn.execute(
                    "UPDATE policy_control_helpers SET pid=?,state='started' WHERE id=?",
                    (process.pid, identifier),
                )
            raw, code = git.capture(process, input_bytes)
            require(
                git.file_state(self.isolation.policy) == self.profile.isolation_state
                and git.configuration(self.profile.repository) == self.profile.config,
                "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
            )
            closed = True
            self.bytes += len(raw)
            return raw, code
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            self.current_process = None
            with closing(connect(self.attempt.operation.database)) as conn, transaction(conn):
                conn.execute(
                    "UPDATE policy_control_helpers SET state=?,exit_code=?,observed_bytes=? "
                    "WHERE id=?",
                    (
                        "closed" if closed else "unknown",
                        process.returncode if process else None,
                        len(raw) if closed else None,
                        identifier,
                    ),
                )

    def blobs(self, values):
        require(set(values) == {"oids", "max_bytes", "file_bytes"})
        oids = values["oids"]
        require(isinstance(oids, list) and len(oids) <= 4096)
        require(
            all(
                isinstance(oid, str)
                and __import__("re").fullmatch("[0-9a-f]{40}|[0-9a-f]{64}", oid)
                for oid in oids
            )
        )
        require(type(values["max_bytes"]) is int and type(values["file_bytes"]) is int)
        maximum = min(values["max_bytes"], git.BYTE_LIMIT)
        per_file = min(values["file_bytes"], 2 * 1024 * 1024)
        require(0 <= maximum <= git.BYTE_LIMIT and 0 <= per_file <= 2 * 1024 * 1024)
        raw, code = self.run_helper(
            "read_blobs", ["cat-file", "--batch"], "".join(oid + "\n" for oid in oids).encode()
        )
        require(code == 0)
        result = {}
        offset = 0
        total = 0
        for oid in oids:
            end = raw.find(b"\n", offset)
            require(offset < end < offset + 256)
            header = raw[offset:end].split()
            require(
                len(header) == 3 and header[:2] == [oid.encode(), b"blob"] and header[2].isdigit()
            )
            size = int(header[2])
            total += size
            require(size <= per_file and total <= maximum)
            offset = end + 1
            value = raw[offset : offset + size]
            require(len(value) == size and raw[offset + size : offset + size + 1] == b"\n")
            import hashlib

            algorithm = "sha1" if len(oid) == 40 else "sha256"
            require(
                hashlib.new(algorithm, b"blob " + str(size).encode() + b"\0" + value).hexdigest()
                == oid
            )
            result[oid] = base64.b64encode(value).decode()
            offset += size + 1
        require(offset == len(raw))
        return result

    def request(self, request):
        require(
            isinstance(request, dict)
            and set(request) == {"protocol", "nonce", "sequence", "method", "operands"}
        )
        require(request["protocol"] == "sealed-al-control-v1" and request["nonce"] == self.nonce)
        method, values = request["method"], request["operands"]
        require(isinstance(values, dict))
        if method == "activate" and values.get("stage") == "pin" and self.phase == "launcher":
            self.sequence = 0
        require(request["sequence"] == self.sequence + 1 and type(request["sequence"]) is int)
        self.sequence = request["sequence"]
        require(not self.completed)
        if method == "activate":
            require(
                set(values)
                == {"stage", "profile", "repository", "platform", "session", "argv", "pin"}
            )
            stage = values["stage"]
            require((self.phase, stage) in {(None, "launcher"), ("launcher", "pin")})
            require(
                values["profile"] == self.profile.method
                and values["repository"] == self.profile.repository
                and values["platform"] == self.profile.platform
                and values["session"] == self.attempt.operation.envelope["al_session_id"]
                and values["argv"] == list(self.profile.args)
                and values["pin"] == (self.profile.pin if stage == "pin" else None),
                "AUTHORITY_STALE",
            )
            if stage == "pin":
                for form, command in (
                    ("preflight_index", ["ls-files", "--stage", "-z"]),
                    ("preflight_tree", ["ls-tree", "-r", "-z", "HEAD"]),
                ):
                    raw, code = self.run_helper(form, command)
                    require(
                        code == 0
                        and not any(row.startswith(b"160000 ") for row in raw.split(b"\0")),
                        "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
                    )
            self.phase = stage
            return {
                "protocol": "sealed-al-control-v1",
                "profile": self.profile.method,
                "repository": self.profile.repository,
                "interpreter": self.profile.interpreter,
                "pin_identity": self.profile.pin,
            }
        require(self.phase is not None)
        if method == "writer-binding":
            require(
                self.phase == "pin"
                and self.profile.method == "sealed-al-writer-finish-v1"
                and values == self.profile.writer_binding,
                "AUTHORITY_STALE",
            )
            self.writer_verified = True
            return {"bound": True}
        if method == "complete":
            require(
                self.phase == "pin"
                and set(values) == {"exit_code"}
                and type(values["exit_code"]) is int
                and 0 <= values["exit_code"] <= 255
            )
            require(
                self.profile.method != "sealed-al-writer-finish-v1"
                or getattr(self, "writer_verified", False)
                or values["exit_code"] != 0
            )
            with closing(connect(self.attempt.operation.database)) as conn, transaction(conn):
                require(
                    not conn.execute(
                        "SELECT 1 FROM policy_control_helpers WHERE attempt_id=? "
                        "AND state!='closed' LIMIT 1",
                        (self.attempt.intent.id,),
                    ).fetchone()
                )
                receipt = secrets.token_hex(16)
                conn.execute(
                    "INSERT INTO policy_control_receipts VALUES (?,?,?,?,?,?,'prepared')",
                    (
                        receipt,
                        self.attempt.intent.id,
                        canonical(self.profile.binding),
                        self.helpers,
                        values["exit_code"],
                        utcnow(),
                    ),
                )
            self.completed = True
            self.exit_code = values["exit_code"]
            self.profile.receipt = receipt
            return {"receipt": receipt}
        if method == "read_blobs":
            return self.blobs(values)
        args = git.argv(method, values)
        raw, code = self.run_helper(method, args)
        return {"bytes": base64.b64encode(raw).decode(), "returncode": code}

    def serve(self):
        try:
            while not self.completed:
                request = receive(self.server)
                result = self.request(request)
                send(self.server, {"sequence": request["sequence"], "result": result})
        except (ValueError, OSError, EOFError, subprocess.SubprocessError):
            pass
        finally:
            self.server.close()

    def close(self):
        self.client.close()
        try:
            self.server.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        process = self.current_process
        if process is not None and process.poll() is None:
            process.kill()
        self.thread.join(timeout=12)
        self.closed = not self.thread.is_alive()
