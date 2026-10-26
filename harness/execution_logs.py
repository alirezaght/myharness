"""Bounded execution-scoped diagnostics, separate from semantic progress."""

import re
import secrets

from harness.db import utcnow
from harness.execution_store import get_execution

MAX_TEXT = 16384
MAX_LOGS = 200


def redact(text, *, secrets=()):
    value = str(text)
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        value = value.replace(secret, "[redacted]")
    value = re.sub(
        r"(?is)-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?(?:-----END [^-\r\n]*PRIVATE KEY-----|$)",
        "[private key redacted]",
        value,
    )
    value = re.sub(
        r'(?i)("(?:[^"\\]*)(?:password|secret|token|api[_-]?key|private[_-]?key)'
        r'(?:[^"\\]*)"\s*:\s*)"(?:[^"\\]|\\.)*"',
        r'\1"[redacted]"',
        value,
    )
    value = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@", value)
    value = re.sub(
        r"(?i)((?:authorization|private-token|x-api-key)\s*:\s*(?:bearer\s+)?|"
        r"(?:api[_-]?key|access_token|token|secret|password)\s*[=:]\s*)[^\s,;\"&]+",
        r"\1[redacted]",
        value,
    )
    return value[:MAX_TEXT]


def redact_value(value, *, secrets=()):
    if isinstance(value, dict):
        return {
            str(k): (
                "[redacted]"
                if re.search(
                    r"(?i)authorization|password|secret|token|api.?key|private.?key", str(k)
                )
                else redact_value(v, secrets=secrets)
            )
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(v, secrets=secrets) for v in value]
    return redact(value, secrets=secrets) if isinstance(value, str) else value


class StreamRedactor:
    """Retain a bounded overlap so split credentials never reach retained output."""

    def __init__(self, secrets=()):
        self.secrets = tuple(s for s in secrets if s)
        self.pending = ""
        self.private_key = False

    def feed(self, text, *, final=False):
        self.pending += text
        # Line buffering also handles credential headers split across chunks.
        if not final and "\n" not in self.pending:
            if len(self.pending) > MAX_TEXT:
                self.pending = "[oversized diagnostic redacted]"
            return ""
        cut = len(self.pending) if final else self.pending.rfind("\n") + 1
        lines = []
        for line in self.pending[:cut].splitlines(keepends=True):
            if re.search(r"-----BEGIN [^-\r\n]*PRIVATE KEY-----", line):
                self.private_key = True
                lines.append("[private key redacted]\n")
            if self.private_key:
                if re.search(r"-----END [^-\r\n]*PRIVATE KEY-----", line):
                    self.private_key = False
                continue
            lines.append(line)
        result = redact("".join(lines), secrets=self.secrets)
        self.pending = self.pending[cut:]
        return result


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
