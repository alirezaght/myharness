"""Close abandoned AL runs restorably so they stop deferring installer updates (#168).

An update is deferred while any run it can see is potentially resumable: every runtime state
that is not APPROVED/DONE, a deliberately stopped one included. A run that was simply left
behind therefore blocks updates forever, and closing it from inside AL needs its pinned
runtime, which an old run may not be able to provide.

`alctl close-runs` lists exactly the runs that defer an update (the same activity scan the
update uses). With `--apply` it archives the named sessions: their runtime state, companion
directory, session lock and a `current` pointer naming them move into
`<runtime root>/runtime-archive/<stamp>/`, and their protected run-pin binding records into
`runtime-archive/<stamp>/al-run-pins/` beside the registry, under a manifest that records the
reason. Nothing is deleted and no state byte changes: every archived path is restored by moving
it back to its recorded source. Only a run that is potentially resumable is closed; a run whose
state is malformed or has an unresolved writer or running check is reported and left alone,
and a run whose lock is held is skipped.
"""

import os
from pathlib import Path

from .guards import activity_roots, scan_activity
from .registry import records as registry_records
from .runtime_stale_runs import KEY, _archive_root, _files, _locked, _read, _write_manifest
from .runtime_stale_runs import records as binding_records
from .validator_origin import capture, digest, safe

MANIFEST_KIND = "al-closed-run-archive-v1"
ARCHIVE = "runtime-archive"


def _scan(state_root, runtime_roots, invoking):
    rows = registry_records(state_root)
    return scan_activity(activity_roots(rows, explicit=runtime_roots, invoking=invoking, state_root=state_root))


def _inside(path, root):
    try:
        Path(path).relative_to(root)
    except ValueError:
        return False
    return True


def _binding(storage, repository, session, platform):
    """The protected binding of one run, matched on repository, session and (when known) platform."""
    found = []
    for path in _files(storage / "runs"):
        if path.suffix != ".json" or not KEY.fullmatch(path.stem) or path.is_symlink():
            continue
        try:
            _, value = _read(path)
        except (OSError, ValueError):
            continue
        identity = value.get("repository_identity") if isinstance(value, dict) else None
        if (isinstance(identity, dict) and identity.get("root") == repository
                and value.get("session_id") == session
                and (platform is None or value.get("platform") == platform)):
            found.append((path, value))
    if len(found) > 1:
        raise ValueError("ambiguous protected run binding for session " + session)
    return found[0] if found else (None, None)


def _candidate(storage, blocker):
    path = Path(blocker["path"])
    root, session = path.parent, path.stem
    raw, _ = capture(path)
    from .registry import read_json

    state = read_json(path)
    repository = state["identity"]["root"]
    durable = _inside(root, storage)
    platform = None if durable else root.parent.name.lstrip(".")
    binding, value = _binding(storage, repository, session, platform) if storage.exists() else (None, None)
    if durable and binding is None:
        raise ValueError("durable run has no protected binding")
    return {
        "session_id": session,
        "runtime_root": str(root),
        "state": str(path),
        "state_sha256": digest(raw),
        "repository": repository,
        "platform": platform or (value or {}).get("platform"),
        "run_id": state.get("run_id"),
        "status": state.get("status"),
        "phase": state.get("phase"),
        "layout": "durable" if durable else "flat",
        "binding": str(binding) if binding else None,
        "reason": blocker["reason"],
        "_value": value,
    }


def _public(item):
    return {k: v for k, v in item.items() if not k.startswith("_")}


def blocking_runs(state_root, *, runtime_roots=(), invoking=None):
    """The potentially resumable runs that defer an update, and every other blocker."""
    state_root = safe(state_root)
    storage = state_root / "al-run-pins"
    report = _scan(state_root, runtime_roots, invoking)
    closable, others = [], []
    for blocker in report["blockers"]:
        if blocker.get("category") != "terminal-workflow" or not blocker["path"].endswith(".json"):
            others.append(blocker)
            continue
        try:
            closable.append(_candidate(storage, blocker))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            others.append(dict(blocker, reason=blocker["reason"] + "; not closable: " + str(exc)))
    return closable, others, report


def _flat_sources(item):
    root, session = Path(item["runtime_root"]), item["session_id"]
    sources = []
    pointer = root / "current"
    # The pointer moves before the state so the root never holds a dangling pointer.
    if pointer.is_file() and not pointer.is_symlink() and pointer.read_text().strip() == session:
        sources.append(pointer)
    sources.append(root / (session + ".json"))
    for candidate in (root / session, root / ("." + session + ".lock")):
        if os.path.lexists(candidate):
            sources.append(candidate)
    return sources


