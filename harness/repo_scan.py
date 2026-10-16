"""Read-only access to a target repository: file listing, git state and bounded text reads.

Nothing here writes: git runs with GIT_OPTIONAL_LOCKS=0 so it never refreshes the index.
"""

import os
import subprocess
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from harness.db import git_env
from harness.investigation import Assumption, Fact, FactCategory, OpenQuestion

IGNORED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "target",
    }
)
SECRET_PATTERNS = ("*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*")
SAFE_ENV_FILES = frozenset({".env.example", ".env.sample"})
MAX_SCAN_BYTES = 256 * 1024
WALK_SOURCE = "directory walk"
GIT_SOURCE = "git ls-files"


@dataclass
class Findings:
    facts: list[Fact] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    questions: list[OpenQuestion] = field(default_factory=list)

    def fact(self, category: FactCategory, text: str, evidence: str) -> None:
        self.facts.append(Fact(category, text, evidence))

    def assume(self, text: str, basis: str) -> None:
        self.assumptions.append(Assumption(text, basis))

    def ask(self, topic: str, text: str) -> None:
        self.questions.append(OpenQuestion(topic, text))


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            env=git_env(),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _walk(root: Path) -> list[PurePosixPath]:
    found = []
    for current, dirs, names in os.walk(root, followlinks=False):
        base = Path(current)
        dirs[:] = sorted(
            name for name in dirs if name not in IGNORED_DIRS and not (base / name).is_symlink()
        )
        found.extend(PurePosixPath((base / name).relative_to(root).as_posix()) for name in names)
    return found


def list_files(root: Path) -> tuple[list[PurePosixPath], str]:
    """Relative, sorted file paths and the source that enumerated them."""
    output = None
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            env=git_env(),
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            output = result.stdout.decode("utf-8", errors="replace")
    except OSError:
        pass
    if output is None:
        return sorted(_walk(root)), WALK_SOURCE
    paths = {PurePosixPath(entry) for entry in output.split("\0") if entry}
    existing = [path for path in paths if (root / path).is_file()]
    return sorted(existing), GIT_SOURCE


def git_facts(root: Path) -> tuple[list[Fact], str | None]:
    repo = FactCategory.REPOSITORY
    if (_git(root, "rev-parse", "--is-inside-work-tree") or "").strip() != "true":
        return [Fact(repo, "not a git repository (directory walk used)", "git rev-parse")], None
    revision = (_git(root, "rev-parse", "HEAD") or "").strip() or None
    if revision is None:
        return [Fact(repo, "git repository without commits", "git rev-parse HEAD")], None
    facts = [Fact(repo, f"revision {revision}", "git rev-parse HEAD")]
    branch = (_git(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    if branch:
        facts.append(Fact(repo, f"branch {branch}", "git rev-parse --abbrev-ref HEAD"))
    status = _git(root, "status", "--porcelain")
    if status is not None:
        changed = len([line for line in status.splitlines() if line.strip()])
        text = "working tree clean" if changed == 0 else f"{changed} uncommitted paths"
        facts.append(Fact(repo, text, "git status --porcelain"))
    return facts, revision


def is_secret_path(rel: PurePosixPath) -> bool:
    name = rel.name
    if name == ".env" or (name.startswith(".env.") and name not in SAFE_ENV_FILES):
        return True
    return any(fnmatch(name, pattern) for pattern in SECRET_PATTERNS)


def read_text(root: Path, rel: PurePosixPath) -> str | None:
    """Bounded UTF-8 read inside root; None for secrets, binaries, oversized or outside files."""
    if is_secret_path(rel):
        return None
    path = root / rel
    try:
        if not path.resolve().is_relative_to(root) or path.stat().st_size > MAX_SCAN_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None
