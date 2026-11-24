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
    def __init__(self, context_path, validate, *, storage=None):
        self.server, self.client = socket.socketpair()
        self.server.settimeout(1)
        self.context_path, self.validate = context_path, validate
        self.storage = storage
        self.records = {}
        self.active_handle = None
        self.closed = threading.Event()
        self.requesting_handle = None
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    @property
    def descriptor(self):
        return self.client.fileno()

    def spawned(self):
        self.client.close()

    @property
    def identifier(self):
        return self.active_handle

    @property
    def process(self):
        record = self.records.get(self.active_handle)
        return record["process"] if record else None

    @property
    def deadline(self):
        record = self.records.get(self.active_handle)
        return record["deadline"] if record else None

    @property
    def failure(self):
        return next((r["failure"] for r in self.records.values() if r["failure"]), None)

    def stop(self, handle=None):
        record = self.records.get(handle if handle is not None else self.active_handle)
        if record is not None and not record["settled"]:
            process = record["process"]
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            record["settled"] = True

    def observe(self, handle=None):
        handle = self.active_handle if handle is None else handle
        record = self.records.get(handle)
        if record is None:
            return None
        process = record["process"]
        code = process.poll()
        if code is not None and record["completion"] is None:
            self.stop(handle)
            self.validate_templates(record)
            absent, errors = False, []
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                absent = True
            except OSError as error:
                errors.append(type(error).__name__)
            record["completion"] = {
                "exit": code,
                "group_absent": absent,
                "errors": errors,
                "unresolved": not absent,
                "complete": absent and not errors,
                "leader_reaped": process.returncode is not None,
                "owned_group_settled": absent and not errors,
                "descendant_scope": "group",
                "descendants_settled": None,
                "settlement_basis": "owned-process-group-only",
                "settlement_evidence": {"handle": handle, "leader_pid": process.pid},
            }
            if record["completion"]["complete"]:
                from harness.execution_isolation import release_process_temporary

                release_process_temporary(record.get("process_temporary"))
        if record["completion"] is not None and self.storage is not None:
            self.storage.completed(handle, record["completion"])
        return code

    def validate_templates(self, record):
        from harness.template_classification import ERROR, validate_reference

        try:
            validate_reference(record["classification"], root=record["classification_root"])
        except (ValueError, OSError):
            record["failure"] = ERROR
            raise ValueError(ERROR) from None

    def request(self, value, descriptors):
        action = value.get("action")
        self.requesting_handle = value.get("handle")
        if action in {"start", "poll", "completion", "cancel", "acknowledge_publication"}:
            self.validate()
        if action == "start":
            if self.process is not None and self.process.poll() is None:
                raise ValueError("check channel already owns a running process")
            self.observe()
            from harness.storage import CHECK_HANDLE_CAPACITY

            if len(self.records) >= CHECK_HANDLE_CAPACITY:
                raise ValueError("check channel retained-handle capacity exceeded")
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
            managed = None
            identifier = secrets.token_hex(24)
            self.requesting_handle = identifier
            process_context = Path(self.context_path)
            if value.get("storage") is not None:
                if self.storage is None:
                    raise ValueError("managed check storage unavailable")
                managed, argv = self.storage.begin(identifier, value["storage"], argv)
                from harness.execution_handoff import durable_file

                process_context = process_context.parent / ("check-storage-" + identifier + ".json")
                declaration_reference = self.storage.core["storage_filesystem"].publish_json(
                    process_context.with_name("check-storage-declaration-" + identifier + ".json"),
                    managed["declaration"],
                )
                sealed_managed = {k: v for k, v in managed.items() if k != "declaration"}
                sealed_managed["declaration_reference"] = declaration_reference
                durable_file(
                    process_context,
                    json.dumps(
                        context | {"managed_storage": sealed_managed},
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode(),
                )
            original = Path(context["repository"])
            classification_root = (
                original
                if target.is_relative_to(original)
                else next((p for p in (target, *target.parents) if (p / ".git").exists()), target)
            )
            record = {
                "process": None,
                "settled": False,
                "classification": context.get("classification"),
                "classification_root": classification_root,
                "failure": None,
                "completion": None,
                "deadline": time.monotonic() + 900,
            }
            self.validate_templates(record)
            root = str(Path(__file__).resolve().parent.parent)
            program = (
                "import sys;sys.path.insert(0," + repr(root) + ");"
                "from harness.execution_check_process import main;main()"
            )
            from harness.command_safety import safe_environment

            from harness.execution_check_process import PROCESS_TEMPORARY_VARIABLE
            from harness.execution_isolation import (
                create_process_temporary,
                release_process_temporary,
            )

            # Short private TMPDIR for the check (AF_UNIX socket paths); released at settlement.
            record["process_temporary"] = create_process_temporary()
            env = safe_environment() | {
                "PATH": os.environ.get("PATH", os.defpath),
                PROCESS_TEMPORARY_VARIABLE: record["process_temporary"],
            }
            try:
                record["process"] = subprocess.Popen(
                    [sys.executable, "-I", "-B", "-c", program, str(process_context), *argv],
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=descriptors[0],
                    stderr=descriptors[1],
                    close_fds=True,
                    start_new_session=True,
                )
            except BaseException:
                release_process_temporary(record["process_temporary"])
                raise
            self.records[identifier] = record
            self.active_handle = identifier
            result = {"handle": identifier, "pid": record["process"].pid}
            if managed is not None:
                self.storage.started(identifier, record["process"].pid)
                result["storage"] = managed
            return result
        handle = value.get("handle")
        record = self.records.get(handle) if isinstance(handle, str) else None
        if record is None:
            raise ValueError("owned check handle mismatch")
        if action == "cancel":
            self.stop(handle)
        if action in {"poll", "cancel"}:
            return {"returncode": self.observe(handle)}
        if action in {"completion", "acknowledge_publication"}:
            self.observe(handle)
            completion = record["completion"] or {
                "exit": None,
                "group_absent": False,
                "errors": [],
                "unresolved": True,
                "complete": False,
            }
            if action == "acknowledge_publication":
                if (
                    set(value)
                    not in (
                        {"action", "schema_version", "handle", "publication"},
                        {"action", "schema_version", "handle", "publication", "reconciliation"},
                    )
                    or type(value["schema_version"]) is not int
                    or value["schema_version"] != 1
                    or self.storage is None
                ):
                    raise ValueError("invalid managed publication acknowledgment")
                self.validate_templates(record)
                return self.storage.acknowledge(
                    handle,
                    value["publication"],
                    completion,
                    reconciliation=value.get("reconciliation"),
                )
            return completion
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
                    if self.requesting_handle in self.records:
                        self.stop(self.requesting_handle)
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
            if self.storage is not None:
                for handle in self.storage.records:
                    self.storage.retain(handle, "publication_acknowledgment_missing")
            self.server.close()

    def close(self):
        self.closed.set()
        self.client.close()
        try:
            self.server.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.thread.join(timeout=6)
        if self.thread.is_alive():
            raise ValueError("check channel settlement unavailable")
        if self.process is not None:
            self.observe()
        if self.failure:
            raise ValueError(self.failure)
