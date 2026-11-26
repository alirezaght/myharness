"""Logical snapshots that prove a read-only request sequence persisted nothing.

HTTP GET handlers open their own SQLite connections, so a counter such as
``controller.conn.total_changes`` cannot see their writes. These helpers read the
committed database through a fresh connection and compare its full logical dump.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path


def logical_contents(database: Path | str) -> list[str]:
    """Return the committed schema and rows of ``database`` as SQL statements."""
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        return list(conn.iterdump())


@contextmanager
def unchanged_database(database: Path | str) -> Iterator[None]:
    """Fail when the wrapped block commits any schema or row change to ``database``."""
    before = logical_contents(database)
    yield
    after = logical_contents(database)
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    assert after == before, (
        f"read-only sequence changed persisted contents: added={added[:5]} removed={removed[:5]}"
    )
