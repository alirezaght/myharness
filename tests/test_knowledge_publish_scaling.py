"""Issue #2: snapshot publication holds the write lock, so its work must stay ~linear in size."""

import sqlite3

from harness.db import apply_migrations
from harness.knowledge.store import CARRY_FORWARD_ASSERTIONS

EVIDENCE_PER_SOURCE = 8


def _graph(sources: int) -> sqlite3.Connection:
    """A parent snapshot `old` and its successor `new` sharing every source but the last one."""
    conn = sqlite3.connect(":memory:", autocommit=True)
    apply_migrations(conn)  # foreign keys stay off: only the carried-forward statement is measured
    snapshot_sources = []
    for snapshot in ("old", "new"):
        for index in range(sources):
            version = f"v{index}" if snapshot == "old" or index < sources - 1 else f"v{index}-new"
            snapshot_sources.append((snapshot, f"s{index}", version, "present"))
    conn.executemany("INSERT INTO knowledge_snapshot_sources VALUES (?,?,?,?)", snapshot_sources)
    evidence, members = [], []
    for index in range(sources):
        for item in range(EVIDENCE_PER_SOURCE):
            assertion = f"a{index}-{item}"
            members.append(("old", assertion))
            evidence.append((assertion, f"v{index}", item, item + 1, "x"))
    # One assertion whose evidence spans two sources, one of which changed.
    members.append(("old", "cross"))
    evidence += [("cross", "v0", 0, 1, "x"), ("cross", f"v{sources - 1}", 0, 1, "x")]
    members.append(("old", "unsupported"))  # no evidence at all: never carried
    conn.executemany("INSERT INTO knowledge_evidence VALUES (?,?,?,?,?)", evidence)
    conn.executemany("INSERT INTO knowledge_snapshot_assertions VALUES (?,?)", members)
    return conn


def _carry(conn: sqlite3.Connection) -> int:
    steps = 0

    def count() -> int:
        nonlocal steps
        steps += 1
        return 0

    conn.set_progress_handler(count, 100)
    conn.execute(CARRY_FORWARD_ASSERTIONS, ("new", "old", "new"))
    conn.set_progress_handler(None, 0)
    return steps


def test_carry_forward_keeps_only_assertions_whose_evidence_is_all_present():
    conn = _graph(20)
    _carry(conn)
    carried = {
        row[0]
        for row in conn.execute(
            "SELECT assertion_id FROM knowledge_snapshot_assertions WHERE snapshot_id='new'"
        )
    }
    expected = {f"a{i}-{j}" for i in range(19) for j in range(EVIDENCE_PER_SOURCE)}
    assert carried == expected


def test_carry_forward_work_grows_linearly_with_repository_size():
    small, large = _carry(_graph(200)), _carry(_graph(1600))
    # 8x the sources: linear work is ~8x (plus log factors); the quadratic plan was ~64x.
    assert large < small * 16, (small, large)
