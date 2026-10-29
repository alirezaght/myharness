"""Bounded execution-scoped diagnostics, separate from semantic progress."""

import re
import secrets

from harness.db import utcnow
from harness.execution_store import get_execution

MAX_TEXT = 16384
MAX_LOGS = 200


def _redact(text, *, secrets=()):
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
    return value


def redact(text, *, secrets=()):
    return _redact(text, secrets=secrets)[:MAX_TEXT]


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
    """Buffer credential lines; explicitly suppress oversized lines through their terminator."""

    def __init__(self, secrets=()):
        self.secrets = tuple(s for s in secrets if s)
        self.pending = ""
        self.private_key = False
        self.discarding = False
        self.discard_tail = ""
        self.json_pending = ""
        self.json_suppressed = False

    def feed(self, text, *, final=False):
        result = []
        for piece in text.splitlines(keepends=True):
            ended = piece.endswith(("\n", "\r"))
            if self.discarding:
                # Detect a private-key opener even across the suppression boundary.
                scan = self.discard_tail + piece
                if re.search(r"-----BEGIN [^-\r\n]*PRIVATE KEY-----", scan):
                    self.private_key = True
                self.discard_tail = scan[-256:]
                if ended:
                    self.discarding = False
                    self.discard_tail = ""
                continue
            self.pending += piece
            if len(self.pending) > MAX_TEXT:
                if re.search(r"-----BEGIN [^-\r\n]*PRIVATE KEY-----", self.pending):
                    self.private_key = True
                self.discard_tail = self.pending[-256:]
                self.pending = ""
                self.discarding = not ended
                result.append("[oversized diagnostic line suppressed]\n")
                continue
            if ended:
                result.append(self._line(self.pending))
                self.pending = ""
        if final:
            if self.pending:
                result.append(self._line(self.pending))
            self.pending = ""
            self.discarding = False
            self.discard_tail = ""
        batch = self.json_pending + "".join(result)
        self.json_pending = ""
        if self.json_suppressed:
            if final:
                self.json_suppressed = False
            return ""
        # A sensitive JSON key and its quoted value may span physical lines/chunks.
        pattern = (
            r'(?i)"[^"\\]*(?:password|secret|token|api[_-]?key|private[_-]?key)[^"\\]*"\s*:\s*'
        )
        hold = None
        for match in re.finditer(pattern, batch):
            rest = batch[match.end() :]
            if not rest or (
                rest.startswith('"') and re.match(r'"(?:[^"\\]|\\.)*"', rest, re.S) is None
            ):
                hold = match.start()
                break
        if hold is not None and final:
            batch = batch[:hold] + "[incomplete JSON credential redacted]"
        elif hold is not None:
            self.json_pending = batch[hold:]
            batch = batch[:hold]
            if len(self.json_pending) > MAX_TEXT:
                self.json_pending = ""
                self.json_suppressed = True
                batch += "[oversized JSON credential suppressed]\n"
        return _redact(batch, secrets=self.secrets)

    def _line(self, line):
        if re.search(r"-----BEGIN [^-\r\n]*PRIVATE KEY-----", line):
            self.private_key = True
            marker = "[private key redacted]\n"
        else:
            marker = ""
        if self.private_key:
            if re.search(r"-----END [^-\r\n]*PRIVATE KEY-----", line):
                self.private_key = False
            return marker
        return line


def append_log(conn, execution_id, channel, text):
    execution = get_execution(conn, execution_id)
    if channel not in {"protocol", "worker", "host", "stderr", "authority"}:
        raise ValueError("invalid diagnostic channel")
    log_id = "log-" + secrets.token_hex(12)
    conn.execute(
        (
            "INSERT INTO execution_logs(id,execution_id,channel,text,created_at,epoch_id) "
            "VALUES (?,?,?,?,?,?)"
        ),
        (log_id, execution_id, channel, redact(text), utcnow(), execution["epoch_id"]),
    )
    conn.execute(
        (
            "DELETE FROM execution_logs WHERE execution_id=? AND epoch_id=? AND rowid NOT "
            "IN (SELECT rowid FROM execution_logs WHERE execution_id=? AND epoch_id=? "
            "ORDER BY rowid DESC LIMIT ?)"
        ),
        (execution_id, execution["epoch_id"], execution_id, execution["epoch_id"], MAX_LOGS),
    )
    return log_id


def read_log(conn, execution_id, log_id, *, epoch_id=None):
    from harness.execution_epochs import selected_epoch

    epoch_id = selected_epoch(conn, execution_id, epoch_id)
    row = conn.execute(
        (
            "SELECT id,channel,text,created_at FROM execution_logs WHERE execution_id=? "
            "AND id=? AND epoch_id=?"
        ),
        (execution_id, log_id, epoch_id),
    ).fetchone()
    if row is None:
        raise LookupError("execution log not found")
    return dict(row)
