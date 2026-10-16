"""A small throwaway target repository and a byte/mtime snapshot of everything under it."""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

FAKE_SECRET = "sekrit-do-not-leak"

APPROVED = """# DEC-0001 — Storage engine

- **ID:** DEC-0001
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-01-01
- **Scope:** Persistence
- **Supersedes:** none

## Decision

Store exports as files.

## Rationale

Simple.

## Impact

None.
"""

SUPERSEDED = APPROVED.replace("DEC-0001", "DEC-0002").replace("approved", "superseded")

FILES = {
    "pyproject.toml": """[project]
name = "demo"
version = "0.1.0"
dependencies = ["requests"]

[dependency-groups]
dev = ["pytest"]

[tool.ruff]
line-length = 88

[tool.pytest.ini_options]
testpaths = ["tests"]
""",
    "demo/__init__.py": "",
    "demo/export.py": "def export_rows(rows):  # csv\n    return [','.join(r) for r in rows]\n",
    "demo/api/routes.py": "ROUTES = ['/items']\n",
    "tests/test_export.py": "def test_rows():\n    assert True\n",
    "migrations/0001_init.sql": "CREATE TABLE items (id INTEGER PRIMARY KEY);\n",
    "config/settings.yaml": "debug: false\n",
    ".env": f"TOKEN={FAKE_SECRET}\n",
    ".github/workflows/ci.yml": "on: push\n",
    "docs/decisions/DEC-0001-storage.md": APPROVED,
    "docs/decisions/DEC-0002-old.md": SUPERSEDED,
    "README.md": "# demo\n",
}


def write_files(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def git_init(root: Path) -> bool:
    if shutil.which("git") is None:
        return False
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run([*git, *args], cwd=root, check=True, capture_output=True)
    return True


def make_sample_repo(root: Path, with_git: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    write_files(root, FILES)
    if with_git:
        git_init(root)
    return root


def make_index_stale(root: Path) -> None:
    """Bump a tracked file's mtime so any index-refreshing git command would rewrite .git/index."""
    path = root / "demo" / "export.py"
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))


def tree_snapshot(root: Path) -> tuple[dict[str, tuple[int, int, str]], dict[str, int]]:
    """Every file (including .git/) as (size, mtime_ns, sha256); every directory's mtime_ns."""
    files, dirs = {}, {}
    for current, _dirs, names in os.walk(root, followlinks=False):
        base = Path(current)
        dirs[base.relative_to(root).as_posix()] = base.lstat().st_mtime_ns
        for name in names:
            path = base / name
            stat = path.lstat()
            digest = "" if path.is_symlink() else hashlib.sha256(path.read_bytes()).hexdigest()
            files[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns, digest)
    return files, dirs
