"""Exact operator-classified template versions; no general protected-file exception."""

import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
from pathlib import Path, PurePosixPath

from harness.command_safety import git_argv, safe_environment
from harness.execution_contract import canonical
from harness.execution_files import PROTECTED

ERROR = "template classification policy refused; inspect bindings and reclassify exact versions"
MAX_RECORD = 4 * 1024 * 1024


def _fail():
    raise ValueError(ERROR)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _ordinary(path, maximum=MAX_RECORD):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        _fail()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
            _fail()
        raw = os.read(fd, maximum + 1)
        after = os.fstat(fd)
        if len(raw) > maximum or any(
            getattr(before, k) != getattr(after, k)
            for k in ("st_dev", "st_ino", "st_size", "st_mode", "st_mtime_ns")
        ):
            _fail()
        if path.stat().st_ino != before.st_ino:
            _fail()
        return raw, stat.S_IMODE(before.st_mode)
    finally:
        os.close(fd)


def _git(root, *args):
    result = subprocess.run(
        git_argv(args), cwd=root, env=safe_environment(), capture_output=True, timeout=10
    )
    if result.returncode or len(result.stdout) > 16 * 1024 * 1024:
        _fail()
    return result.stdout


def protected_name(path):
    credential_names = (PROTECTED - {".git", ".codex", ".claude", ".agents"}) | {
        "auth.json",
        ".credentials.json",
    }
    return any(p.startswith(".env") or p in credential_names for p in PurePosixPath(path).parts)


def _path(path):
    rel = PurePosixPath(path)
    if (
        not isinstance(path, str)
        or not path
        or str(rel) != path
        or rel.is_absolute()
        or ".." in rel.parts
    ):
        _fail()
    if (
        not rel.name.startswith(".env")
        or not rel.name.endswith(".example")
        or any(p in PROTECTED or p.startswith(".env") for p in rel.parts[:-1])
    ):
        _fail()
    return rel


def _trees(root, baseline):
    values = {}
    for label, revision in (("baseline", baseline), ("HEAD", "HEAD")):
        if not re.fullmatch(r"[0-9a-f]{40,64}", baseline):
            _fail()
        rows = {}
        for raw in _git(root, "ls-tree", "-rz", revision).split(b"\0"):
            if not raw:
                continue
            metadata, name = raw.split(b"\t", 1)
            path = os.fsdecode(name)
            if protected_name(path):
                rows[path] = metadata.decode("ascii")
        values[label] = rows
    rows = {}
    for raw in _git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if raw:
            metadata, name = raw.split(b"\t", 1)
            path = os.fsdecode(name)
            if protected_name(path):
                rows[path] = metadata.decode("ascii")
    values["index"] = rows
    untracked = [
        os.fsdecode(p)
        for p in _git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        if p
    ]
    if any(protected_name(p) for p in untracked):
        _fail()
    return values


def validate_record(record, *, root=None):
    """Hash only explicitly classified files; enumerate other candidate members as metadata."""
    try:
        original = Path(record["repository"])
        target = Path(root) if root is not None else original
        files = record["files"]
        if not isinstance(files, list) or not files or len(files) > 1000:
            _fail()
        expected = {item["path"] for item in files}
        if len(expected) != len(files):
            _fail()
        if target.resolve(strict=True) == original.resolve(strict=True):
            rows = _trees(original, record["baseline"])
            if _git(original, "rev-parse", "HEAD").decode().strip() != record["HEAD"]:
                _fail()
            for label in ("baseline", "HEAD", "index"):
                if set(rows[label]) != expected or any(
                    rows[label][f["path"]] != f["versions"][label] for f in files
                ):
                    _fail()
        for item in files:
            relative = _path(item["path"])
            raw, mode = _ordinary(target / relative)
            oid = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            if mode != item["mode"] or _sha(raw) != item["sha256"] or oid != item["blob_oid"]:
                _fail()
        if target.resolve(strict=True) != original.resolve(strict=True):
            rows = _trees(target, record["baseline"])
            for label in ("baseline", "HEAD", "index"):
                if set(rows[label]) != expected or any(
                    rows[label][f["path"]] != f["versions"][label] for f in files
                ):
                    _fail()
        return [str(target / item["path"]) for item in files]
    except (OSError, KeyError, TypeError, ValueError, UnicodeError, subprocess.SubprocessError):
        raise ValueError(ERROR) from None


