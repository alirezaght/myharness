"""Independent complete repository/worktree and runtime-root enumeration."""

import os
from pathlib import Path
import stat

from .activity_inventory import repository_identity
from .registry import canonical, digest, records
from .validator_origin import safe

LIMITS = {"directories": 200000, "depth": 64, "repositories": 10000}
# Dependency, build and cache trees are recorded but never descended: they hold no AL runs.
PRUNED = frozenset({"node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache",
                    ".ruff_cache", ".gradle", ".next", ".turbo", ".cache", ".pnpm-store", "site-packages",
                    ".terraform", ".uv-cache"})


def _symlink(entry, roots):
    """An unfollowed symlink leaf; one to a repository outside the scan roots is refused."""
    try:
        target = Path(entry.path).resolve(strict=True)
    except (OSError, RuntimeError):
        return "symlink"
    if target.is_dir() and (target / ".git").exists() and not any(target.is_relative_to(root) for root in roots):
        raise ValueError("workspace repository enumeration symlink boundary: repository outside scan roots")
    return "symlink"


def enumerate_repositories(roots):
    repositories, observations = {}, []
    count = 0
    resolved_roots = [Path(r).absolute().resolve() for r in roots]
    for requested in roots:
        root = safe(Path(requested).absolute())
        if not root.exists():
            observations.append({"root": str(root), "exists": False, "directories": []})
            continue
        if not root.is_dir():
            raise ValueError("authorized repository scan root is not a directory")
        pending, directories = [(root, 0)], []
        while pending:
            directory, depth = pending.pop()
            count += 1
            if count > LIMITS["directories"] or depth > LIMITS["depth"]:
                raise ValueError("workspace repository enumeration incomplete: limit")
            children = []
            with os.scandir(directory) as entries:
                for entry in sorted(entries, key=lambda item: item.name):
                    mode = entry.stat(follow_symlinks=False).st_mode
                    if stat.S_ISLNK(mode):
                        kind = _symlink(entry, resolved_roots)
                    elif stat.S_ISDIR(mode) and entry.name in PRUNED:
                        kind = "pruned-directory"
                    elif stat.S_ISDIR(mode):
                        kind = "directory"
                    elif stat.S_ISREG(mode):
                        kind = "file"
                    else:
                        raise ValueError("workspace repository enumeration special boundary")
                    children.append([entry.name, kind])
                    if kind == "directory" and entry.name != ".git":
                        pending.append((Path(entry.path), depth + 1))
            if any(name == ".git" for name, _ in children):
                identity = repository_identity(directory)
                if identity["root"] != str(directory.resolve()):
                    raise ValueError("workspace repository boundary identity differs")
                repositories[identity["root"]] = identity
                if len(repositories) > LIMITS["repositories"]:
                    raise ValueError("workspace repository count limit")
            directories.append({"path": str(directory), "children": children})
        observations.append(
            {
                "root": str(root),
                "exists": True,
                "directories": sorted(directories, key=lambda row: row["path"]),
            }
        )
    return {
        "repositories": [repositories[key] for key in sorted(repositories)],
        "observations": observations,
        "limits": dict(LIMITS),
        "pruned": sorted(PRUNED),
    }


def descriptor(value):
    if isinstance(value, str):
        path = safe(Path(value).expanduser().absolute())
        if path.name != "al-runtime" or path.parent.name not in {".codex", ".claude"}:
            raise ValueError("custom runtime root requires explicit repository/platform binding")
        value = {
            "runtime_root": str(path),
            "platform": path.parent.name[1:],
            "repository": repository_identity(path.parent.parent),
        }
    if (
        not isinstance(value, dict)
        or set(value) != {"runtime_root", "platform", "repository"}
        or value["platform"] not in {"codex", "claude"}
        or not isinstance(value["repository"], dict)
    ):
        raise ValueError("invalid additive workspace runtime root")
    path = safe(Path(value["runtime_root"]).absolute())
    identity = repository_identity(value["repository"]["root"])
    if value["repository"] != identity:
        raise ValueError("foreign workspace runtime repository identity")
    return {"runtime_root": str(path), "platform": value["platform"], "repository": identity}


def derive_scope(source, home, state_root, *, explicit=()):
    source = repository_identity(source)
    home = safe(Path(home).resolve())
    enumeration = enumerate_repositories([home / "personal", home / "Documents"])
    repositories = {row["root"]: row for row in enumeration["repositories"]}
    repositories[source["root"]] = source
    provided = [descriptor(row) for row in explicit]
    supplied = list(provided)
    by_path = {row["runtime_root"]: row for row in supplied}
    if len(by_path) != len(supplied):
        raise ValueError("duplicate explicit workspace runtime root")
    registered = []
    for row in records(state_root):
        target = row["target"]
        if target["scope"] == "repo":
            identity = repository_identity(target["root"])
            if identity["root"] != target["root"]:
                raise ValueError("registered workspace root moved")
            repositories[identity["root"]] = identity
            registered.append({"repository": identity})
        for root in row.get("runtime_roots", []):
            item = by_path.get(str(Path(root).absolute())) or descriptor(root)
            supplied.append(item)
            registered.append(item)
    ambient = os.environ.get("AL_RUNTIME_DIR")
    if ambient:
        item = by_path.get(str(Path(ambient).absolute())) or descriptor(ambient)
        supplied.append(item)
    for item in supplied:
        repositories[item["repository"]["root"]] = item["repository"]
    selected = {}

    def add(item):
        previous = selected.get(item["runtime_root"])
        if previous is not None and previous != item:
            raise ValueError("conflicting workspace runtime root identity")
        selected[item["runtime_root"]] = item

    for identity in repositories.values():
        for platform in ("codex", "claude"):
            add(
                {
                    "repository": identity,
                    "platform": platform,
                    "runtime_root": str(Path(identity["root"]) / ("." + platform) / "al-runtime"),
                }
            )
    for item in supplied:
        add(item)
    global_roots = []
    for platform in ("codex", "claude"):
        path = safe(home / ("." + platform) / "al-runtime")
        if path.exists():
            raise ValueError(
                "unsupported non-repository global runtime requires positive classification"
            )
        global_roots.append({"path": str(path), "exists": False})
    value = {
        "schema_version": 1,
        "kind": "complete-workspace-runtime-scope-v1",
        "home": str(home),
        "source_repository": source,
        "authorized_scan_roots": [str(home / "personal"), str(home / "Documents")],
        "enumeration": enumeration,
        "registered_contributors": sorted(registered, key=canonical),
        "explicit": provided,
        "ambient_runtime_root": ambient,
        "global_runtime_roots": global_roots,
        "roots": [selected[key] for key in sorted(selected)],
    }
    value["digest"] = digest(canonical(value))
    return value


def revalidate_scope(value, state_root):
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("kind") != "complete-workspace-runtime-scope-v1"
        or value.get("digest")
        != digest(canonical({k: v for k, v in value.items() if k != "digest"}))
    ):
        raise ValueError("invalid workspace runtime scope")
    current = derive_scope(
        value["source_repository"]["root"], value["home"], state_root, explicit=value["explicit"]
    )
    if value != current:
        raise ValueError("workspace runtime-root enumeration changed or incomplete")
    return current
