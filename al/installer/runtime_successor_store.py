"""Protected preparation records; initialized does not mean product-authoritative."""

import os
import re

from .registry import write_json
from .runtime_pin import _directory, _sync
from .runtime_pin_transaction import key, storage
from .validator_origin import canonical, capture, decode, digest, safe


def request_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise ValueError("invalid successor request identity")
    return value


def directory(identifier):
    return _directory(storage() / "successors" / request_id(identifier))


def immutable(path, value):
    path = safe(path)
    raw = canonical(value) + b"\n"
    if path.exists():
        previous, info = capture(path)
        if previous != raw or info["mode"] != 0o600 or path.stat().st_nlink != 1:
            raise ValueError("successor immutable publication differs")
    else:
        write_json(path, value)
        _sync(path.parent)
    return {"stored_path": str(path), "sha256": digest(raw)}


def read_reference(ref):
    if not isinstance(ref, dict) or set(ref) != {"stored_path", "sha256"}:
        raise ValueError("invalid protected successor reference")
    path = safe(ref["stored_path"])
    if not path.is_relative_to(storage() / "successors"):
        raise ValueError("successor reference escaped protected storage")
    raw, info = capture(path)
    if info["mode"] != 0o600 or path.stat().st_nlink != 1 or digest(raw) != ref["sha256"]:
        raise ValueError("protected successor reference changed")
    return decode(raw)


def fence_path(subject):
    return storage(readonly=True) / "successor-fences" / (key(subject) + ".json")


def fence(subject, identifier, plan_sha256):
    parent = _directory(storage() / "successor-fences")
    return immutable(
        parent / (key(subject) + ".json"),
        {
            "schema_version": 1,
            "kind": "prepared-successor-fence-v1",
            "subject": subject,
            "request_id": request_id(identifier),
            "plan_sha256": plan_sha256,
        },
    )


def require_ordinary_authority(subject):
    # Stage2b supplies the activation selector. No preparation record grants mutation.
    path = fence_path(subject)
    try:
        path.lstat()
    except FileNotFoundError:
        return
    from .runtime_successor_activation import active_path, require_active
    try:
        active_path(subject).lstat()
    except FileNotFoundError:
        raise ValueError('prepared successor has no product authority; inspect its preparation receipt') from None
    require_active(subject)


def lineage_for(transaction):
    subject = {k: transaction[k] for k in ("repository_identity", "platform", "session_id")}
    path = fence_path(subject)
    raw, info = capture(path)
    value = decode(raw)
    if (
        info["mode"] != 0o600
        or set(value) != {"schema_version", "kind", "subject", "request_id", "plan_sha256"}
        or value["kind"] != "prepared-successor-fence-v1"
        or value["subject"] != subject
    ):
        raise ValueError("invalid prepared successor fence")
    plan_path = directory(value["request_id"]) / "plan.json"
    raw, _ = capture(plan_path)
    if digest(raw) != value["plan_sha256"]:
        raise ValueError("successor plan changed")
    plan = decode(raw)
    if plan["successor_transaction"] != {
        **transaction,
        "initialization": None,
        "status": "RESERVED",
    }:
        raise ValueError("successor plan transaction differs")
    ref_path = directory(value["request_id"]) / "lineage-reference.json"
    raw, _ = capture(ref_path)
    ref = decode(raw)
    return ref, read_reference(ref)


def archive_bytes(identifier, name, raw):
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.json", name):
        raise ValueError("invalid successor archive name")
    path = directory(identifier) / name
    if path.exists():
        old, info = capture(path)
        if old != raw or info["mode"] != 0o600 or path.stat().st_nlink != 1:
            raise ValueError("successor archive changed")
    else:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _sync(path.parent)
    return {"stored_path": str(path), "sha256": digest(raw)}
