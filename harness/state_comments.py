"""Comments in the task workspace (P2-05): tied to an element id and a diagram version.

Rows are append-only except their status and updated_at; the Comment Service owns the rules.
"""

import sqlite3
from dataclasses import dataclass
from enum import StrEnum

from harness.db import utcnow
from harness.state_proposals import required


class CommentKind(StrEnum):
    CLARIFICATION = "clarification"
    CORRECTION = "correction"
    REDESIGN = "redesign"


class CommentStatus(StrEnum):
    OPEN = "open"
    ADDRESSED = "addressed"
    RESOLVED = "resolved"


@dataclass(frozen=True)
class Comment:
    id: int
    task_id: str
    diagram_version: int
    element_id: str
    kind: CommentKind
    text: str
    status: CommentStatus
    request_id: str
    created_at: str
    updated_at: str


def comment_from_row(row: sqlite3.Row) -> Comment:
    return Comment(
        id=row["id"],
        task_id=row["task_id"],
        diagram_version=row["diagram_version"],
        element_id=row["element_id"],
        kind=CommentKind(row["kind"]),
        text=row["text"],
        status=CommentStatus(row["status"]),
        request_id=row["request_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class CommentsMixin:
    """Mixed into TaskWorkspace, which provides conn, task_id, _one and _all."""

    conn: sqlite3.Connection
    task_id: str

    def insert_comment(
        self, diagram_version: int, element_id: str, kind: CommentKind, text: str, request_id: str
    ) -> Comment:
        values = (
            int(diagram_version),
            required(element_id, "element id"),
            CommentKind(kind),
            required(text, "comment text"),
            CommentStatus.OPEN,
            required(request_id, "request id"),
        )
        now = utcnow()
        cursor = self.conn.execute(
            "INSERT INTO comments (task_id, diagram_version, element_id, kind, text, status,"
            " request_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.task_id, *values, now, now),
        )
        return self.comment(cursor.lastrowid)

    def comment(self, comment_id: int) -> Comment:
        return comment_from_row(self._one("comments", "comment", comment_id))

    def comments(self, status: CommentStatus | None = None) -> list[Comment]:
        status = None if status is None else CommentStatus(status)
        return [comment_from_row(row) for row in self._all("comments", status)]

    def comment_by_request(self, request_id: str) -> Comment | None:
        row = self.conn.execute(
            "SELECT * FROM comments WHERE task_id = ? AND request_id = ?",
            (self.task_id, request_id),
        ).fetchone()
        return None if row is None else comment_from_row(row)

    def set_comment_status(
        self, comment_id: int, expected: CommentStatus, target: CommentStatus
    ) -> Comment:
        """Move a comment that is still in the expected status; nothing else changes."""
        cursor = self.conn.execute(
            "UPDATE comments SET status = ?, updated_at = ? WHERE id = ? AND task_id = ?"
            " AND status = ?",
            (CommentStatus(target), utcnow(), comment_id, self.task_id, CommentStatus(expected)),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"comment {comment_id} is not {expected}")
        return self.comment(comment_id)
