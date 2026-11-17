"""First-run durable reservation through the sealed, read-only Harness authority."""

import os
import re
from pathlib import Path

from . import runtime_successor_store as Store
from .runtime_pin_transaction import key, read, runtime_directory, storage, subject, validate
from .validator_origin import capture, decode, digest, safe


def binding_for_start(value, argv):
    from .runtime_successor import _authority

    if argv.count("--handoff") != 1:
        raise ValueError("Harness start requires one explicit authority handoff")
    index = argv.index("--handoff")
    if index + 1 >= len(argv):
        raise ValueError("Harness start handoff missing")
    path = Path(argv[index + 1])
    raw, info = capture(path)
    envelope = decode(raw)
    if info["mode"] != 0o600 or envelope.get("runtime_layout") != "harness-durable-epoch-v1":
        return None  # Existing sealed v1/v2 handoffs retain their original layout.
    handoff_ref = {"path": str(path), "sha256": "sha256:" + digest(raw)}
    inputs = {
        "kind": "initial-harness-runtime-inputs-v1",
        "subject": value,
        "execution_id": envelope["execution_id"],
        "authority_handoff_ref": handoff_ref,
    }
    ref = Store.immutable(Store.directory("initial-" + key(value)) / "initial-inputs.json", inputs)
    authorized = _authority(
        envelope["runtime_authority_ref"], path, ref["stored_path"], inputs_path=ref["stored_path"]
    )
    return authorized["harness_binding"]


def inspect_origin(repository, platform, session, *, allow_absent=False):
    """The protected issuer, never caller state, identifies the initialized runtime."""
    from .runtime_pin_transaction import publication_lock
    from .runtime_successor import distribution

    distribution(platform)
    value = subject(repository, platform, session)
    with publication_lock(value, readonly=not allow_absent) as lock:
        if allow_absent:
            _require_unambiguous_origin(value, lock.path.parent)
            try:
                lock.path.lstat()
            except FileNotFoundError:
                _require_no_partial_origin(value)
                lock.validate(value)
                return {
                    "schema_version": 1,
                    "kind": "runtime-origin-absence-v1",
                    "state": "ABSENT",
                    "repository": value["repository_identity"]["root"],
                    "platform": platform,
                    "session_id": session,
                }
        transaction = read(lock.path)
        validate(transaction, value)
        if transaction["status"] != "ACTIVE":
            raise ValueError("runtime origin requires active protected initialization")
        return {
            "storage": str(storage(readonly=True)),
            "transaction": transaction,
            "source": str(runtime_directory(transaction) / (session + ".json")),
        }


def _metadata_files(root):
    from .runtime_pin import _directory

    root = safe(root)
    try:
        root.lstat()
    except FileNotFoundError:
        return []
    _directory(root, create=False)
    pending, files, count = [root], [], 0
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                count += 1
                if count > 10000:
                    raise ValueError("runtime origin metadata inventory exceeds bound")
                path = safe(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    raise ValueError("unexpected runtime origin metadata directory")
                elif entry.is_file(follow_symlinks=False):
                    files.append(path)
                else:
                    raise ValueError("unknown runtime origin metadata member")
    return files


def _require_unambiguous_origin(value, directory):
    for path in _metadata_files(directory):
        if path.suffix != ".json":
            if not re.fullmatch(r"[0-9a-f]{64}\.lock", path.name):
                raise ValueError("unrecognized protected origin metadata member")
            continue
        record = read(path)
        if not isinstance(record, dict) or not set(value) <= set(record):
            raise ValueError("unrecognized protected origin metadata")
        observed = {field: record[field] for field in value}
        if path.name != key(observed) + ".json":
            raise ValueError("ambiguous or corrupt protected runtime origin")


def _require_no_partial_origin(value):
    root = storage(readonly=True)
    runtime = Path(value["repository_identity"]["root"]) / ("." + value["platform"]) / "al-runtime"
    for path in (
        runtime / (value["session_id"] + ".json"),
        runtime / "sessions" / value["session_id"],
        root / "successors" / ("initial-" + key(value)),
        root / "successor-fences" / (key(value) + ".json"),
    ):
        path = safe(path)
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise ValueError("partial runtime origin exists without protected binding")
    for path in _metadata_files(root / "initializations"):
        raw, metadata = capture(path)
        if metadata["mode"] != 0o600 or path.name != digest(raw):
            raise ValueError("protected initialization metadata changed")
        record = decode(raw)
        if not isinstance(record, dict) or not {"repository_identity", "session_id"} <= set(record):
            raise ValueError("unrecognized protected initialization metadata")
        if (
            record["repository_identity"] == value["repository_identity"]
            and record["session_id"] == value["session_id"]
        ):
            raise ValueError("protected initialization exists without runtime binding")
    _require_no_orphan_durable_origin(root / "harness-runtimes", value["session_id"])


def _require_no_orphan_durable_origin(root, session):
    from .runtime_pin import _directory

    root = safe(root)
    try:
        root.lstat()
    except FileNotFoundError:
        return
    _directory(root, create=False)
    patterns = (r"[0-9a-f]{64}", r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", r"legacy-0|epoch-[0-9a-f]{8}")
    pending, count = [(root, 0)], 0
    while pending:
        directory, depth = pending.pop()
        if depth == len(patterns):
            for name in (session + ".json", session):
                path = safe(directory / name)
                try:
                    path.lstat()
                except FileNotFoundError:
                    continue
                raise ValueError("orphaned durable runtime origin exists")
            continue
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > 10000:
                    raise ValueError("runtime origin structural inventory exceeds bound")
                path = safe(entry.path)
                if not re.fullmatch(patterns[depth], entry.name) or not entry.is_dir(
                    follow_symlinks=False
                ):
                    raise ValueError("unrecognized durable runtime structural member")
                _directory(path, create=False)
                pending.append((path, depth + 1))
