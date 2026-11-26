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
from contextlib import contextmanager
from pathlib import Path

EXECUTION_POLICY = {"version": 2, "default_stdin": "closed-pipe"}


def selected_execution_policy(value):
    if (
        type(value) is not dict
        or value != EXECUTION_POLICY
        or type(value.get("version")) is not int
    ):
        raise ValueError("unsupported check execution policy")
    return dict(EXECUTION_POLICY)


@contextmanager
def check_input(policy):
    if policy is None:
        yield subprocess.DEVNULL
        return
    reader, writer = os.pipe()
    try:
        os.close(writer)
        writer = None
        yield reader
    finally:
        try:
            if writer is not None:
                os.close(writer)
        finally:
            os.close(reader)


def exact(stream, size):
    data = bytearray()
    while len(data) < size:
        raw = stream.recv(size - len(data))
        if not raw:
            raise EOFError
        data.extend(raw)
    return bytes(data)


class CheckChannel:
    def __init__(
        self,
        context_path,
        validate,
        *,
        storage=None,
        policy_attempt=None,
        ownership=None,
        validate_allocation=None,
    ):
        self.server, self.client = socket.socketpair()
        self.server.settimeout(1)
        self.context_path, self.validate = context_path, validate
        self.storage = storage
        self.records = {}
        self.active_handle = None
        self.closed = threading.Event()
        self.requesting_handle = None
        self.policy_attempt = policy_attempt
        self.ownership = ownership
        self.validate_allocation = validate_allocation
        self.owner_ref = None
        self.owner_ready = threading.Event()
        from harness.reliability.process_context import CURRENT

        self.process_context = CURRENT.get()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    @property
    def descriptor(self):
        return self.client.fileno()

    def spawned(self):
        self.client.close()

    def bind_owner(self, process, observer):
        try:
            self.owner_ref = self.ownership.bind_owner(process, self.process_context, observer)
        finally:
            self.owner_ready.set()

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
        if record is not None and record["process"] is not None and not record["settled"]:
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
        if process is None:
            return None
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
        if record["completion"] is not None:
            if not record.get("policy_observed"):
                record["policy_observed"] = True
                if record.get("policy"):
                    record["policy"].finish(
                        closed=record["completion"]["complete"],
                        outcome="returned" if code == 0 else "failed",
                    )
                if record.get("observer"):
                    record["observer"].notify(
                        "process.exited", returncode=code, cleanup_status="observed"
                    )
            if self.storage is not None:
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
        self.requesting_handle = None
        if action == "capabilities":
            if (
                set(value) != {"action", "schema_version"}
                or type(value["schema_version"]) is not int
                or value["schema_version"] != 1
                or descriptors
            ):
                raise ValueError("invalid check capabilities request")
            self.validate()
            return {"schema_version": 1, "execution_policy": dict(EXECUTION_POLICY)}
        policy = None
        if action == "start" and "execution_policy" in value:
            policy = selected_execution_policy(value["execution_policy"])
        self.requesting_handle = value.get("handle")
        if action in {
            "start",
            "poll",
            "completion",
            "cancel",
            "acknowledge_publication",
            "register_owner",
            "observe_owner",
            "same_owner",
        }:
            self.validate()
        if action in {"register_owner", "observe_owner", "same_owner"}:
            if (
                self.ownership is None
                or not self.owner_ready.wait(timeout=5)
                or self.owner_ref is None
                or descriptors
            ):
                raise ValueError("check owner observation unavailable")
            if action == "register_owner":
                binding, coordinator = value.get("binding"), value.get("coordinator")
                self.validate_allocation(binding, coordinator=coordinator)
                return self.ownership.register(self.owner_ref, binding, coordinator=coordinator)
            reference = value.get("reference")
            if not isinstance(reference, dict):
                raise ValueError("invalid check owner reference")
            binding = {k: reference.get(k) for k in ("run", "id", "reservation")}
            entry = self.validate_allocation(binding, coordinator=None)
            if entry.get("process_observation") != reference:
                raise ValueError("check observation reference changed")
            observed = self.ownership.observe(reference, self.process_context)
            if action == "same_owner":
                return {
                    "same_owner": observed["owner"] == "live"
                    and reference.get("owner_ref") == self.owner_ref
                }
            return observed
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
            self.records[identifier] = record
            self.active_handle = identifier
            if self.ownership is not None:
                if not self.owner_ready.wait(timeout=5) or self.owner_ref is None:
                    raise ValueError("original check owner unavailable")
                reference = value.get("process_observation")
                if not isinstance(reference, dict):
                    raise ValueError("registered check allocation required")
                entry = self.validate_allocation(
                    {k: reference.get(k) for k in ("run", "id", "reservation")},
                    coordinator=False,
                )
                if entry.get("process_observation") != reference:
                    raise ValueError("check allocation reference changed")
                self.ownership.attach(
                    reference, self.owner_ref, self.process_context, identifier, record
                )
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
            record["policy"] = None
            record["observer"] = None
            try:
                if self.policy_attempt:
                    from harness.policy.processes import ProcessHooks
                    from harness.reliability.process_store import pending_process

                    observer = pending_process(
                        self.policy_attempt.intent.principal_id,
                        self.policy_attempt.intent.task_id,
                        self.policy_attempt.intent.revision,
                        context=self.process_context,
                    )
                    record["observer"] = observer
                    hooks = ProcessHooks(self.policy_attempt)
                    record["policy"] = hooks
                    bound = hooks.prepare(identifier, observer)
                    if bound is not None:
                        record["deadline"] = min(record["deadline"], bound)
                if time.monotonic() >= record["deadline"]:
                    raise ValueError("BUDGET_EXHAUSTED")
                with check_input(policy) as stdin:
                    record["process"] = subprocess.Popen(
                        [sys.executable, "-I", "-B", "-c", program, str(process_context), *argv],
                        cwd=cwd,
                        env=env,
                        stdin=stdin,
                        stdout=descriptors[0],
                        stderr=descriptors[1],
                        close_fds=True,
                        start_new_session=True,
                    )
                if record["policy"]:
                    record["policy"].spawned(record["process"])
                if record["observer"]:
                    record["observer"].notify("process.started")
            except BaseException:
                if record["process"] is None:
                    release_process_temporary(record["process_temporary"])
                    if record["policy"]:
                        record["policy"].finish(closed=False, outcome="failed")
                else:
                    self.stop(identifier)
                raise
            result = {"handle": identifier, "pid": record["process"].pid}
            if policy is not None:
                result["execution_policy"] = policy
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
                    if (
                        self.policy_attempt
                        and self.process is not None
                        and not self.records[self.active_handle]["settled"]
                    ):
                        self.server.settimeout(max(0.001, min(1, self.deadline - time.monotonic())))
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
                except (ValueError, OSError, KeyError, TypeError):
                    if self.requesting_handle in self.records:
                        self.stop(self.requesting_handle)
                    result = {"error": "check request unavailable or binding changed"}
                finally:
                    for fd in descriptors:
                        os.close(fd)
                raw = json.dumps(result).encode()
                self.server.sendall(struct.pack("!I", len(raw)) + raw)
        except (EOFError, OSError):
            pass
        finally:
            for handle in tuple(self.records):
                try:
                    self.stop(handle)
                    self.observe(handle)
                except (OSError, ValueError, subprocess.SubprocessError):
                    pass
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
        for handle in tuple(self.records):
            self.observe(handle)
        if self.failure:
            raise ValueError(self.failure)
