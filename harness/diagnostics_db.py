"""SQLite diagnostics: capability enforcement beyond SQL text classification."""

import sqlite3
import time
from pathlib import Path

_ALLOWED_FUNCTIONS = frozenset(
    (
        "count sum min max avg total abs round length lower upper substr "
        "coalesce ifnull nullif typeof quote hex instr trim ltrim rtrim"
    ).split()
)


def query_sqlite(path, query, *, max_rows=100, max_bytes=65536, timeout=1):
    target = Path(path).resolve(strict=True)
    if not target.is_file() or not isinstance(query, str) or len(query) > 16384:
        raise ValueError("existing SQLite target and bounded query required")
    if not (1 <= max_rows <= 1000 and 1 <= max_bytes <= 1048576 and 0 < timeout <= 5):
        raise ValueError("diagnostic limits exceeded")
    conn = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=timeout)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, max_bytes)
        if hasattr(conn, "enable_load_extension"):
            conn.enable_load_extension(False)

        def authorize(action, one, two, database, source):
            if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE}:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and str(two).lower() in _ALLOWED_FUNCTIONS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        conn.set_authorizer(authorize)
        deadline = time.monotonic() + timeout
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        cursor = conn.execute(query)
        if cursor.description is None:
            raise ValueError("diagnostic query must return rows")
        rows, size = [], 0
        for row in cursor:
            size += sum(len(str(v).encode()) for v in row)
            if len(rows) >= max_rows or size > max_bytes:
                return {
                    "columns": [d[0] for d in cursor.description],
                    "rows": rows,
                    "truncated": True,
                }
            rows.append(list(row))
        return {"columns": [d[0] for d in cursor.description], "rows": rows, "truncated": False}
    except sqlite3.Error:
        raise ValueError("SQLite read-only diagnostic denied, unavailable or timed out") from None
    finally:
        conn.close()
