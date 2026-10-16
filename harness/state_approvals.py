"""Approval records in the task workspace: append-only, each bound to one sealed spec version."""

import sqlite3
from dataclasses import dataclass

from harness.db import utcnow
from harness.state_proposals import required


@dataclass(frozen=True)
class ApprovalRecord:
    id: int
    task_id: str
    spec_version: int
    path: str
    revision: str
    content_hash: str
    review_state_hash: str
    created_at: str


def approval_from_row(row: sqlite3.Row) -> ApprovalRecord:
    return ApprovalRecord(
        id=row["id"],
        task_id=row["task_id"],
        spec_version=row["spec_version"],
        path=row["path"],
        revision=row["revision"],
        content_hash=row["content_hash"],
        review_state_hash=row["review_state_hash"],
        created_at=row["created_at"],
    )


class ApprovalsMixin:
    """Mixed into TaskWorkspace, which provides conn and task_id. Rows are never changed."""

    conn: sqlite3.Connection
    task_id: str

    def record_approval(
        self,
        spec_version: int,
        path: str,
        revision: str,
        content_hash: str,
        review_state_hash: str,
    ) -> ApprovalRecord:
        version = int(spec_version)
        values = (
            required(str(path), "spec path"),
            required(revision, "revision"),
            required(content_hash, "content hash"),
            required(review_state_hash, "review state hash"),
        )
        try:
            cursor = self.conn.execute(
                "INSERT INTO approvals (task_id, spec_version, path, revision, content_hash,"
                " review_state_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (self.task_id, version, *values, utcnow()),
            )
        except sqlite3.IntegrityError as error:
            problem = (
                "is already recorded"
                if "UNIQUE" in str(error)
                else "is not bound to a recorded spec version"
            )
            raise ValueError(
                f"approval of spec v{version} of task {self.task_id} {problem} ({error})"
            ) from error
        row = self.conn.execute(
            "SELECT * FROM approvals WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return approval_from_row(row)

    def approvals(self) -> list[ApprovalRecord]:
        rows = self.conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? ORDER BY id", (self.task_id,)
        ).fetchall()
        return [approval_from_row(row) for row in rows]

    def latest_approval(self) -> ApprovalRecord | None:
        row = self.conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? ORDER BY id DESC LIMIT 1", (self.task_id,)
        ).fetchone()
        return None if row is None else approval_from_row(row)
