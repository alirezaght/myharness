"""Runtime proof of the stdlib SQLite capabilities the knowledge platform depends on.

Operational evidence, not mocks: every check runs against the interpreter's real ``sqlite3``
module and ``harness.db.connect``. ``test_wal_reset_advisory_fixed`` is deliberately strict
and fails on an affected runtime; provisioning a patched SQLite is an operator repair
(OPS-sqlite-wal-advisory), never a reason to skip, xfail or weaken the check.
"""

import platform
import sqlite3
import sys

import pytest

from harness.db import connect

# Official advisory: https://www.sqlite.org/wal.html#walreset (WAL-reset race).
FIXED_VERSION = (3, 51, 3)
# Identified fixed backports (exact sqlite_version_info tuples). Intentionally empty: no
# authoritative backport source was verifiable in this task. Add an entry only together with
# a source comment, e.g. ``(3, 50, 7),  # <URL of the official advisory/release note>``.
FIXED_BACKPORTS: frozenset[tuple[int, int, int]] = frozenset()


def _runtime_facts() -> str:
    return (
        f"sqlite3.sqlite_version={sqlite3.sqlite_version} "
        f"python={sys.version.split()[0]} platform={platform.platform()}"
    )


def _is_wal_reset_fixed(version_info: tuple[int, ...]) -> bool:
    version = tuple(version_info)[:3]
    return version >= FIXED_VERSION or version in FIXED_BACKPORTS


def test_report_runtime_facts(capsys, record_property):
    facts = _runtime_facts()
    record_property("sqlite_version", sqlite3.sqlite_version)
    record_property("python_version", sys.version.split()[0])
    record_property("platform", platform.platform())
    with capsys.disabled():
        print(f"\nSQLITE RUNTIME FACTS: {facts}")
    assert sqlite3.sqlite_version_info >= (3, 0, 0), facts


def test_fts5_is_usable(tmp_path):
    conn = sqlite3.connect(tmp_path / "fts.db")
    try:
        conn.execute("CREATE VIRTUAL TABLE docs USING fts5(title, body)")
        conn.executemany(
            "INSERT INTO docs (title, body) VALUES (?, ?)",
            [
                ("alpha", "knowledge platform retrieval"),
                ("beta", "unrelated payload"),
            ],
        )
        rows = conn.execute(
            "SELECT title FROM docs WHERE docs MATCH ? ORDER BY rank", ("retrieval",)
        ).fetchall()
        assert rows == [("alpha",)], _runtime_facts()
        miss = conn.execute("SELECT count(*) FROM docs WHERE docs MATCH 'nothingmatches'")
        assert miss.fetchone() == (0,)
    finally:
        conn.close()


def test_unknown_virtual_table_module_fails(tmp_path):
    """An unavailable module raises, so the FTS5 check above cannot pass vacuously."""
    conn = sqlite3.connect(tmp_path / "nomod.db")
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE VIRTUAL TABLE t USING no_such_module_xyz(a)")
    finally:
        conn.close()


def test_connect_activates_wal_journal_mode(tmp_path):
    db = tmp_path / "state" / "state.db"
    conn = connect(db)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal", _runtime_facts()
        conn.execute("CREATE TABLE wal_probe (v INTEGER)")
        conn.execute("INSERT INTO wal_probe VALUES (1)")
        assert (db.parent / "state.db-wal").exists(), "write did not go through the WAL file"
    finally:
        conn.close()


def test_connect_wal_is_persistent_across_connections(tmp_path):
    db = tmp_path / "state.db"
    connect(db).close()
    other = sqlite3.connect(db)
    try:
        assert other.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        other.close()


def test_wal_reset_advisory_fixed():
    """Fail visibly on a runtime affected by https://www.sqlite.org/wal.html#walreset.

    Requires SQLite >= 3.51.3 or an explicitly allowlisted, sourced fixed backport. This test
    must never be skipped, xfailed or relaxed: its result is the operational evidence.
    """
    assert _is_wal_reset_fixed(sqlite3.sqlite_version_info), (
        f"SQLite {sqlite3.sqlite_version} is affected by the WAL-reset advisory "
        f"(fixed in {'.'.join(map(str, FIXED_VERSION))}+; allowlisted backports: "
        f"{sorted(FIXED_BACKPORTS) or 'none'}). Operator repair: OPS-sqlite-wal-advisory. "
        f"{_runtime_facts()}"
    )
