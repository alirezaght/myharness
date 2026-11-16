"""Operator-only restoration of exact retained sealed bytes, without new approval."""

import os
import secrets
import stat
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from harness.approval import GRANTED_EVENT, ApprovalGate
from harness.db import transaction
from harness.execution_contract import MAX_HANDOFF, canonical, digest, identity, loads
from harness.execution_epochs import require_active_epoch
from harness.execution_handoff import atomic_file, same_path
from harness.execution_models import ExecutionConflict
from harness.execution_runtime_recovery import _locks
from harness.spec_record import SpecStatus, content_hash, parse_spec_text
from harness.spec_store import SpecStore
from harness.template_classification import _ordinary
from harness.testplan import TestPlanner


def _epoch(controller, execution_id, epoch_id, generation):
    if not isinstance(epoch_id, str) or type(generation) is not int or generation < 0:
        raise ExecutionConflict("explicit current epoch and authority generation required")
    return require_active_epoch(
        controller.conn,
        execution_id,
        expected_epoch_id=epoch_id,
        expected_authority_generation=generation,
        mutation=True,
    )


def _absolute(value):
    path = Path(value)
    if not path.is_absolute() or str(path) != value or ".." in path.parts:
        raise ExecutionConflict("recorded specification path is not canonical and absolute")
    return path


