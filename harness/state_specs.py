"""Approved specification versions in the task workspace: path, revision and content hash."""

import sqlite3
from dataclasses import dataclass

from harness.db import utcnow
from harness.spec_record import SpecStatus
from harness.state_proposals import required


@dataclass(frozen=True)
class SpecVersion:
    id: int
    task_id: str
    version: int
    path: str
    revision: str
    working_tree: str
    content_hash: str
    status: SpecStatus
    superseded_by: int | None
    created_at: str


def spec_version_from_row(row: sqlite3.Row) -> SpecVersion:
    return SpecVersion(
        id=row["id"],
        task_id=row["task_id"],
        version=row["version"],
        path=row["path"],
        revision=row["revision"],
        working_tree=row["working_tree"],
        content_hash=row["content_hash"],
        status=SpecStatus(row["status"]),
        superseded_by=row["superseded_by"],
        created_at=row["created_at"],
    )


class SpecsMixin:
    """Mixed into TaskWorkspace, which provides conn, task_id and _all."""

    conn: sqlite3.Connection
    task_id: str

    def record_spec_version(
        self, version: int, path: str, revision: str, working_tree: str, content_hash: str
    ) -> SpecVersion:
        values = (
            required(str(path), "spec path"),
            required(revision, "revision"),
            required(working_tree, "working tree"),
            required(content_hash, "content hash"),
        )
        try:
            self.conn.execute(
                "INSERT INTO spec_versions (task_id, version, path, revision, working_tree,"
                " content_hash, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self.task_id, int(version), *values, SpecStatus.APPROVED, utcnow()),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError(
                f"spec v{version} of task {self.task_id} is already recorded ({error})"
            ) from error
        return self._spec_or_fail(version)

    def mark_spec_superseded(self, version: int, by: int) -> SpecVersion:
        cursor = self.conn.execute(
            "UPDATE spec_versions SET status = ?, superseded_by = ?"
            " WHERE task_id = ? AND version = ?",
            (SpecStatus.SUPERSEDED, int(by), self.task_id, int(version)),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"spec v{version} of task {self.task_id} is not recorded")
        return self._spec_or_fail(version)

    def spec_version(self, version: int) -> SpecVersion | None:
        row = self.conn.execute(
            "SELECT * FROM spec_versions WHERE task_id = ? AND version = ?",
            (self.task_id, int(version)),
        ).fetchone()
        return None if row is None else spec_version_from_row(row)

    def spec_versions(self) -> list[SpecVersion]:
        rows = self.conn.execute(
            "SELECT * FROM spec_versions WHERE task_id = ? ORDER BY version", (self.task_id,)
        ).fetchall()
        return [spec_version_from_row(row) for row in rows]

    def current_spec_version(self) -> SpecVersion | None:
        approved = [v for v in self.spec_versions() if v.status is SpecStatus.APPROVED]
        return approved[-1] if approved else None

    def _spec_or_fail(self, version: int) -> SpecVersion:
        found = self.spec_version(version)
        if found is None:
            raise ValueError(f"spec v{version} of task {self.task_id} is not recorded")
        return found
