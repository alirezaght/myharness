"""Exact source selectors for the two reviewed isolated reader profiles."""

import hashlib
from pathlib import Path, PurePosixPath
import stat
import tomllib

import al_store
import al_task_attribution as T

EXCLUDED = {".git", ".venv", "__pycache__", "node_modules"}


def _path(value):
    if (
        not isinstance(value, str)
        or not value
        or Path(value).is_absolute()
        or ".." in Path(value).parts
    ):
        raise ValueError("unsupported reader source root")
    return Path(value).as_posix()


def closure(profile, spec):
    inputs = profile["repository_inputs"]
    if profile["id"] == "isolated-json-tool-v1":
        return {"kind": "literal-input-v1", "files": inputs, "roots": [], "configuration": None}
    if profile["id"] != "bundled-module-ban-v1":
        raise ValueError("unknown reader closure")
    file = inputs[0]
    path = al_store.REPO_ROOT / file
    raw = path.read_bytes()
    config = tomllib.loads(raw.decode())
    tool = config.get("tool", {})
    settings = tool.get("setuptools", {})
    packages = settings.get("packages", {})
    roots = packages.get("find", {}).get("where") if isinstance(packages, dict) else None
    roots = roots or list(settings.get("package-dir", {}).values())
    unit = Path(file).parent
    selected = None
    if not roots:
        selected = (al_store.REPO_ROOT / unit / "src").is_dir()
        roots = [
            tool.get("uv", {})
            .get("build-backend", {})
            .get("module-root", "src" if selected else ".")
        ]
    if not isinstance(roots, list) or not roots:
        raise ValueError("literal architecture roots required")
    normalized = sorted(set((unit / _path(root)).as_posix() for root in roots))
    for root in normalized:
        target = al_store.REPO_ROOT / root
        if target.is_symlink() or not target.is_dir():
            raise ValueError("architecture source root unavailable")
    return {
        "kind": "module-ban-source-v1",
        "files": inputs,
        "roots": normalized,
        "configuration": {
            "path": file,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "default_src_present": selected,
        },
    }


def selected(profile, members):
    declaration = profile["source_closure"]
    paths = set(declaration["files"])
    if declaration["kind"] == "module-ban-source-v1":
        for path in members:
            parts = PurePosixPath(path).parts
            if (
                path.endswith(".py")
                and not EXCLUDED.intersection(parts)
                and any(
                    root == "." or path == root or path.startswith(root + "/")
                    for root in declaration["roots"]
                )
            ):
                paths.add(path)
    return sorted(paths)


def directories(paths):
    return sorted({str(parent) for path in paths for parent in PurePosixPath(path).parents})


def source_binding(st, profile):
    rows = {
        row["path"]: row for row in T.observe(st)["files"] if row["worktree"]["type"] != "deleted"
    }
    paths = selected(profile, rows)
    records = {}
    absent = []
    for path in paths:
        row = rows.get(path)
        if row is None or row["worktree"]["type"] != "file":
            absent.append(path)
            continue
        records[path] = {"worktree": row["worktree"], "physical_mode": row["physical_mode"]}
    directory_modes = {}
    for directory in directories(paths):
        path = al_store.REPO_ROOT / directory
        try:
            before = path.lstat()
        except FileNotFoundError:
            directory_modes[directory] = None
            continue
        if not stat.S_ISDIR(before.st_mode):
            raise ValueError("reader ancestor is not a real directory")
        directory_modes[directory] = stat.S_IMODE(before.st_mode)
    return {
        "kind": "scoped-reader-inputs-v1",
        "records": records,
        "membership": paths,
        "directories": directory_modes,
        "absent_paths": absent,
        "observes_git": False,
        "selector": profile["source_closure"],
    }


def materialized_binding(profile, work):
    paths = selected(profile, work)
    wanted = set(paths) | set(directories(paths))
    configuration = profile["source_closure"]["configuration"]
    if configuration:
        record = work.get(configuration["path"])
        if not record or record[0] != "file" or record[2] != configuration["sha256"]:
            raise ValueError(
                "materialized architecture configuration differs from prepared selector"
            )
    if any(path not in work or work[path][0] != "file" for path in paths):
        raise ValueError("materialized reader source missing or nonregular")
    return {path: work[path] for path in sorted(wanted) if path in work}