@contextmanager
def _parent(target, specs_root, *, create=False):
    """Walk original path through directory descriptors, refusing every alias."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    current = Path("/")
    try:
        for part in target.parent.parts[1:]:
            current = current / part
            try:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
            except FileNotFoundError:
                if not create:
                    yield None
                    return
                if current != specs_root and not current.is_relative_to(specs_root):
                    raise ExecutionConflict(
                        "original specification root parent is unavailable"
                    ) from None
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
            os.close(descriptor)
            descriptor = child
        yield descriptor
    except (NotADirectoryError, OSError) as error:
        if isinstance(error, PermissionError):
            raise
        raise ExecutionConflict(
            "specification parent is not an ordinary, unaliased directory"
        ) from error
    finally:
        os.close(descriptor)


def _condition(target, specs_root, raw):
    with _parent(target, specs_root) as parent:
        if parent is None:
            return "missing"
        try:
            info = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return "missing"
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            return "unsafe"
        if not stat.S_IMODE(info.st_mode) & stat.S_IRUSR:
            return "unreadable"
        existing, _ = _ordinary(target, MAX_HANDOFF)
        return "intact" if existing == raw else "modified"


def _decision_obligations(controller, execution):
    obligations = []
    for row in controller.conn.execute(
        "SELECT registry_path,registry_hash FROM execution_decisions "
        "WHERE execution_id=? AND registry_hash IS NOT NULL AND state != 'Intent'",
        (execution["id"],),
    ):
        try:
            raw, _ = _ordinary(row["registry_path"], MAX_HANDOFF)
            if digest(raw) != row["registry_hash"]:
                obligations.append("Decision Registry provenance changed: " + row["registry_path"])
        except (OSError, ValueError) as error:
            obligations.append(
                "Decision Registry provenance unavailable: "
                + row["registry_path"]
                + " ("
                + type(error).__name__
                + ")"
            )
    return obligations


def _validate_inputs(controller, execution):
    raw_handoff, mode = _ordinary(execution["handoff_path"], MAX_HANDOFF)
    if mode != 0o600 or digest(raw_handoff) != execution["handoff_hash"]:
        raise ExecutionConflict("retained execution handoff changed")
    envelope = loads(raw_handoff, max_bytes=MAX_HANDOFF)
    if (
        envelope.get("schema_version") != 1
        or any(envelope.get(k) != execution[k] for k in ("task_id", "host", "al_session_id"))
        or envelope.get("execution_id") != execution["id"]
        or not same_path(
            envelope["database"], controller.conn.execute("PRAGMA database_list").fetchone()["file"]
        )
    ):
        raise ExecutionConflict("retained handoff execution/database identity differs")
    ws = controller.workspace(execution["task_id"])
    approval = ws.latest_approval()
    if approval is None:
        raise ExecutionConflict("no current approval is available for specification restoration")
    expected = {
        "execution_id": execution["id"],
        "task_id": execution["task_id"],
        "approval_id": approval.id,
        "spec_version": approval.spec_version,
        "content_hash": approval.content_hash,
        "revision": approval.revision,
        "review_state_hash": approval.review_state_hash,
    }
    if (
        execution["approval_id"] != approval.id
        or execution["binding"] != expected
        or envelope["approval"] != dict(expected, path=approval.path)
    ):
        raise ExecutionConflict("execution approval is superseded or its retained binding changed")
    specs_root, target = _absolute(envelope["specs"]), _absolute(approval.path)
    if target != specs_root / execution["task_id"] / ("v" + str(approval.spec_version) + ".md"):
        raise ExecutionConflict("approved target differs from original recorded specification root")
    row = ws.spec_version(approval.spec_version)
    if (
        row is None
        or ws.current_spec_version() != row
        or row.status is not SpecStatus.APPROVED
        or row.superseded_by is not None
        or row.path != approval.path
        or row.revision != approval.revision
        or row.content_hash != approval.content_hash
    ):
        raise ExecutionConflict("recorded approved specification version is no longer current")
    granted = [
        event.payload
        for event in ws.events()
        if event.kind == GRANTED_EVENT and event.payload.get("approval_id") == approval.id
    ]
    recorded = {
        "approval_id": approval.id,
        "spec_version": approval.spec_version,
        "path": approval.path,
        "revision": approval.revision,
        "content_hash": approval.content_hash,
        "review_state_hash": approval.review_state_hash,
    }
    checkpoint = ws.latest_checkpoint("approval")
    if granted != [recorded] or checkpoint is None or checkpoint.payload != recorded:
        raise ExecutionConflict("current approval lacks matching original grant provenance")
    gate = ApprovalGate(
        controller, execution["task_id"], SpecStore(target.parent), Path(envelope["repository"])
    )
    gate._check_phase()
    gate._check_state(approval)
    sealed = envelope["sealed_bytes"].encode("utf-8")
    if digest(sealed) != envelope["sealed_hash"]:
        raise ExecutionConflict("retained sealed specification raw hash differs")
    parsed = parse_spec_text(envelope["sealed_bytes"], target)
    if (
        parsed.task_id != execution["task_id"]
        or parsed.version != approval.spec_version
        or parsed.status is not SpecStatus.APPROVED
        or parsed.superseded_by is not None
        or parsed.revision != approval.revision
        or parsed.content_hash != approval.content_hash
        or content_hash(parsed.body) != approval.content_hash
        or parsed.working_tree != row.working_tree
    ):
        raise ExecutionConflict("retained sealed specification identity/body differs from approval")
    plan = TestPlanner(controller, execution["task_id"]).document()
    if (
        not plan.review.current
        or plan.to_dict() != envelope["plan"]
        or plan.render() != envelope["plan_text"]
    ):
        raise ExecutionConflict("current approved test plan differs from retained handoff")
    return envelope, approval, target, specs_root, sealed


def _validate(controller, execution):
    try:
        return _validate_inputs(controller, execution)
    except (KeyError, TypeError, AttributeError, UnicodeError) as error:
        raise ExecutionConflict("retained sealed specification metadata is malformed") from error


def _preview(controller, execution):
    envelope, approval, target, specs_root, sealed = _validate(controller, execution)
    try:
        condition = _condition(target, specs_root, sealed)
    except PermissionError:
        condition = "unreadable"
    except ExecutionConflict:
        condition = "unsafe"
    preview = {
        "schema_version": 1,
        "kind": "sealed-specification-restoration-preview-v1",
        "execution_id": execution["id"],
        "task_id": execution["task_id"],
        "expected_epoch_id": execution["epoch_id"],
        "expected_authority_generation": execution["authority_generation"],
        "approval": asdict(approval),
        "handoff_sha256": execution["handoff_hash"],
        "target": str(target),
        "specs_root": str(specs_root),
        "sealed_sha256": envelope["sealed_hash"],
        "byte_count": len(sealed),
        "state_condition": condition,
        "readiness": "ready" if condition in {"missing", "intact"} else "unavailable",
        "provenance_obligations": _decision_obligations(controller, execution),
    }
    return {"preview": preview, "preview_sha256": digest(preview)}, sealed


def preview_spec_restoration(
    controller, execution_id, *, expected_epoch_id, expected_authority_generation
):
    execution = _epoch(controller, execution_id, expected_epoch_id, expected_authority_generation)
    with _locks(execution):
        execution = _epoch(
            controller, execution_id, expected_epoch_id, expected_authority_generation
        )
        return _preview(controller, execution)[0]


def _publish(target, specs_root, raw):
    with _parent(target, specs_root, create=True) as parent:
        before = os.fstat(parent)
        temporary = "." + target.name + ".restore-" + secrets.token_hex(16)
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            current = target.parent.stat(follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
                raise ExecutionConflict("specification parent moved before publication")
            try:
                os.link(
                    temporary,
                    target.name,
                    src_dir_fd=parent,
                    dst_dir_fd=parent,
                    follow_symlinks=False,
                )
                published = True
            except FileExistsError:
                published = False
        finally:
            os.unlink(temporary, dir_fd=parent)
            os.fsync(parent)
    if _condition(target, specs_root, raw) != "intact":
        raise ExecutionConflict("specification changed during exclusive restoration")
    return published


def _audit(controller, execution, request_id, reference):
    payload = {"execution_id": execution["id"], "request_id": request_id, "receipt": reference}
    existing = [
        event.payload
        for event in controller.workspace(execution["task_id"]).events()
        if event.kind == "execution.specification.restored"
        and event.payload.get("execution_id") == execution["id"]
        and event.payload.get("request_id") == request_id
    ]
    if existing and existing != [payload]:
        raise ExecutionConflict("specification restoration audit identity differs")
    if not existing:
        controller.workspace(execution["task_id"]).record_event(
            "execution.specification.restored", payload
        )


def restore_specification(
    controller,
    execution_id,
    *,
    request_id,
    preview_sha256,
    expected_epoch_id,
    expected_authority_generation,
):
    identity(request_id)
    execution = _epoch(controller, execution_id, expected_epoch_id, expected_authority_generation)
    with _locks(execution):
        directory = Path(execution["authority_directory"]) / "spec-restorations" / request_id
        request_path, receipt_path = directory / "request.json", directory / "receipt.json"
        with transaction(controller.conn):
            execution = _epoch(
                controller, execution_id, expected_epoch_id, expected_authority_generation
            )
            observed, raw = _preview(controller, execution)
            preview = observed["preview"]
            request = {
                "request_id": request_id,
                "execution_id": execution_id,
                "preview_sha256": preview_sha256,
                "preview": preview,
            }
            try:
                saved_raw, mode = _ordinary(request_path, MAX_HANDOFF)
            except FileNotFoundError:
                if observed["preview_sha256"] != preview_sha256:
                    raise ExecutionConflict("stale specification restoration preview") from None
                if preview["readiness"] != "ready":
                    raise ExecutionConflict(
                        "specification is " + preview["state_condition"]
                    ) from None
                atomic_file(request_path, canonical(request).encode())
            else:
                saved = loads(saved_raw, max_bytes=MAX_HANDOFF)
                normalized = dict(preview, state_condition=saved["preview"]["state_condition"])
                if (
                    mode != 0o600
                    or saved["execution_id"] != execution_id
                    or saved["request_id"] != request_id
                    or saved["preview_sha256"] != preview_sha256
                    or digest(saved["preview"]) != preview_sha256
                    or preview["readiness"] != "ready"
                    or normalized != saved["preview"]
                ):
                    raise ExecutionConflict(
                        "restoration request ID reused with changed content or authority"
                    )
                request = saved
            target, root = Path(preview["target"]), Path(preview["specs_root"])
            published = (
                _publish(target, root, raw) if preview["state_condition"] == "missing" else False
            )
        from harness.execution_authority import require_execution_authority

        try:
            require_execution_authority(controller, execution_id, operation="read")
            authority = {"status": "current", "diagnostic": None}
        except (ValueError, OSError, LookupError) as error:
            authority = {"status": "unavailable", "diagnostic": str(error)}
        receipt = {
            "schema_version": 1,
            "kind": "sealed-specification-restoration-receipt-v1",
            "request": request,
            "restored": published,
            "authority_revalidation": authority,
            "implementation_authority_granted": False,
        }
        try:
            existing_raw, mode = _ordinary(receipt_path, MAX_HANDOFF)
        except FileNotFoundError:
            existing_raw = canonical(receipt).encode()
            atomic_file(receipt_path, existing_raw)
        else:
            existing = loads(existing_raw, max_bytes=MAX_HANDOFF)
            if mode != 0o600 or existing["request"] != request:
                raise ExecutionConflict("restoration receipt identity differs")
        reference = {"path": str(receipt_path), "sha256": digest(existing_raw)}
        with transaction(controller.conn):
            _audit(controller, execution, request_id, reference)
        return {
            "restored": published,
            "state": "restored" if published else "already-present",
            "request_id": request_id,
            "receipt": reference,
            "authority_revalidation": authority,
            "implementation_authority_granted": False,
        }
