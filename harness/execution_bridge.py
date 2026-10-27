"""Audited, versioned bridge upgrades preserve the exact approved execution.

Old snapshots, native identities, AL pins, proof history and reports stay intact.
Only settled executions can select a separately verified new bridge snapshot.
"""

import hashlib
import importlib.util
import json
import marshal
import os
import re
import stat
import tempfile
import time
from pathlib import Path

from harness.execution_contract import canonical, digest, loads
from harness.execution_handoff import durable_file
from harness.execution_models import ExecutionConflict


def _read(path, maximum=4 * 1024 * 1024):
    path = Path(path)
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ExecutionConflict("bridge resource symlink refused")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise ExecutionConflict("bridge resource must be a bounded regular file")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    after = path.stat()
    if len(raw) > maximum or any(
        getattr(info, field) != getattr(after, field)
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_mode")
    ):
        raise ExecutionConflict("bridge resource moved during read")
    return raw


def _fingerprint(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)


def _verified_bytecode(root, name, files):
    """Legacy authority imports created caches; verify rather than discard them.

    Bytecode is never executed here. Its code object must equal compilation of
    the sealed source with this interpreter, including its bound source path.
    """
    path = root / "harness" / name
    if path.suffix != ".pyc" or path.parent.name != "__pycache__":
        return False
    try:
        source = Path(importlib.util.source_from_cache(str(path)))
        relative = source.relative_to(root / "harness").as_posix()
        if relative not in files:
            return False
        raw = _read(path)
        if raw[:4] != importlib.util.MAGIC_NUMBER or len(raw) < 16:
            return False
        code = marshal.loads(raw[16:])
        optimized = re.search(r"\.opt-([12])\.pyc$", name)
        expected = compile(
            _read(source),
            str(source),
            "exec",
            dont_inherit=True,
            optimize=int(optimized[1]) if optimized else 0,
        )
        return code == expected and code.co_filename == str(source)
    except (ValueError, TypeError, EOFError):
        return False


def verify_snapshot(root):
    root = Path(root)
    if any(p.is_symlink() for p in [root, *root.parents]):
        raise ExecutionConflict("bridge snapshot symlink refused")
    marker = root / "manifest.json"
    manifest = loads(_read(marker), max_bytes=4 * 1024 * 1024)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"files", "fingerprint"}
        or not _fingerprint(manifest["fingerprint"])
        or not isinstance(manifest["files"], dict)
        or not manifest["files"]
    ):
        raise ExecutionConflict("invalid bridge snapshot manifest")
    files = manifest["files"]
    if any(p.is_symlink() for p in (root / "harness").rglob("*")):
        raise ExecutionConflict("bridge snapshot symlink refused")
    actual = {
        p.relative_to(root / "harness").as_posix()
        for p in (root / "harness").rglob("*")
        if p.is_file()
    }
    extras = actual - set(files)
    if set(files) - actual or any(not _verified_bytecode(root, name, files) for name in extras):
        raise ExecutionConflict(
            "bridge snapshot membership differs: "
            f"missing={sorted(set(files) - actual)[:10]} extra={sorted(actual - set(files))[:10]}"
        )
    values = {}
    for name, sha in files.items():
        path = root / "harness" / name
        if (
            not isinstance(name, str)
            or Path(name).is_absolute()
            or ".." in Path(name).parts
            or Path(name).as_posix() != name
            or "\\" in name
            or not _fingerprint(sha)
            or any(p.is_symlink() for p in [path, *path.parents])
        ):
            raise ExecutionConflict("unsafe bridge snapshot member")
        raw = _read(path)
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ExecutionConflict("bridge snapshot changed")
        values[name] = raw
    fingerprint = hashlib.sha256(
        b"".join(k.encode() + v for k, v in sorted(values.items()))
    ).hexdigest()
    if fingerprint != manifest["fingerprint"]:
        raise ExecutionConflict("bridge snapshot fingerprint differs")
    return fingerprint


def selected_bridge(directory, execution=None):
    directory = Path(directory)
    pointer = directory / "bridge-selection.json"
    if not pointer.exists():
        return directory / "trusted"
    if pointer.is_symlink():
        raise ExecutionConflict("bridge selection symlink refused")
    selected = loads(_read(pointer), max_bytes=4 * 1024 * 1024)
    if (
        not isinstance(selected, dict)
        or set(selected) != {"receipt", "sha256"}
        or not isinstance(selected["receipt"], str)
    ):
        raise ExecutionConflict("invalid bridge selection")
    receipt = directory / selected["receipt"]
    if (
        Path(selected["receipt"]).is_absolute()
        or ".." in Path(selected["receipt"]).parts
        or receipt.is_symlink()
        or receipt.parent != directory / "bridge-upgrades"
    ):
        raise ExecutionConflict("unsafe bridge upgrade receipt")
    raw = _read(receipt)
    if digest(raw) != selected["sha256"]:
        raise ExecutionConflict("bridge upgrade receipt changed")
    value = loads(raw, max_bytes=4 * 1024 * 1024)
    if (
        not isinstance(value, dict)
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("kind") != "execution-bridge-upgrade-v1"
        or not all(_fingerprint(value.get(key)) for key in ("from", "to"))
    ):
        raise ExecutionConflict("unsupported bridge upgrade receipt")
    if execution is not None and any(
        value.get(key) != execution[key]
        for key in ["id", "binding", "handoff_hash", "al_run_id", "al_session_id", "native_id"]
    ):
        raise ExecutionConflict("bridge upgrade execution/approval/native binding changed")
    root = directory / "bridge-revisions" / value["to"] / "trusted"
    if verify_snapshot(root) != value["to"]:
        raise ExecutionConflict("selected bridge differs")
    return root


