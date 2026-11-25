"""Stale run bindings: protected run pins whose repository root no longer exists (#37).

A binding outlives its repository when the repository is deleted (a test's temporary
repository, a removed worktree). Such a run can never resume, so diagnostics report it
as stale instead of failing, and `alctl prune-stale-runs --apply` moves its records into
`runtime-archive/<timestamp>/` beside the registry with a manifest. Nothing is deleted:
every archived path is restored by moving it back to its recorded source.
"""

import fcntl
import os
import re
import time
from pathlib import Path

from .validator_origin import capture, decode, digest, safe

KEY = re.compile(r"[0-9a-f]{64}")
LIMIT = 10000
MANIFEST_KIND = "al-stale-run-archive-v1"


def _repository_root(value):
    identity = value.get("repository_identity") if isinstance(value, dict) else None
    root = identity.get("root") if isinstance(identity, dict) else None
    return root if isinstance(root, str) and os.path.isabs(root) else None


def is_stale(value):
    """A binding is stale only when its recorded repository root path is missing."""
    root = _repository_root(value)
    return root is not None and not os.path.lexists(root)


def _read(path):
    raw, _ = capture(path)
    return raw, decode(raw)


def _files(directory):
    try:
        entries = sorted(directory.iterdir())
    except FileNotFoundError:
        return []
    if len(entries) > LIMIT:
        raise ValueError("protected run storage enumeration exceeds limit")
    return entries


def _initializations(storage, value):
    """Initialization records of the binding: its reference and any record of its subject."""
    directory = storage / "initializations"
    found = set()
    reference = value.get("initialization")
    if isinstance(reference, dict) and KEY.fullmatch(str(reference.get("sha256", ""))):
        candidate = directory / reference["sha256"]
        if candidate.is_file() and not candidate.is_symlink():
            found.add(candidate)
    for path in _files(directory):
        if not KEY.fullmatch(path.name) or path.is_symlink() or not path.is_file():
            continue
        try:
            raw, record = _read(path)
        except (OSError, ValueError):
            continue
        if (digest(raw) == path.name and isinstance(record, dict)
                and record.get("repository_identity") == value.get("repository_identity")
                and record.get("session_id") == value.get("session_id")):
            found.add(path)
    return sorted(found)


def _runtime_directory(storage, value):
    """The durable epoch directory of a v3 binding, only when it is issuer-derived."""
    binding = value.get("harness_binding")
    if value.get("schema_version") != 3 or not isinstance(binding, dict):
        return None
    parts = [binding.get(k) for k in ("authority_identity", "execution_id", "epoch_id")]
    if not all(isinstance(part, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", part)
               for part in parts):
        return None
    expected = storage / "harness-runtimes" / parts[0] / parts[1] / parts[2]
    if value.get("runtime_directory") != str(expected) or not expected.is_dir() or expected.is_symlink():
        return None
    return expected


def records(storage, path, value):
    """Every existing registry path that belongs to the binding at `runs/<key>.json`."""
    key = path.stem
    paths = [path]
    for candidate in (storage / "successors" / ("initial-" + key),
                      storage / "successor-fences" / (key + ".json")):
        if os.path.lexists(candidate):
            paths.append(candidate)
    paths.extend(_initializations(storage, value))
    runtime = _runtime_directory(storage, value)
    if runtime is not None:
        paths.append(runtime)
    return paths


def stale_runs(storage_root):
    """Bindings under `<state_root>/al-run-pins` whose repository root is missing."""
    storage = safe(storage_root)
    found = []
    for path in _files(storage / "runs"):
        if path.suffix != ".json" or not KEY.fullmatch(path.stem) or path.is_symlink():
            continue
        try:
            _, value = _read(path)
        except (OSError, ValueError):
            continue
        if not isinstance(value, dict) or not is_stale(value):
            continue
        found.append({
            "binding": str(path),
            "repository": _repository_root(value),
            "platform": value.get("platform"),
            "session_id": value.get("session_id"),
            "run_id": value.get("run_id"),
            "status": value.get("status"),
            "schema_version": value.get("schema_version"),
            "records": [str(p) for p in records(storage, path, value)],
        })
    return found


def _archive_root(state_root, now):
    base = safe(Path(state_root).parent / "runtime-archive")
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    for index in range(1000):
        candidate = base / (stamp if index == 0 else stamp + "-" + str(index))
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            continue
        return candidate
    raise ValueError("no free runtime archive directory")


def _write_manifest(path, manifest):
    import json

    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _locked(lock):
    """Hold the binding's publication lock without creating one; None when it is busy."""
    if not lock.is_file() or lock.is_symlink():
        return -1
    descriptor = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    return descriptor


def prune_stale_runs(state_root, *, apply=False, now=None):
    """Report stale bindings; with `apply`, archive their records restorably."""
    state_root = safe(state_root)
    storage = state_root / "al-run-pins"
    stale = stale_runs(storage) if storage.exists() else []
    result = {"apply": bool(apply), "registry": str(storage), "stale": stale,
              "archived": [], "skipped": [], "archive": None}
    if not apply or not stale:
        return result
    archive = _archive_root(state_root, time.time() if now is None else now)
    manifest_path = archive / "manifest.json"
    manifest = {
        "schema_version": 1,
        "kind": MANIFEST_KIND,
        "archived_at": archive.name,
        "reason": "run bindings whose repository root no longer exists (alctl prune-stale-runs)",
        "registry_root": str(storage),
        "restore": "move each entry's 'archived' path back to its 'source' path",
        "status": "IN_PROGRESS",
        "bindings": [],
    }
    _write_manifest(manifest_path, manifest)
    for item in stale:
        binding = Path(item["binding"])
        descriptor = _locked(binding.with_suffix(".lock"))
        if descriptor is None:
            result["skipped"].append({"binding": str(binding), "reason": "binding lock is held"})
            continue
        try:
            # Re-check under the lock: the binding is unchanged and its repository still missing.
            try:
                _, value = _read(binding)
            except (OSError, ValueError) as exc:
                result["skipped"].append({"binding": str(binding), "reason": str(exc)})
                continue
            if not is_stale(value):
                result["skipped"].append({"binding": str(binding), "reason": "repository exists"})
                continue
            entry = dict(item, moved=[])
            manifest["bindings"].append(entry)
            for source in records(storage, binding, value):
                destination = archive / "al-run-pins" / source.relative_to(storage)
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                entry["moved"].append({"source": str(source), "archived": str(destination)})
                _write_manifest(manifest_path, manifest)
                os.rename(source, destination)
            result["archived"].append(entry)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    manifest["status"] = "COMPLETE"
    _write_manifest(manifest_path, manifest)
    result["archive"] = str(archive)
    return result
