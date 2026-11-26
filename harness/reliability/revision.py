"""A cheap marker that changes whenever an execution's reliability read models may change.

The dashboard's reliability panels (operation incidents, incident intelligence, timeline and
runtime policy) fetch their scoped lists again only when this marker, their own query, or an
explicit refresh changes, instead of on every live repaint.

The marker is derived from the schema rather than a hand-kept list, so a table added to the
reliability, incident or policy families is covered without editing this module. For each
table it records the row count and newest rowid (inserts and deletes) and, for columns that
are updated in place, their non-null count and maximum. Tables with an ``execution_id``
column are scoped to the execution; the rest (revision and membership tables keyed by an
incident, group or policy) are counted globally, which can only cause an extra fetch.
"""

import hashlib
import sqlite3

FAMILIES = ("reliability_", "incident_", "policy_", "contract_rejection_receipts")
MUTABLE = frozenset(
    {"finished_at", "updated_at", "settled_at", "revision", "status", "state", "outcome"}
)


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables(conn: sqlite3.Connection) -> list[tuple[str, bool, tuple[str, ...]]]:
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    tables = []
    for name, sql in rows:
        if not name.startswith(FAMILIES):
            continue
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({_quoted(name)})")]
        has_rowid = "WITHOUT ROWID" not in (sql or "").upper()
        mutable = tuple(column for column in columns if column in MUTABLE)
        tables.append((name, has_rowid, ("execution_id" in columns, *mutable)))
    return tables


def reliability_revision(conn: sqlite3.Connection, execution_id: str) -> str:
    """An opaque digest of the execution's reliability tables; equal values mean no change."""
    digest = hashlib.sha256()
    for name, has_rowid, (scoped, *mutable) in _tables(conn):
        aggregates = ["count(*)"]
        if has_rowid:
            aggregates.append("max(rowid)")
        for column in mutable:
            quoted = _quoted(column)
            aggregates.append(f"count({quoted})")
            aggregates.append(f"max({quoted})")
            aggregates.append(f"total(length({quoted}))")
        query = f"SELECT {', '.join(aggregates)} FROM {_quoted(name)}"
        params: tuple[str, ...] = ()
        if scoped:
            query += " WHERE execution_id=?"
            params = (execution_id,)
        values = conn.execute(query, params).fetchone()
        digest.update(repr((name, tuple(values))).encode())
    return digest.hexdigest()[:24]
