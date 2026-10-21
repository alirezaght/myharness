"""Content-bound, bounded verification observations and opt-in measurements."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from al.installer.owned_artifacts import read_json as read_json, write_json as write_json

EXCLUDED = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    "al-runtime",
}
ROOTS = (
    "al",
    "harness",
    "tests",
    "scripts",
    ".claude/commands",
    ".github/workflows",
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "docs",
    "tasks",
    ".gitignore",
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def capture_source_binding(root):
    root = Path(root).resolve()
    files = {}
    total = 0

    def observe():
        members = []
        for name in ROOTS:
            target = root / name
            if target.is_symlink():
                raise ValueError("source symlink boundary: " + name)
            if target.is_file():
                members.append(target)
            elif target.is_dir():
                for directory, dirs, names in os.walk(target, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
                    for child in [
                        *(Path(directory) / d for d in dirs),
                        *(Path(directory) / n for n in names),
                    ]:
                        if child.is_symlink():
                            raise ValueError("source symlink boundary: " + str(child))
                    members.extend(
                        Path(directory) / n
                        for n in sorted(names)
                        if not n.endswith((".pyc", ".pyo"))
                    )
            if len(members) > 100000:
                raise ValueError("verification source membership limit")
        return sorted(members)

    members = observe()
    for path in members:
        raw = path.read_bytes()
        total += len(raw)
        if len(raw) > 32 * 1024 * 1024 or total > 256 * 1024 * 1024:
            raise ValueError("verification source byte limit")
        files[path.relative_to(root).as_posix()] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "mode": path.stat().st_mode & 0o777,
        }
    if observe() != members or any(
        hashlib.sha256((root / p).read_bytes()).hexdigest() != value["sha256"]
        or (root / p).stat().st_mode & 0o777 != value["mode"]
        for p, value in files.items()
    ):
        raise ValueError("verification source moved during observation")
    result = subprocess.run(
        ["git", "--no-optional-locks", "rev-parse", "--verify", "HEAD"],
        cwd=root,
        capture_output=True,
    )
    return {
        "root": str(root),
        "head": result.stdout.decode().strip() if result.returncode == 0 else None,
        "files": files,
        "content_digest": digest(files),
    }


class Profile:
    def __init__(self, enabled=False):
        self.enabled = enabled
        self.seconds = {}
        self.calls = {}

    @contextmanager
    def span(self, name):
        started = time.monotonic()
        try:
            yield
        finally:
            if self.enabled:
                self.seconds[name] = self.seconds.get(name, 0) + time.monotonic() - started
                self.calls[name] = self.calls.get(name, 0) + 1

    def report(self):
        return {"seconds": self.seconds, "calls": self.calls}
