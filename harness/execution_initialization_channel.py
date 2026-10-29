"""One supervised coordinator exchange; registry publication remains in the AL issuer."""

import array
import hashlib
import json
import os
import socket
import struct
from pathlib import Path

from harness.execution_contract import canonical, loads
from harness.execution_handoff import durable_file
from harness.owned_subprocess import run_owned

LIMIT = 1024 * 1024


def receive(sock):
    def exact(size):
        value = bytearray()
        while len(value) < size:
            raw, ancillary, flags, _ = sock.recvmsg(size - len(value), socket.CMSG_SPACE(256))
            if ancillary or flags & socket.MSG_CTRUNC:
                for level, kind, data in ancillary:
                    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                        descriptors = array.array("i")
                        descriptors.frombytes(data[: len(data) - len(data) % descriptors.itemsize])
                        for descriptor in descriptors:
                            os.close(descriptor)
                raise ValueError("prepared coordinator descriptors refused")
            if not raw:
                raise EOFError("prepared coordinator disconnected")
            value.extend(raw)
        return bytes(value)

    size = struct.unpack("!I", exact(4))[0]
    if not 0 < size <= LIMIT:
        raise ValueError("prepared coordinator frame exceeds bound")
    return loads(exact(size), max_bytes=LIMIT)


def send(sock, value):
    raw = canonical(value).encode()
    if not 0 < len(raw) <= LIMIT:
        raise ValueError("prepared coordinator frame exceeds bound")
    sock.sendall(struct.pack("!I", len(raw)) + raw)


def preparation_context(plan_ref):
    from harness.template_classification import _ordinary

    raw, _ = _ordinary(plan_ref["stored_path"])
    if hashlib.sha256(raw).hexdigest() != plan_ref["sha256"]:
        raise ValueError("issued preparation plan changed")
    plan = json.loads(raw)
    return {
        "schema_version": 1,
        "request_id": plan["request"]["request_id"],
        "preparation_identity": plan_ref["sha256"],
        "plan_ref": plan_ref,
        "repository": plan["predecessor_transaction"]["repository_identity"]["root"],
        "predecessor": plan["predecessor_transaction"],
        "successor": plan["successor_transaction"],
        "baseline": plan["source_anchors"]["baseline_head_oid"],
        "HEAD": plan["source_anchors"]["observed_head_oid"],
        "start_operation": "managed-successor-start-v1",
    }


def build_start_resources(controller, execution, plan_ref, notification):
    from harness.execution_authority import require_execution_authority
    from harness.execution_isolation import describe_prepared_start
    from harness.template_classification import prepare_successor_classification, selected_reference

    context = preparation_context(plan_ref)
    envelope = require_execution_authority(controller, execution["id"], operation="read")
    directory = (
        Path(execution["handoff_path"]).parent / "runtime-preparations" / context["request_id"]
    )
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    old_state = json.loads(Path(notification["predecessor_state_ref"]["stored_path"]).read_bytes())
    package_ref = old_state["task_package"]
    raw = Path(package_ref["stored_path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != package_ref["sha256"]:
        raise ValueError("original approved package changed")
    package_path = directory / "task-package.json"
    durable_file(package_path, raw)
    handoff_path = directory / "handoff.json"
    handoff = dict(
        envelope,
        al_session_id=context["successor"]["session_id"],
        runtime_layout="harness-session-directory-v1",
    )
    handoff_raw = canonical(handoff).encode()
    durable_file(handoff_path, handoff_raw)
    initial = {
        "pin_root": str(
            Path(notification["storage"]) / "pins" / context["predecessor"]["pin"]["pin_identity"]
        )
    }
    prior = selected_reference(execution, envelope, initial)
    grant = prepare_successor_classification(prior, context)
    resources = {
        "handoff_ref": {
            "path": str(handoff_path),
            "sha256": "sha256:" + hashlib.sha256(handoff_raw).hexdigest(),
        },
        "package_ref": {"path": str(package_path), "sha256": "sha256:" + package_ref["sha256"]},
        "classification_ref": grant,
    }
    descriptor = describe_prepared_start(context, resources, notification)
    return {"resources": resources, "isolation_descriptor": descriptor}


class InitializationChannel:
    def __init__(self, request, resources, persist, receipt_directory, *, timeout=300):
        self.request, self.resources, self.persist = request, resources, persist
        self.receipt_directory = Path(receipt_directory)
        self.timeout = timeout
        self.parent, self.child = socket.socketpair()
        self.parent.settimeout(timeout)
        self.result = None

    def serve(self, process):
        self.child.close()
        send(
            self.parent,
            {"version": 1, "sequence": 1, "operation": "prepare", "payload": self.request},
        )
        reserved = receive(self.parent)
        if (
            set(reserved) != {"version", "sequence", "phase", "payload"}
            or reserved["version"] != 1
            or reserved["sequence"] != 1
            or reserved["phase"] != "reserved"
        ):
            raise ValueError("prepared reservation response refused")
        response = self.resources(reserved["payload"])
        send(
            self.parent,
            {"version": 1, "sequence": 2, "operation": "resources", "payload": response},
        )
        completed = receive(self.parent)
        if (
            set(completed) != {"version", "sequence", "phase", "payload"}
            or completed["version"] != 1
            or completed["sequence"] != 2
            or completed["phase"] != "prepared"
        ):
            raise ValueError("prepared completion response refused")
        receipt = completed["payload"]
        raw = canonical(receipt).encode()
        self.receipt_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        durable_file(self.receipt_directory / "issuer-receipt.json", raw)
        self.persist(receipt)
        send(
            self.parent,
            {
                "version": 1,
                "sequence": 3,
                "operation": "acknowledge",
                "payload": {"receipt_sha256": hashlib.sha256(raw).hexdigest()},
            },
        )
        self.result = receipt

    def run(self, launcher, repository, platform, *, environment):
        descriptor = self.child.fileno()
        argv = [
            str(launcher),
            "runtime-successor",
            "prepare",
            "--repository",
            str(repository),
            "--platform",
            platform,
            "--request-id",
            self.request["request_id"],
            "--coordinator-fd",
            str(descriptor),
        ]
        try:
            result = run_owned(
                argv,
                cwd=repository,
                env=environment,
                timeout=self.timeout,
                pass_fds=(descriptor,),
                parent_service=self.serve,
            )
            if result.returncode or self.result is None:
                raise ValueError(
                    "managed successor preparation failed; inspect protected preparation"
                )
            return self.result
        finally:
            self.parent.close()
            self.child.close()
