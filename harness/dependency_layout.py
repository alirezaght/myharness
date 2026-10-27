"""Scoped reconciliation of ignored dependency hardlinks without changing bytes."""

import hashlib
import os
import secrets
import shutil
import stat
import subprocess
from pathlib import Path

from harness.execution_contract import digest

LIMIT = 200000
SKIP = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}


def _git(root, *args, input=None):
    result = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        cwd=root,
        input=input,
        capture_output=True,
        env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"),
    )
    if result.returncode not in {0, 1}:
        raise ValueError("dependency layout Git observation unavailable")
    return result


def _source(root):
    return {
        "head": _git(root, "rev-parse", "HEAD").stdout.decode().strip(),
        "worktree": digest(
            _git(root, "diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD").stdout
        ),
        "status": digest(_git(root, "status", "--porcelain", "-z", "--untracked-files=all").stdout),
    }


def _ordinary(root, name):
    path = root / name
    if Path(name).is_absolute() or ".." in Path(name).parts or root not in path.resolve().parents:
        raise ValueError("dependency scope escapes the repository")
    if any(p.is_symlink() for p in [path, *path.parents] if p != root and root in p.parents):
        raise ValueError("dependency reconciliation cannot traverse symlinks")
    return path


def inspect(root, scopes=None):
    root = Path(root).resolve()
    before = _source(root)
    if scopes is None:
        scopes = []
        for directory, dirs, _ in os.walk(root, followlinks=False):
            for name in list(dirs):
                if name in {"node_modules", ".venv"}:
                    scopes.append((Path(directory) / name).relative_to(root).as_posix())
            dirs[:] = [n for n in dirs if n not in SKIP and not (Path(directory) / n).is_symlink()]
    rows = []
    visited = 0
    for name in sorted(set(scopes)):
        path = _ordinary(root, name)
        if not path.exists():
            continue
        members = (
            [path]
            if path.is_file()
            else (Path(d) / n for d, dirs, files in os.walk(path, followlinks=False) for n in files)
        )
        for member in members:
            visited += 1
            if visited > LIMIT:
                raise ValueError("dependency layout scan limit; provide narrower explicit scopes")
            if member.is_symlink():
                continue
            info = member.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink <= 1:
                continue
            relative = member.relative_to(root).as_posix()
            _ordinary(root, relative)
            if _git(root, "ls-files", "--error-unmatch", "--", relative).returncode == 0:
                raise ValueError(
                    "tracked hardlink requires source-owner reconciliation: " + relative
                )
            if _git(root, "check-ignore", "-q", "--", relative).returncode != 0:
                raise ValueError("only ignored dependency files may be reconciled: " + relative)
            if info.st_size > 64 * 1024 * 1024:
                raise ValueError("dependency file exceeds reconciliation bound: " + relative)
            raw = member.read_bytes()
            after = member.lstat()
            if (info.st_ino, info.st_mtime_ns, info.st_size, info.st_nlink) != (
                after.st_ino,
                after.st_mtime_ns,
                after.st_size,
                after.st_nlink,
            ):
                raise ValueError("dependency layout changed during observation")
            rows.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "mode": stat.S_IMODE(info.st_mode),
                    "device": info.st_dev,
                    "inode": info.st_ino,
                    "links": info.st_nlink,
                    "size": info.st_size,
                }
            )
    if _source(root) != before:
        raise ValueError("repository source changed during dependency observation")
    return {
        "schema_version": 1,
        "kind": "dependency-reconciliation-plan-v1",
        "repository": str(root),
        "source": before,
        "scopes": sorted(set(scopes)),
        "hardlinks": sorted(rows, key=lambda r: r["path"]),
        "ready": not rows,
        "visited": visited,
    }


def reconcile(plan, expected_sha256):
    if digest(plan) != expected_sha256:
        raise ValueError("dependency plan approval hash differs")
    current = inspect(plan["repository"], plan["scopes"])
    if current != plan:
        raise ValueError("dependency plan is stale; inspect and approve the current layout")
    root = Path(plan["repository"])
    backups = []
    completed = []
    try:
        for row in plan["hardlinks"]:
            path = _ordinary(root, row["path"])
            backup = path.with_name(".harness-original-" + secrets.token_hex(12))
            temporary = path.with_name(".harness-copy-" + secrets.token_hex(12))
            os.link(path, backup, follow_symlinks=False)
            backups.append((path, backup, temporary))
            with path.open("rb") as src, temporary.open("xb") as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fchmod(dst.fileno(), row["mode"])
                os.fsync(dst.fileno())
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != row["sha256"]:
                raise ValueError("dependency copy verification failed")
            os.replace(temporary, path)
            completed.append(row["path"])
        if _source(root) != plan["source"]:
            raise ValueError("dependency reconciliation changed source")
        for path, backup, _ in backups:
            if (
                path.stat().st_nlink != 1
                or hashlib.sha256(path.read_bytes()).hexdigest()
                != hashlib.sha256(backup.read_bytes()).hexdigest()
            ):
                raise ValueError(
                    "dependency reconciliation did not preserve a private identical file"
                )
    except BaseException:
        for path, backup, temporary in reversed(backups):
            temporary.unlink(missing_ok=True)
            if backup.exists():
                os.replace(backup, path)
        raise
    else:
        for _, backup, _ in backups:
            backup.unlink()
    return {
        "schema_version": 1,
        "kind": "dependency-reconciliation-receipt-v1",
        "plan_sha256": expected_sha256,
        "repository": str(root),
        "source": plan["source"],
        "files": plan["hardlinks"],
        "completed": completed,
        "guard_weakened": False,
        "source_unchanged": True,
    }