def _binding(execution, pin):
    return {
        k: execution[k]
        for k in ("id", "task_id", "binding", "handoff_hash", "al_run_id", "al_session_id")
    } | {"pin_identity": pin}


def selected_reference(execution, envelope, initialization):
    directory = Path(execution["handoff_path"]).parent
    pointer = directory / "template-classification.json"
    try:
        raw, _ = _ordinary(pointer)
    except FileNotFoundError:
        return None
    try:
        ref = json.loads(raw)
        if set(ref) != {"record", "sha256"} or not re.fullmatch(
            r"[0-9a-f]{64}\.json", ref["record"]
        ):
            _fail()
        path = directory / "template-classifications" / ref["record"]
        reference = {"path": str(path), "sha256": ref["sha256"]}
        record = read_reference(reference)
        if record["repository"] != envelope["repository"] or record["binding"] != _binding(
            execution, Path(initialization["pin_root"]).name
        ):
            _fail()
        validate_record(record)
        return reference
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ValueError(ERROR) from None


def read_reference(reference):
    try:
        raw, mode = _ordinary(reference["path"])
        if mode != 0o600 or _sha(raw) != reference["sha256"]:
            _fail()
        value = json.loads(raw)
        if value["kind"] != "exact-template-classification-v1":
            _fail()
        return value
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError(ERROR) from None


def validate_reference(reference, *, root=None):
    if reference is None:
        return []
    record = read_reference(reference)
    validate_record(record)
    return (
        validate_record(record, root=root)
        if root is not None
        else [str(Path(record["repository"]) / f["path"]) for f in record["files"]]
    )