def close_runs(state_root, sessions=(), *, reason="", apply=False, runtime_roots=(), invoking=None, now=None):
    """Report blocking runs; with `apply`, archive the named sessions restorably."""
    import time

    state_root = safe(state_root)
    storage = state_root / "al-run-pins"
    closable, others, report = blocking_runs(state_root, runtime_roots=runtime_roots, invoking=invoking)
    result = {"apply": bool(apply), "closable": [_public(item) for item in closable], "other_blockers": others,
              "roots": report["roots"], "closed": [], "skipped": [], "archive": None,
              "usage": "alctl close-runs --session <id> [--session <id> ...] --reason <text> --apply"}
    if not apply:
        return result
    sessions = list(dict.fromkeys(sessions))
    if not sessions:
        raise ValueError("close-runs --apply requires at least one --session")
    if not reason.strip():
        raise ValueError("close-runs --apply requires a --reason")
    selected = []
    for session in sessions:
        matches = [item for item in closable if item["session_id"] == session]
        if len(matches) != 1:
            result["skipped"].append({"session_id": session, "reason": "not a potentially resumable run that defers updates"
                                      if not matches else "session names more than one blocking run"})
        else:
            selected.append(matches[0])
    if not selected:
        return result
    archive = _archive_root(state_root, time.time() if now is None else now)
    manifest_path = archive / "manifest.json"
    manifest = {
        "schema_version": 1,
        "kind": MANIFEST_KIND,
        "archived_at": archive.name,
        "reason": reason,
        "registry_root": str(storage),
        "restore": "move each entry's 'archived' path back to its 'source' path",
        "status": "IN_PROGRESS",
        "runs": [],
    }
    _write_manifest(manifest_path, manifest)
    for item in selected:
        descriptors = []
        try:
            locks = [Path(item["binding"]).with_suffix(".lock")] if item["binding"] else []
            if item["layout"] == "flat":
                locks.append(Path(item["runtime_root"]) / ("." + item["session_id"] + ".lock"))
            busy = False
            for lock in locks:
                descriptor = _locked(lock)
                if descriptor is None:
                    busy = True
                    break
                descriptors.append(descriptor)
            if busy:
                result["skipped"].append({"session_id": item["session_id"], "reason": "run lock is held"})
                continue
            # Re-check under the locks: the state is unchanged and still only potentially resumable.
            current = [blocker for blocker in scan_activity([(Path(item["runtime_root"]), item["repository"])])["blockers"]
                       if blocker["path"] == item["state"]]
            try:
                unchanged = digest(capture(Path(item["state"]))[0]) == item["state_sha256"]
            except (OSError, ValueError):
                unchanged = False
            if not unchanged or [b.get("category") for b in current] != ["terminal-workflow"]:
                result["skipped"].append({"session_id": item["session_id"], "reason": "run changed while closing"})
                continue
            entry = dict(_public(item), moved=[])
            manifest["runs"].append(entry)
            moves = []
            if item["layout"] == "flat":
                local = safe(Path(item["runtime_root"]) / ARCHIVE / archive.name)
                local.mkdir(mode=0o700, parents=True, exist_ok=True)
                moves.extend((source, local / source.name) for source in _flat_sources(item))
            if item["binding"]:
                binding = Path(item["binding"])
                moves.extend((source, archive / "al-run-pins" / source.relative_to(storage))
                             for source in binding_records(storage, binding, item["_value"]))
            for source, destination in moves:
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if os.path.lexists(destination):
                    raise ValueError("archive destination exists: " + str(destination))
                entry["moved"].append({"source": str(source), "archived": str(destination)})
                _write_manifest(manifest_path, manifest)
                os.rename(source, destination)
            result["closed"].append(entry)
        finally:
            for descriptor in descriptors:
                if descriptor >= 0:
                    os.close(descriptor)
    manifest["status"] = "COMPLETE"
    _write_manifest(manifest_path, manifest)
    for entry in manifest["runs"]:
        if entry["layout"] == "flat":
            _write_manifest(Path(entry["runtime_root"]) / ARCHIVE / archive.name / "manifest.json", manifest)
    result["archive"] = str(archive)
    return result
