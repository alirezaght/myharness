"""One supervised coordinator exchange; registry publication remains in the AL issuer."""

import array
import hashlib
import json
import os
import secrets
import socket
import struct
from pathlib import Path

from harness.execution_contract import canonical, loads
from harness.execution_handoff import durable_file
from harness.owned_subprocess import run_owned

LIMIT = 1024 * 1024
OWNERSHIP_DIAGNOSTICS = {
    "OWNERSHIP_ARGV_UNAVAILABLE": (
        "Process arguments unavailable; restore complete host observation "
        "and retry the same protected request. No settlement is implied."
    ),
    "OWNERSHIP_CWD_UNAVAILABLE": (
        "Process cwd unavailable; restore complete host observation "
        "and retry the same protected request. No settlement is implied."
    ),
}


def _valid_process_observation(value, code):
    """Closed metadata only; never accept exception text, argv or filesystem paths."""
    return (
        isinstance(value, dict)
        and set(value)
        == {"schema_version", "pid", "operation", "error_kind", "error_number", "identity_after"}
        and type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and type(value["pid"]) is int
        and 0 < value["pid"] < 2**31
        and isinstance(value["operation"], str)
        and {"argv": "OWNERSHIP_ARGV_UNAVAILABLE", "cwd": "OWNERSHIP_CWD_UNAVAILABLE"}.get(
            value["operation"]
        )
        == code
        and value["error_kind"] in ("os_error", "invalid_value")
        and (
            value["error_number"] is None
            or type(value["error_number"]) is int
            and 0 <= value["error_number"] <= 4095
        )
        and value["identity_after"] in ("live", "zombie", "changed", "unavailable")
    )


class PreparationFailure(ValueError):
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        super().__init__(canonical(diagnostic))


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
    if "recovery_lineage_ref" in notification:
        from harness.execution_runtime_fences import _reference

        recovery = _reference(notification["recovery_lineage_ref"])
        package_ref = recovery["original_package_ref"]
    else:
        old_state = json.loads(
            Path(notification["predecessor_state_ref"]["stored_path"]).read_bytes()
        )
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
        runtime_layout=context["successor"].get("runtime_layout", "harness-session-directory-v1"),
        **(
            {
                "runtime_binding": {
                    "storage": notification["storage"],
                    "transaction": context["successor"],
                }
            }
            if context["successor"]["schema_version"] == 3
            else {}
        ),
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
        self.phase = "request"
        self.process = None

    def _response(self, sequence, process):
        try:
            value = receive(self.parent)
        except EOFError:
            raise PreparationFailure(
                {
                    "request_id": self.request["request_id"],
                    "stage": self.phase,
                    "code": "UNEXPECTED_EOF",
                    "observed_exit": process.poll(),
                    "diagnostic": "Coordinator disconnected; protected preparation may exist.",
                }
            ) from None
        if isinstance(value, dict) and value.get("phase") == "error":
            payload = value.get("payload")
            if (
                set(value) != {"version", "sequence", "phase", "payload"}
                or type(value["version"]) is not int
                or value["version"] != 1
                or type(value["sequence"]) is not int
                or value["sequence"] != sequence
                or not isinstance(payload, dict)
                or set(payload)
                not in (
                    {"request_id", "stage", "code", "diagnostic"},
                    {"request_id", "stage", "code", "diagnostic", "observation"},
                )
                or payload["request_id"] != self.request["request_id"]
                or payload["stage"] not in {"request", "preparation", "start"}
                or payload["code"]
                not in {
                    "REFUSED",
                    "DISCONNECTED",
                    "INTERNAL",
                    "OWNERSHIP_UNAVAILABLE",
                    *OWNERSHIP_DIAGNOSTICS,
                    "TASK_ACTIVITY",
                    "PAYLOAD_MISMATCH",
                }
                or "observation" in payload
                and not _valid_process_observation(payload["observation"], payload["code"])
                or payload["diagnostic"]
                != OWNERSHIP_DIAGNOSTICS.get(
                    payload["code"],
                    "Successor preparation refused; inspect the protected request before retry.",
                )
            ):
                raise ValueError("prepared coordinator error response refused")
            raise PreparationFailure(payload)
        return value

    def serve(self, process):
        self.process = process
        self.child.close()
        send(
            self.parent,
            {"version": 1, "sequence": 1, "operation": "prepare", "payload": self.request},
        )
        reserved = self._response(1, process)
        if (
            set(reserved) != {"version", "sequence", "phase", "payload"}
            or reserved["version"] != 1
            or reserved["sequence"] != 1
            or reserved["phase"] != "reserved"
        ):
            raise ValueError("prepared reservation response refused")
        self.phase = "reserved"
        response = self.resources(reserved["payload"])
        send(
            self.parent,
            {"version": 1, "sequence": 2, "operation": "resources", "payload": response},
        )
        self.phase = "resources"
        completed = self._response(2, process)
        if (
            set(completed) != {"version", "sequence", "phase", "payload"}
            or completed["version"] != 1
            or completed["sequence"] != 2
            or completed["phase"] != "prepared"
        ):
            raise ValueError("prepared completion response refused")
        self.phase = "prepared"
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
        except BaseException as error:
            diagnostic = (
                dict(error.diagnostic)
                if isinstance(error, PreparationFailure)
                else {
                    "request_id": self.request["request_id"],
                    "stage": self.phase,
                    "code": "PROTOCOL_REFUSED",
                    "diagnostic": "Managed preparation failed; inspect protected progress.",
                }
            )
            diagnostic["owned_child_exit"] = self.process.poll() if self.process else None
            self.receipt_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            durable_file(
                self.receipt_directory / ("attempt-diagnostic-" + secrets.token_hex(16) + ".json"),
                canonical(diagnostic).encode(),
            )
            if isinstance(error, PreparationFailure):
                error.diagnostic = diagnostic
                error.args = (canonical(diagnostic),)
                raise
            raise PreparationFailure(diagnostic) from None
        finally:
            self.parent.close()
            self.child.close()