def register_classification(
    controller,
    execution_id,
    inventory_path,
    inventory_sha256,
    classification_path,
    classification_sha256,
):
    """Trusted public CLI only: authority storage is outside every native write grant."""
    from harness.execution_authority import require_execution_authority
    from harness.execution_handoff import durable_file
    from harness.execution_hosts import managed_launcher
    from harness.execution_initialization import validate_existing_origin
    from harness.execution_store import get_execution, process_alive
    from harness.execution_worker import worker_lock

    execution = get_execution(controller.conn, execution_id)
    try:
        with worker_lock(execution):
            execution = get_execution(controller.conn, execution_id)
            envelope = require_execution_authority(controller, execution_id, operation="read")
            if execution["status"] not in {
                "Interrupted",
                "Unavailable",
                "RecoveryRequired",
                "Completed",
                "Failed",
            }:
                _fail()
            root = Path(envelope["repository"]).resolve(strict=True)
            directory = Path(execution["handoff_path"]).parent
            if directory.resolve().is_relative_to(root):
                _fail()
            # Actual exclusive creation, not os.access; native operation grants cannot write here.
            probe = directory / (".template-operator-" + secrets.token_hex(12))
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            probe.unlink()
            attempts = controller.conn.execute(
                "SELECT child_pid,status FROM execution_attempts WHERE execution_id=?",
                (execution_id,),
            ).fetchall()
            if any(
                r["status"] not in {"Exited", "Reconciled", "PreSpawnFailed"}
                or process_alive(r["child_pid"])
                for r in attempts
            ):
                _fail()
            initial = validate_existing_origin(execution, envelope, managed_launcher())
            source_raw, _ = _ordinary(initial["source"], 32 * 1024 * 1024)
            state = json.loads(source_raw)
            if (
                state["run_id"] != execution["al_run_id"]
                or any(
                    w["state"] in {"ACTIVE", "UNRESOLVED"}
                    for w in state.get("writers", {}).values()
                )
                or any(
                    e["status"] == "RUNNING" for e in state.get("evidence", {}).get("entries", [])
                )
            ):
                _fail()
            inventory_raw, _ = _ordinary(inventory_path)
            classification_raw, _ = _ordinary(classification_path)
            if (
                _sha(inventory_raw) != inventory_sha256
                or _sha(classification_raw) != classification_sha256
            ):
                _fail()
            inventory, classification = json.loads(inventory_raw), json.loads(classification_raw)
            if (
                set(classification) != {"question", "answer", "inventory_sha256", "source"}
                or classification["inventory_sha256"] != inventory_sha256
                or any(
                    not isinstance(classification[k], str) or not classification[k].strip()
                    for k in classification
                )
            ):
                _fail()
            if (
                inventory["schema_version"] != 1
                or inventory["repository"] != str(root)
                or inventory["execution"] != execution_id
                or inventory["AL_run"] != execution["al_run_id"]
            ):
                _fail()
            baseline = (
                state["identity"]["merge_base"]
                if state.get("review_mode") == "whole-mr"
                else state["baseline_head"]
            )
            if inventory["baseline"] != baseline:
                _fail()
            files = []
            for item in inventory["files"]:
                relative = _path(item["path"])
                raw, mode = _ordinary(root / relative)
                version = item["versions"]["worktree"]
                oid = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
                if (
                    version != {"blob_oid": oid, "mode": oct(mode), "symlink": False, "nlink": 1}
                    or item["versions"]["baseline"] != f"100644 blob {oid}"
                    or item["versions"]["HEAD"] != f"100644 blob {oid}"
                    or item["versions"]["index"] != f"100644 {oid} 0"
                    or mode != 0o644
                ):
                    _fail()
                files.append(
                    {
                        "path": item["path"],
                        "versions": item["versions"],
                        "mode": mode,
                        "blob_oid": oid,
                        "sha256": _sha(raw),
                    }
                )
            record = {
                "kind": "exact-template-classification-v1",
                "repository": str(root),
                "binding": _binding(execution, Path(initial["pin_root"]).name),
                "baseline": baseline,
                "HEAD": inventory["HEAD"],
                "inventory_sha256": inventory_sha256,
                "classification_sha256": classification_sha256,
                "classification": classification,
                "files": files,
            }
            validate_record(record)
            raw = canonical(record).encode()
            sha = _sha(raw)
            target = directory / "template-classifications" / (sha + ".json")
            durable_file(target, raw)
            # Publish only the operator-selected immutable record.
            temporary = directory / (".template-selection-" + secrets.token_hex(12))
            try:
                durable_file(temporary, canonical({"record": target.name, "sha256": sha}).encode())
                os.replace(temporary, directory / "template-classification.json")
                parent_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
            finally:
                temporary.unlink(missing_ok=True)
            return {
                "status": "Registered",
                "classification_sha256": sha,
                "files": len(files),
                "execution": execution_id,
            }
    except (OSError, KeyError, TypeError, ValueError, UnicodeError, subprocess.SubprocessError):
        raise ValueError(ERROR) from None


def prepared_context(value):
    """An immutable issuer plan, never an ACTIVE-origin substitute."""
    fields = {
        "schema_version",
        "request_id",
        "preparation_identity",
        "plan_ref",
        "repository",
        "predecessor",
        "successor",
        "baseline",
        "HEAD",
        "start_operation",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value["schema_version"] != 1
        or value["start_operation"] != "managed-successor-start-v1"
    ):
        _fail()
    reference = value["plan_ref"]
    raw, mode = _ordinary(reference["stored_path"])
    if (
        mode != 0o600
        or _sha(raw) != reference["sha256"]
        or reference["sha256"] != value["preparation_identity"]
    ):
        _fail()
    plan = json.loads(raw)
    expected = Path(plan["storage"]) / "successors" / value["request_id"] / "plan.json"
    if (
        Path(reference["stored_path"]) != expected
        or expected.is_relative_to(Path(value["repository"]))
        or plan["kind"] != "successor-start-plan-v1"
        or plan["request"]["request_id"] != value["request_id"]
        or plan["successor_transaction"] != value["successor"]
        or plan["predecessor_transaction"] != value["predecessor"]
        or plan["source_anchors"]["baseline_head_oid"] != value["baseline"]
        or plan["source_anchors"]["observed_head_oid"] != value["HEAD"]
        or plan["predecessor_transaction"]["repository_identity"]["root"] != value["repository"]
    ):
        _fail()
    return plan


