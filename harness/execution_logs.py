"""Bounded execution-scoped diagnostics, separate from semantic progress."""

import re
import secrets

from harness.db import utcnow
from harness.execution_store import get_execution

MAX_TEXT = 16384
MAX_LOGS = 200


def redact(text):
    value = re.sub(
        r"(?i)(authorization\s*:\s*(?:bearer\s+)?|(?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s,;]+",
        r"\1[redacted]",
        str(text),
    )
    return value[:MAX_TEXT]


def append_log(conn, execution_id, channel, text):
    get_execution(conn, execution_id)
    if channel not in {"protocol", "worker", "host", "stderr", "authority"}:
        raise ValueError("invalid diagnostic channel")
    log_id = "log-" + secrets.token_hex(12)
    conn.execute(
        "INSERT INTO execution_logs VALUES (?,?,?,?,?)",
        (log_id, execution_id, channel, redact(text), utcnow()),
    )
    conn.execute(
        "DELETE FROM execution_logs WHERE execution_id=? AND rowid NOT IN "
        "(SELECT rowid FROM execution_logs WHERE execution_id=? ORDER BY rowid DESC LIMIT ?)",
        (execution_id, execution_id, MAX_LOGS),
    )
    return log_id


def read_log(conn, execution_id, log_id):
    get_execution(conn, execution_id)
    row = conn.execute(
        "SELECT id,channel,text,created_at FROM execution_logs WHERE execution_id=? AND id=?",
        (execution_id, log_id),
    ).fetchone()
    if row is None:
        raise LookupError("execution log not found")
    return dict(row)
