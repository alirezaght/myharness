"""Independent immutable new-run proof initialization, before state publication."""

import time
import re

from .registry import write_json
from .runtime_pin import _directory, _sync
from .runtime_pin_transaction import LAYOUT_FIELDS, coordinated, read, storage, validate, runtime_directory
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
    from .runtime_initialization_client import selected
    client = selected()
    if client is not None:
        if transaction != client.transaction or client.initialization is None:
            raise ValueError('prepared initialization cache differs')
        return client.initialization
    reference = transaction.get("initialization")
    if not isinstance(reference, dict):
        raise ValueError("missing protected proof initialization")
    version = transaction["schema_version"]
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
        or reference["kind"] != "proof-admission-initialization-v" + str(version)
    ):
        raise ValueError("proof initialization reference escaped protected storage")
    raw, metadata = capture(expected)
    if metadata["mode"] != 0o600 or digest(raw) != reference["sha256"]:
        raise ValueError("protected proof initialization changed")
    from .validator_origin import decode

    value = decode(raw)
    if (
        set(value) != (KEYS | LAYOUT_FIELDS if version == 2 else KEYS)
        or value["schema_version"] != version
        or value["kind"] != "proof-admission-initialization-v" + str(version)
    ):
        raise ValueError("unsupported proof initialization")
    if (
        any(value[k] != transaction[k] for k in ("repository_identity", "session_id", "run_id"))
        or value["runtime_pin"] != transaction["pin"]
    ):
        raise ValueError("proof initialization and protected run binding differ")
    if version == 2:
        runtime_directory(transaction)
        if any(value[k] != transaction[k] for k in LAYOUT_FIELDS):
            raise ValueError("proof initialization runtime layout differs")
    return value


def initialize(transaction, supplied):
    from .runtime_initialization_client import selected, initialize as channel_initialize
    if selected() is not None:
        return channel_initialize(transaction, supplied)
    subject = {k: transaction[k] for k in ('repository_identity', 'platform', 'session_id')}
    from .runtime_pin_transaction import publication_lock
    with publication_lock(subject) as capability:
        return initialize_locked(capability, transaction, supplied)


def initialize_locked(capability, transaction, supplied):
    if set(supplied) != (KEYS | LAYOUT_FIELDS if transaction['schema_version'] == 2 else KEYS):
        raise ValueError('incomplete new-run proof initialization')
    subject = {k: transaction[k] for k in ('repository_identity', 'platform', 'session_id')}
    capability.validate(subject)
    path = capability.path
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