def prepare_successor_classification(predecessor_reference, context):
    """Carry unchanged original user authorization into one reserved start operation."""
    plan = prepared_context(context)
    record = read_reference(predecessor_reference) if predecessor_reference else None
    if record is not None:
        binding = record["binding"]
        prior = context["predecessor"]
        if (
            binding["al_session_id"] != prior["session_id"]
            or binding["al_run_id"] != prior["run_id"]
            or binding["pin_identity"] != prior["pin"]["pin_identity"]
            or record["repository"] != context["repository"]
        ):
            _fail()
        validate_record(record)
        classification = record["classification"]
        if classification["inventory_sha256"] != record["inventory_sha256"] or not re.fullmatch(
            r"[a-f0-9]{64}", record["classification_sha256"]
        ):
            _fail()
    else:
        # With no exception, candidate membership must contain no protected version.
        rows = _trees(Path(context["repository"]), context["baseline"])
        if any(rows.values()):
            _fail()
    value = {
        "kind": "prepared-exact-template-classification-v1",
        "context": context,
        "predecessor_reference": predecessor_reference,
        "original_inventory_sha256": record["inventory_sha256"] if record else None,
        "original_classification_sha256": record["classification_sha256"] if record else None,
    }
    raw = canonical(value).encode()
    sha = _sha(raw)
    path = Path(plan["authority_directory"]) / "prepared-template-classifications" / (sha + ".json")
    from harness.execution_handoff import durable_file

    durable_file(path, raw)
    return {"path": str(path), "sha256": sha}


def validate_prepared_classification(reference, context):
    prepared_context(context)
    raw, mode = _ordinary(reference["path"])
    if mode != 0o600 or _sha(raw) != reference["sha256"]:
        _fail()
    value = json.loads(raw)
    if (
        set(value)
        != {
            "kind",
            "context",
            "predecessor_reference",
            "original_inventory_sha256",
            "original_classification_sha256",
        }
        or value["kind"] != "prepared-exact-template-classification-v1"
        or value["context"] != context
    ):
        _fail()
    ref = value["predecessor_reference"]
    if ref is None:
        if any(_trees(Path(context["repository"]), context["baseline"]).values()):
            _fail()
        return []
    record = read_reference(ref)
    prior = context["predecessor"]
    if (
        record["inventory_sha256"] != value["original_inventory_sha256"]
        or record["classification_sha256"] != value["original_classification_sha256"]
        or record["binding"]["al_session_id"] != prior["session_id"]
        or record["binding"]["al_run_id"] != prior["run_id"]
        or record["binding"]["pin_identity"] != prior["pin"]["pin_identity"]
        or record["repository"] != context["repository"]
    ):
        _fail()
    return validate_record(record)


def finalize_successor_classification(execution, receipt):
    """Rebind only the exact original authorized versions to genuine initialized identity."""
    from harness.execution_handoff import durable_file
    from harness.execution_initialization_channel import preparation_context

    plan_ref = {
        "stored_path": str(Path(receipt["lineage_ref"]["stored_path"]).parent / "plan.json"),
        "sha256": receipt["preparation_sha256"],
    }
    context = preparation_context(plan_ref)
    validate_prepared_classification(receipt["prepared_classification_ref"], context)
    grant = json.loads(_ordinary(receipt["prepared_classification_ref"]["path"])[0])
    if grant["predecessor_reference"] is None:
        return None
    old = read_reference(grant["predecessor_reference"])
    proposed = dict(
        execution,
        al_session_id=receipt["successor_transaction"]["session_id"],
        al_run_id=receipt["successor_transaction"]["run_id"],
        handoff_hash=receipt["successor_handoff_ref"]["sha256"],
    )
    record = dict(
        old, binding=_binding(proposed, receipt["successor_transaction"]["pin"]["pin_identity"])
    )
    validate_record(record)
    raw = canonical(record).encode()
    directory = Path(receipt["successor_handoff_ref"]["path"]).parent
    path = directory / "template-classifications" / (_sha(raw) + ".json")
    durable_file(path, raw)
    durable_file(
        directory / "template-classification.json",
        canonical({"record": path.name, "sha256": _sha(raw)}).encode(),
    )
    return {"path": str(path), "sha256": _sha(raw)}
