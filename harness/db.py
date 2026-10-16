"""SQLite access: connection setup, default location and numbered .sql migrations (DEC-0003)."""

import hashlib
import os
import re
import sqlite3
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

MIGRATIONS_DIR = files("harness") / "migrations"
_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def git_env() -> dict[str, str]:
    """Environment for read-only git calls: never take optional locks or refresh the index."""
    return {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}


def repo_root(cwd: Path | None = None) -> Path:
    """Git top-level of cwd (default: the current directory), or cwd itself outside git."""
    cwd = Path.cwd() if cwd is None else Path(cwd)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            env=git_env(),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return cwd.resolve()
    top = result.stdout.strip()
    return Path(top).resolve() if result.returncode == 0 and top else cwd.resolve()


def default_db_path(root: Path | None = None) -> Path:
    """Per-repository state DB under $HARNESS_HOME, outside the target repo. Creates nothing."""
    root = (root or repo_root()).resolve()
    digest = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    return _projects_dir() / digest / "state.db"


def _projects_dir() -> Path:
    home = os.environ.get("HARNESS_HOME")
    base = Path(home) if home else Path.home() / ".local" / "share" / "harness"
    return base / "projects"


def _restrict(path: Path, created_dir: bool) -> None:
    """POSIX owner-only access; a pre-existing directory outside the harness home is left alone."""
    if os.name != "posix":
        return
    if created_dir or path.parent.resolve().is_relative_to(_projects_dir().resolve()):
        os.chmod(path.parent, 0o700)
    os.chmod(path, 0o600)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT, rolling back on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _migrations() -> list[tuple[int, str, str]]:
    found = []
    for entry in MIGRATIONS_DIR.iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort()
    versions = [version for version, _, _ in found]
    if len(versions) != len(set(versions)):
        raise RuntimeError(f"duplicate migration versions in {MIGRATIONS_DIR}")
    return found


def apply_migrations(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations, each atomically with its schema_migrations row."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    applied = []
    for version, name, sql in _migrations():
        conn.execute("BEGIN IMMEDIATE")
        try:
            done = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
            ).fetchone()
            if not done:
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, utcnow()),
                )
                applied.append(name)
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    return applied


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    created_dir = not path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, autocommit=True)
    try:
        _restrict(path, created_dir)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        apply_migrations(conn)
    except BaseException:
        conn.close()
        raise
    return conn
