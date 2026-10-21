"""Coordinate new-run pins outside repository-controlled runtime state."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import secrets
import stat
import sys

from .registry import write_json
from .runtime_pin import _directory
from .validator_origin import canonical, capture, decode, digest, repository_identity, safe


def storage():
    pinned = sys.modules.get("_al_run_pin_context")
    if pinned is not None:
        return _directory(Path(pinned.storage), create=not getattr(pinned, "readonly", False))
    trusted = sys.modules.get("_al_validator_context")
    if trusted is None or trusted.request.get("mode") != "snapshot":
        raise ValueError("runtime storage requires the protected managed launcher")
    root = safe(trusted.request["root"]).resolve(strict=True)
    if (
        root.parent.name != "snapshots"
        or root.parent.parent.name != "cli"
        or root.parent.parent.parent.name != "installer"
    ):
        raise ValueError("unrecognized managed runtime registry layout")
    return _directory(root.parents[3] / "al-run-pins")


def subject(repository, platform, session):
    if platform not in {"codex", "claude"} or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", session
    ):
        raise ValueError("explicit safe platform and session required")
    root = safe(repository).resolve(strict=True)
    if root != Path.cwd().resolve():
        raise ValueError("run launcher repository and current directory differ")
    return {
        "repository_identity": repository_identity(root),
        "platform": platform,
        "session_id": session,
    }


def key(value):
    return digest(canonical(value))


def read(path):
    raw, state = capture(path)
    if state["mode"] != 0o600:
        raise ValueError("runtime pin transaction metadata mode changed")
    return decode(raw)


@contextmanager
def coordinated(value):
    directory = _directory(storage() / "runs")
    lock = directory / (key(value) + ".lock")
    safe(lock)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        state = os.fstat(descriptor)
        if (
            not stat.S_ISREG(state.st_mode)
            or state.st_uid != os.getuid()
            or state.st_mode & 0o777 != 0o600
        ):
            raise ValueError("unsafe runtime pin coordination lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield directory / (key(value) + ".json")
    finally:
        os.close(descriptor)


def state_path(value):
    return (
        Path(value["repository_identity"]["root"])
        / ("." + value["platform"])
        / "al-runtime"
        / (value["session_id"] + ".json")
    )


def reserve(value, pin):
    with coordinated(value) as path:
        if path.exists():
            transaction = read(path)
            validate(transaction, value)
            return transaction
        if state_path(value).exists():
            raise ValueError("existing run requires its original issuing runtime")
        transaction = {
            "schema_version": 1,
            "kind": "run-pin-binding-v1",
            **value,
            "run_id": secrets.token_hex(4),
            "publication_transaction": secrets.token_hex(32),
            "pin": pin,
            "status": "RESERVED",
            "initialization": None,
        }
        write_json(path, transaction)
        return transaction


def validate(transaction, value):
    fields = {
        "schema_version",
        "kind",
        "repository_identity",
        "platform",
        "session_id",
        "run_id",
        "publication_transaction",
        "pin",
        "status",
        "initialization",
    }
    if (
        not isinstance(transaction, dict)
        or set(transaction) != fields
        or transaction["schema_version"] != 1
        or transaction["kind"] != "run-pin-binding-v1"
        or transaction["status"] not in {"RESERVED", "ACTIVE"}
        or any(transaction.get(k) != v for k, v in value.items())
    ):
        raise ValueError("protected runtime run binding differs")
    if not re.fullmatch(r"[0-9a-f]{8}", transaction["run_id"]) or not re.fullmatch(
        r"[0-9a-f]{64}", transaction["publication_transaction"]
    ):
        raise ValueError("invalid reserved runtime run identity")


def commit_run_binding(transaction):
    value = {k: transaction[k] for k in ("repository_identity", "platform", "session_id")}
    with coordinated(value) as path:
        current = read(path)
        validate(current, value)
        if current != transaction:
            raise ValueError("runtime transaction moved before state publication")
        state = read_state(transaction)
        if state is None:
            raise ValueError("reserved runtime did not publish matching state")
        if current["status"] == "RESERVED":
            pointer = state_path(transaction).parent / "current"
            raw, _ = capture(pointer)
            if raw != (transaction["session_id"] + "\n").encode():
                raise ValueError("runtime start pointer publication is incomplete")
            write_json(path, dict(current, status="ACTIVE"))


def read_state(transaction):
    path = state_path(transaction)
    if not path.exists():
        return None
    state = read(path)
    expected = {
        k: transaction[k]
        for k in (
            "kind",
            "schema_version",
            "run_id",
            "publication_transaction",
            "pin",
            "initialization",
        )
    }
    if (
        state.get("session_id") != transaction["session_id"]
        or state.get("run_id") != transaction["run_id"]
        or state.get("runtime_origin") != expected
        or state.get("identity", {}).get("root") != transaction["repository_identity"]["root"]
    ):
        raise ValueError("state and independently protected runtime binding differ")
    return state