def bridge_root(execution):
    return selected_bridge(Path(execution["handoff_path"]).parent, execution)


def _select(directory, ref):
    directory = Path(directory)
    descriptor, name = tempfile.mkstemp(prefix=".bridge-selection-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(ref if isinstance(ref, bytes) else canonical(ref).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, directory / "bridge-selection.json")
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(name).unlink(missing_ok=True)


def upgrade_bridge(controller, execution_id, *, reason):
    from harness.db import transaction
    from harness.execution_authority import require_execution_authority
    from harness.execution_bootstrap import authority_snapshot
    from harness.execution_hosts import al_source, managed_launcher
    from harness.execution_initialization import validate_existing_origin
    from harness.execution_store import get_execution, process_alive
    from harness.execution_worker import worker_lock

    if not isinstance(reason, str) or len(reason.strip()) < 20:
        raise ValueError("bridge upgrade requires a substantive tool-maintenance reason")
    execution = get_execution(controller.conn, execution_id)
    with worker_lock(execution):
        execution = get_execution(controller.conn, execution_id)
        envelope = require_execution_authority(controller, execution_id, operation="read")
        rows = controller.conn.execute(
            "SELECT child_pid,status FROM execution_attempts WHERE execution_id=?", (execution_id,)
        ).fetchall()
        if any(
            row["status"] not in {"Exited", "Reconciled", "PreSpawnFailed"}
            or process_alive(row["child_pid"])
            for row in rows
        ):
            raise ExecutionConflict(
                "settle recorded native attempts before a bridge upgrade; "
                "no live or uncertain child may be replaced"
            )
        if not execution["al_run_id"] or not execution["native_id"]:
            raise ExecutionConflict(
                "bridge upgrade requires the exact initialized AL run and recorded native identity"
            )
        identity = validate_existing_origin(execution, envelope, managed_launcher())
        if identity["al_run_id"] != execution["al_run_id"]:
            raise ExecutionConflict("protected AL origin differs from the recorded execution")
        source = al_source(execution, envelope)
        before = source.read_bytes()
        state = json.loads(before)
        if (
            state.get("run_id") != execution["al_run_id"]
            or state.get("session_id") != execution["al_session_id"]
        ):
            raise ExecutionConflict("bridge upgrade AL identity differs")
        if any(
            w["state"] in {"ACTIVE", "UNRESOLVED"} for w in state.get("writers", {}).values()
        ) or any(e["status"] == "RUNNING" for e in state.get("evidence", {}).get("entries", [])):
            raise ExecutionConflict("bridge upgrade requires settled product writers and checks")
        directory = Path(execution["handoff_path"]).parent
        old = selected_bridge(directory, execution)
        old_digest = verify_snapshot(old)
        candidate = Path(authority_snapshot(directory, versioned=True))
        new_digest = verify_snapshot(candidate)
        if old_digest == new_digest:
            return {"status": "Current", "bridge": str(old), "fingerprint": old_digest}
        if source.read_bytes() != before:
            raise ExecutionConflict("AL source moved during bridge upgrade")
        value = {
            "schema_version": 1,
            "kind": "execution-bridge-upgrade-v1",
            **{
                key: execution[key]
                for key in ["id", "binding", "handoff_hash", "al_run_id", "native_id"]
            },
            "al_session_id": execution["al_session_id"],
            "from": old_digest,
            "to": new_digest,
            "prior_bridge": str(old),
            "al_source_sha256": hashlib.sha256(before).hexdigest(),
            "reason": reason.strip(),
            "at": time.time(),
            "preserved": [
                "approval",
                "product scope",
                "native identity",
                "AL run and pin",
                "proof history",
                "original bridge",
                "reports",
            ],
        }
        raw = canonical(value).encode()
        sha = digest(raw)
        path = directory / "bridge-upgrades" / (sha.removeprefix("sha256:") + ".json")
        durable_file(path, raw)
        pointer = directory / "bridge-selection.json"
        prior = _read(pointer) if pointer.exists() else None
        try:
            with transaction(controller.conn):
                require_execution_authority(controller, execution_id, operation="read")
                _select(
                    directory, {"receipt": path.relative_to(directory).as_posix(), "sha256": sha}
                )
                controller.workspace(execution["task_id"]).record_event(
                    "execution.bridge_upgraded", value | {"receipt_sha256": sha}
                )
        except BaseException:
            # Receipts/snapshots are append-only; a failed publication may leave an
            # unselected candidate, but must restore the previously active bridge.
            if controller.conn.in_transaction:
                controller.conn.execute("ROLLBACK")
            if prior is None:
                pointer.unlink(missing_ok=True)
            else:
                _select(directory, prior)
            raise
        return {
            "status": "Upgraded",
            "receipt": str(path),
            "sha256": sha,
            "bridge": str(candidate),
            "al_run_id": execution["al_run_id"],
            "native_id": execution["native_id"],
            "resume_required": True,
        }
