"""Preserve original requirement and comparison anchors without importing authority."""

import copy
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

from .runtime_pin_transaction import runtime_directory
from .runtime_successor_store import immutable
from .validator_origin import capture, decode, digest, safe


def artifact(reference, runtime):
    required = {"id", "kind", "sha256", "stored_path", "source", "captured_at"}
    if not isinstance(reference, dict) or set(reference) != required:
        raise ValueError("invalid inherited artifact reference")
    path = safe(reference["stored_path"])
    expected = Path(runtime) / reference["sha256"]
    if path != expected or reference["id"] != reference["sha256"]:
        raise ValueError("inherited artifact escaped its original storage")
    raw, info = capture(path)
    if info["mode"] != 0o600 or path.stat().st_nlink != 1 or digest(raw) != reference["sha256"]:
        raise ValueError("inherited artifact changed")
    return raw


def revisions(package_sha, amendments):
    values, hashes = [], []
    for amendment in [None, *amendments]:
        if amendment is not None:
            values.append(amendment["artifact"]["sha256"])
        hashes.append(
            digest(
                json.dumps({"original": package_sha, "amendments": values}, sort_keys=True).encode()
            )
        )
    return hashes


def git(root, *args):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/var/empty",
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    result = subprocess.run(
        [
            "/usr/bin/git",
            "--no-optional-locks",
            "--literal-pathspecs",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        cwd=root,
        env=env,
        capture_output=True,
        timeout=30,
    )
    if result.returncode or len(result.stdout) > 16 * 1024 * 1024:
        raise ValueError("successor source metadata observation unavailable")
    return result.stdout


def requirement_lineage(predecessor, state, successor, destination, approved_scopes):
    """The authority endpoint independently verifies scoped delivery/Registry acceptance."""
    runtime = runtime_directory(predecessor) / predecessor["session_id"] / "artifacts"
    package = state["task_package"]
    package_raw = artifact(package, runtime)
    accepted, copied, payloads = [], [], []
    new_runtime = runtime_directory(successor) / successor["session_id"] / "artifacts"
    new_runtime.mkdir(mode=0o700, parents=True, exist_ok=True)

    def copy_artifact(ref, raw):
        path = new_runtime / ref["sha256"]
        if path.exists():
            if capture(path)[0] != raw:
                raise ValueError("successor requirement artifact collision")
        else:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        return dict(
            ref,
            stored_path=str(path),
            captured_at=path.stat().st_mtime,
            source="authenticated predecessor lineage",
        )

    package_copy = copy_artifact(package, package_raw)
    for original in state["amendments"]:
        amendment = copy.deepcopy(original)
        raw = artifact(amendment["artifact"], runtime)
        payload = decode(raw)
        for key in ("id", "text", "source", "affected_criteria"):
            if payload.get(key) != amendment.get(key):
                raise ValueError("inherited amendment payload differs")
        if amendment.get("scoped"):
            keys = ("scoped", "affected_tasks", "base_revision", "delivery_hash", "registry")
            if any(payload.get(k) != amendment.get(k) for k in keys):
                raise ValueError("inherited scoped amendment differs")
            if approved_scopes.get(amendment["id"]) != digest(raw):
                raise ValueError("inherited scoped amendment lacks accepted provenance")
        accepted.append(copy.deepcopy(original))
        amendment["artifact"] = copy_artifact(amendment["artifact"], raw)
        copied.append(amendment)
        payloads.append(digest(raw))
    hashes = revisions(package["sha256"], accepted)
    original = {
        "schema_version": 1,
        "kind": "successor-requirement-lineage-v1",
        "predecessor": {
            k: predecessor[k]
            for k in (
                "repository_identity",
                "platform",
                "session_id",
                "run_id",
                "pin",
                "initialization",
            )
        },
        "successor": {
            k: successor[k]
            for k in ("repository_identity", "platform", "session_id", "run_id", "pin")
        },
        "original_package": package,
        "successor_package": package_copy,
        "amendments": accepted,
        "successor_amendments": copied,
        "revision_hashes": hashes,
        "requirement_lineage_hash": hashes[-1],
        "scoped_acceptance": approved_scopes,
    }
    return immutable(Path(destination) / "requirements.json", original)


def source_anchors(predecessor, state):
    root = state["identity"]["root"]
    for oid in (
        state["identity"]["target_oid"],
        state["identity"]["merge_base"],
        state["baseline_head"],
    ):
        if (
            git(root, "rev-parse", "--verify", "--end-of-options", oid + "^{commit}")
            .decode()
            .strip()
            != oid
        ):
            raise ValueError("preserved source anchor unavailable")
    return {
        "repository_identity": predecessor["repository_identity"],
        "target_label": state["identity"]["target"],
        "target_oid": state["identity"]["target_oid"],
        "merge_base_oid": state["identity"]["merge_base"],
        "baseline_head_oid": state["baseline_head"],
        "review_mode": state.get("review_mode"),
        "original_preexisting_changes": copy.deepcopy(state["preexisting_changes"]),
        "observed_head_oid": git(root, "rev-parse", "HEAD").decode().strip(),
        "predecessor_attribution": copy.deepcopy(state.get("task_attribution")),
        "verification_obligations": {
            "required": [
                "fresh service/dependency enumeration",
                "fresh required-check discovery and binding",
                "fresh execution of required evidence; no predecessor PASS is imported",
            ],
            "predecessor_check_areas": sorted(state["evidence"]["plan"]),
            "predecessor_open_issues": sorted(
                k
                for k, v in state.get("operational_issues", {}).items()
                if v["state"] != "Resolved"
            ),
            "predecessor_gate_manifests": copy.deepcopy(state.get("gate_manifests", [])),
        },
    }


def observe_candidate(root, baseline, classified_paths):
    root = Path(root)
    allowed = set(classified_paths)
    members = set()
    for revision in (baseline, "HEAD"):
        for raw in git(root, "ls-tree", "-rz", revision).split(b"\0"):
            if raw:
                members.add(os.fsdecode(raw.split(b"\t", 1)[1]))
    for raw in git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(
        b"\0"
    ):
        if raw:
            members.add(os.fsdecode(raw))
    protected = {
        ".netrc",
        ".git-credentials",
        ".mcp.json",
        ".npmrc",
        ".pypirc",
        ".ssh",
        ".aws",
        "auth.json",
        ".credentials.json",
    }
    for name in members:
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or any(p.startswith(".env") or p in protected for p in Path(name).parts)
            and str(root / name) not in allowed
        ):
            raise ValueError("unclassified protected candidate member refused")
    records, limitations = [], []
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in sorted(members):
            parent = os.dup(root_fd)
            try:
                parts = Path(name).parts
                for component in parts[:-1]:
                    try:
                        child = os.open(
                            component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                        )
                    except FileNotFoundError:
                        break
                    os.close(parent)
                    parent = child
                else:
                    records.append(
                        _candidate_member(
                            parent, parts[-1], name, str(root / name) in allowed, limitations
                        )
                    )
                    continue
                records.append({"path": name, "kind": "absent"})
            finally:
                os.close(parent)
    finally:
        os.close(root_fd)
    metadata = {
        "HEAD": git(root, "rev-parse", "HEAD").decode().strip(),
        "index": digest(git(root, "ls-files", "--stage", "-z")),
        "baseline": baseline,
        "records": records,
        "opaque_limitations": limitations,
    }
    from .validator_origin import canonical

    return {"sha256": digest(canonical(metadata)), "opaque_limitations": limitations}


def _candidate_member(parent, leaf, name, classified, limitations):
    try:
        before = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return {"path": name, "kind": "absent"}
    mode = stat.S_IMODE(before.st_mode)
    if stat.S_ISLNK(before.st_mode):
        if classified:
            raise ValueError("classified candidate alias refused")
        return {
            "path": name,
            "kind": "symlink",
            "mode": mode,
            "sha256": digest(os.fsencode(os.readlink(leaf, dir_fd=parent))),
        }
    if stat.S_ISDIR(before.st_mode):
        limitations.append(name)
        return {"path": name, "kind": "opaque-directory", "mode": mode}
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("candidate special file or hardlink refused")
    descriptor = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        h = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
        after = os.fstat(stream.fileno())
    current = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
    keys = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(
        getattr(before, k) != getattr(info, k) for info in (opened, after, current) for k in keys
    ):
        raise ValueError("candidate moved during preparation")
    return {"path": name, "kind": "file", "mode": mode, "sha256": h.hexdigest()}
