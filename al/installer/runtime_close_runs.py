"""Close abandoned AL runs restorably so they stop deferring installer updates (#168).

An update is deferred while any run it can see is potentially resumable: every runtime state
that is not APPROVED/DONE, a deliberately stopped one included. A run that was simply left
behind therefore blocks updates forever, and closing it from inside AL needs its pinned
runtime, which an old run may not be able to provide.

`alctl close-runs` lists exactly the runs that defer an update (the same activity scan the
update uses). With `--apply` it archives the named sessions: their runtime state, companion
directory, session lock and the `current` and native conversation pointers naming them move
into `<runtime root>-archive/<stamp>/` beside (never inside) the runtime root, and their protected
run-pin binding records into `runtime-archive/<stamp>/al-run-pins/` beside the registry, under a
manifest that records the reason. Nothing is deleted and no state byte changes: every archived
path is restored by moving it back to its recorded source. Only a run that is potentially
resumable is closed; a run whose state is malformed or has an unresolved writer or running check
is reported and left alone, and a run whose binding, session or conversation lock is held is
skipped.
"""

import os
import time
from pathlib import Path

from .guards import activity_roots, scan_activity
from .registry import records as registry_records
from .runtime_stale_runs import KEY, _archive_root, _files, _locked, _read, _write_manifest
from .runtime_stale_runs import records as binding_records
from .validator_origin import capture, digest, safe

MANIFEST_KIND = "al-closed-run-archive-v1"
PLATFORMS = {".codex": "codex", ".claude": "claude"}


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
    # Only the conventional `<repo>/.<platform>/al-runtime` layout names its platform.
    platform = None if durable else PLATFORMS.get(root.parent.name) if root.name == "al-runtime" else None
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
        # A run between commands looks like an abandoned one; its last change helps the operator tell.
        "last_modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime)),
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


def _conversations(root, session):
    """Native conversation pointers (and their locks) that name the session."""
    import json

    found = []
    directory = root / "conversations"
    for path in _files(directory) if directory.is_dir() and not directory.is_symlink() else []:
        if path.suffix != ".json" or path.is_symlink() or not path.is_file():
            continue
        try:
            value = json.loads(path.read_bytes())
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get("session_id") == session:
            found.append(path)
    return found


def _flat_sources(item, conversations):
    root, session = Path(item["runtime_root"]), item["session_id"]
    sources = []
    pointer = root / "current"
    # Pointers move before the state so the root never holds a dangling pointer.
    if pointer.is_file() and not pointer.is_symlink() and pointer.read_text().strip() == session:
        sources.append(pointer)
    for conversation in conversations:
        sources.append(conversation)
        if os.path.lexists(conversation.with_suffix(".lock")):
            sources.append(conversation.with_suffix(".lock"))
    sources.append(root / (session + ".json"))
    for candidate in (root / session, root / ("." + session + ".lock"), root / ("." + session + ".lock.holder")):
        if os.path.lexists(candidate):
            sources.append(candidate)
    return sources


def _local_archive(item, stamp):
    """Flat runtime files archive beside the runtime root: workspace inventory walks inside it."""
    root = Path(item["runtime_root"])
    base = safe(root.parent / (root.name + "-archive"))
    base.mkdir(mode=0o700, exist_ok=True)
    ignore = base / ".gitignore"
    if not os.path.lexists(ignore):
        ignore.write_text("*\n")  # like the runtime root, the archive never shows up in Git
    return safe(base / stamp)


def close_runs(state_root, sessions=(), *, reason="", apply=False, runtime_roots=(), invoking=None, now=None):
    """Report blocking runs; with `apply`, archive the named sessions restorably."""
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
        "restore": ("move each entry's 'archived' path back to its 'source' path, in reverse order; "
                    "after an interruption trust where each path exists over its 'moved' flag"),
        "status": "IN_PROGRESS",
        "runs": [],
    }
    _write_manifest(manifest_path, manifest)
    failure = None
    for item in selected:
        descriptors = []
        root = Path(item["runtime_root"])
        try:
            locks = [Path(item["binding"]).with_suffix(".lock")] if item["binding"] else []
            # The session lock is what every AL command of the run holds, in both layouts.
            locks.append(root / ("." + item["session_id"] + ".lock"))
            # One pointer listing serves both the locks and the moves: none moves without its lock.
            conversations = _conversations(root, item["session_id"]) if item["layout"] == "flat" else []
            locks.extend(path.with_suffix(".lock") for path in conversations)
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
            # Re-check under the locks: the state is unchanged and still only potentially resumable,
            # and the binding (re-read here) still belongs to this run.
            current = [blocker for blocker in scan_activity([(root, item["repository"])])["blockers"]
                       if blocker["path"] == item["state"]]
            try:
                unchanged = digest(capture(Path(item["state"]))[0]) == item["state_sha256"]
            except (OSError, ValueError):
                unchanged = False
            value = None
            if item["binding"]:
                try:
                    _, value = _read(Path(item["binding"]))
                except (OSError, ValueError):
                    unchanged = False
                else:
                    if not isinstance(value, dict) or value.get("session_id") != item["session_id"] or (
                            (value.get("repository_identity") or {}).get("root") != item["repository"]):
                        unchanged = False
            if not unchanged or [b.get("category") for b in current] != ["terminal-workflow"]:
                result["skipped"].append({"session_id": item["session_id"], "reason": "run changed while closing"})
                continue
            entry = dict(_public(item), moved=[])
            manifest["runs"].append(entry)
            moves = []
            if item["layout"] == "flat":
                local = _local_archive(item, archive.name)
                entry["local_archive"] = str(local)
                moves.extend((source, local / source.relative_to(root)) for source in _flat_sources(item, conversations))
            if item["binding"]:
                binding = Path(item["binding"])
                moves.extend((source, archive / "al-run-pins" / source.relative_to(storage))
                             for source in binding_records(storage, binding, value))
            for source, destination in moves:
                if os.path.lexists(destination):
                    raise ValueError("archive destination exists: " + str(destination))
                move = {"source": str(source), "archived": str(destination), "moved": False}
                entry["moved"].append(move)
                _write_manifest(manifest_path, manifest)
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.rename(source, destination)
                move["moved"] = True
            result["closed"].append(entry)
        except Exception as exc:  # noqa: BLE001 - recorded in the manifest, then re-raised below
            # Stop at the first failure; the manifest says exactly which moves happened.
            failure = exc
            if manifest["runs"] and manifest["runs"][-1]["session_id"] == item["session_id"]:
                manifest["runs"][-1]["error"] = str(exc)
            break
        finally:
            for descriptor in descriptors:
                if descriptor >= 0:
                    os.close(descriptor)
    manifest["status"] = "FAILED" if failure else "COMPLETE"
    _write_manifest(manifest_path, manifest)
    for entry in manifest["runs"]:
        if entry.get("local_archive") and Path(entry["local_archive"]).is_dir():
            _write_manifest(Path(entry["local_archive"]) / "manifest.json", manifest)
    result["archive"] = str(archive)
    if failure:
        raise ValueError("close-runs stopped; see " + str(manifest_path) + ": " + str(failure)) from failure
    return result
