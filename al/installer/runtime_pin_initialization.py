"""Independent immutable new-run proof initialization, before state publication."""

import time
import re

from .registry import write_json
from .runtime_pin import _directory, _sync
from .runtime_pin_transaction import coordinated, read, storage, validate
from .validator_origin import canonical, capture, digest


KEYS = {
    "schema_version",
    "kind",
    "repository_identity",
    "session_id",
    "run_id",
    "original_task_package",
    "requirement_lineage_hash",
    "runtime_pin",
    "verifier_policy",
    "planning_occurrence",
}


def read_initialization(transaction, *, storage_root=None):
    reference = transaction.get("initialization")
    if not isinstance(reference, dict):
        raise ValueError("missing protected proof initialization")
    expected_fields = {"id", "sha256", "kind", "stored_path", "source", "captured_at"}
    if set(reference) != expected_fields or not re.fullmatch(
        r"[0-9a-f]{64}", reference.get("sha256", "")
    ):
        raise ValueError("invalid protected initialization reference")
    root = storage() if storage_root is None else _directory(storage_root, create=False)
    expected = root / "initializations" / reference["sha256"]
    if (
        str(expected) != reference["stored_path"]
        or reference["id"] != reference["sha256"]
        or reference["kind"] != "proof-admission-initialization-v1"
    ):
        raise ValueError("proof initialization reference escaped protected storage")
    raw, metadata = capture(expected)
    if metadata["mode"] != 0o600 or digest(raw) != reference["sha256"]:
        raise ValueError("protected proof initialization changed")
    from .validator_origin import decode

    value = decode(raw)
    if (
        set(value) != KEYS
        or value["schema_version"] != 1
        or value["kind"] != "proof-admission-initialization-v1"
    ):
        raise ValueError("unsupported proof initialization")
    if (
        any(value[k] != transaction[k] for k in ("repository_identity", "session_id", "run_id"))
        or value["runtime_pin"] != transaction["pin"]
    ):
        raise ValueError("proof initialization and protected run binding differ")
    return value


def initialize(transaction, supplied):
    if set(supplied) != KEYS:
        raise ValueError("incomplete new-run proof initialization")
    subject = {k: transaction[k] for k in ("repository_identity", "platform", "session_id")}
    with coordinated(subject) as path:
        current = read(path)
        validate(current, subject)
        if current != transaction or current["status"] != "RESERVED":
            raise ValueError("proof initialization requires the exact reserved transaction")
        if current["initialization"] is not None:
            original = read_initialization(current)
            comparable = dict(supplied, original_task_package=original["original_task_package"])
            if (
                comparable != original
                or supplied["original_task_package"]["sha256"]
                != original["original_task_package"]["sha256"]
            ):
                raise ValueError("retry differs from immutable proof initialization")
            return current, original
        raw = canonical(supplied) + b"\n"
        sha = digest(raw)
        directory = _directory(storage() / "initializations")
        destination = directory / sha
        if destination.exists():
            old, metadata = capture(destination)
            if old != raw or metadata["mode"] != 0o600:
                raise ValueError("proof initialization collision or mode drift")
        else:
            write_json(destination, supplied)
            _sync(directory)
        reference = {
            "id": sha,
            "sha256": sha,
            "kind": supplied["kind"],
            "stored_path": str(destination),
            "source": "protected-run-bootstrap",
            "captured_at": time.time(),
        }
        current = dict(current, initialization=reference)
        write_json(path, current)
        _sync(path.parent)
        return current, supplied
