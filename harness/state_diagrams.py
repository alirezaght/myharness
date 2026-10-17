"""Diagram versions in the task workspace (P2-04): append-only snapshots of the diagram model."""

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from harness.db import utcnow
from harness.errors import RecordNotFound
from harness.state_proposals import required


@dataclass(frozen=True)
class DiagramVersion:
    id: int
    task_id: str
    version: int
    model_json: str
    content_hash: str
    created_at: str

    @property
    def model(self) -> dict[str, Any]:
        return json.loads(self.model_json)


def diagram_version_from_row(row: sqlite3.Row) -> DiagramVersion:
    return DiagramVersion(
        id=row["id"],
        task_id=row["task_id"],
        version=row["version"],
        model_json=row["model"],
        content_hash=row["content_hash"],
        created_at=row["created_at"],
    )


class DiagramsMixin:
    """Mixed into TaskWorkspace, which provides conn and task_id. Rows are never changed."""

    conn: sqlite3.Connection
    task_id: str

    def record_diagram_version(self, model_json: str, content_hash: str) -> DiagramVersion:
        """The next version number, in the same single statement that inserts the row."""
        values = (required(model_json, "diagram model"), required(content_hash, "content hash"))
        cursor = self.conn.execute(
            "INSERT INTO diagram_versions (task_id, version, model, content_hash, created_at)"
            " SELECT ?, COALESCE(MAX(version), 0) + 1, ?, ?, ? FROM diagram_versions"
            " WHERE task_id = ?",
            (self.task_id, *values, utcnow(), self.task_id),
        )
        row = self.conn.execute(
            "SELECT * FROM diagram_versions WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return diagram_version_from_row(row)

    def diagram_versions(self) -> list[DiagramVersion]:
        rows = self.conn.execute(
            "SELECT * FROM diagram_versions WHERE task_id = ? ORDER BY version", (self.task_id,)
        ).fetchall()
        return [diagram_version_from_row(row) for row in rows]

    def diagram_version(self, version: int) -> DiagramVersion:
        row = self.conn.execute(
            "SELECT * FROM diagram_versions WHERE task_id = ? AND version = ?",
            (self.task_id, int(version)),
        ).fetchone()
        if row is None:
            raise RecordNotFound("diagram version", int(version), self.task_id)
        return diagram_version_from_row(row)

    def latest_diagram_version(self) -> DiagramVersion | None:
        row = self.conn.execute(
            "SELECT * FROM diagram_versions WHERE task_id = ? ORDER BY version DESC LIMIT 1",
            (self.task_id,),
        ).fetchone()
        return None if row is None else diagram_version_from_row(row)
