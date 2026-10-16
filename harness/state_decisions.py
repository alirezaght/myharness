"""Decision changes in the task workspace: a proposed new version awaiting approval."""

import sqlite3
from dataclasses import dataclass
from enum import StrEnum

from harness.db import utcnow
from harness.decisions import DecisionRecord
from harness.state_proposals import required

NOT_STATED = "not stated"


class ChangeStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MODIFIED = "modified"


@dataclass(frozen=True)
class DecisionChange:
    id: int
    task_id: str
    decision_id: str
    base_version: int
    title: str
    content: str
    rationale: str
    scope: str
    impact: str
    status: ChangeStatus
    response: str | None
    result_path: str | None
    created_at: str
    resolved_at: str | None

    @property
    def ref(self) -> str:
        return f"C{self.id}"

    @property
    def text(self) -> str:
        """The decision text that would be recorded: the developer's change when modified."""
        if self.status is ChangeStatus.MODIFIED and self.response:
            return self.response
        return self.content


def decision_change_from_row(row: sqlite3.Row) -> DecisionChange:
    return DecisionChange(
        id=row["id"],
        task_id=row["task_id"],
        decision_id=row["decision_id"],
        base_version=row["base_version"],
        title=row["title"],
        content=row["content"],
        rationale=row["rationale"],
        scope=row["scope"],
        impact=row["impact"],
        status=ChangeStatus(row["status"]),
        response=row["response"],
        result_path=row["result_path"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
    )


def _or_not_stated(text: str) -> str:
    return text.strip() or "not stated"


@dataclass(frozen=True)
class ChangePreview:
    current: DecisionRecord
    change: DecisionChange
    task_references: tuple[int, ...]
    record_references: tuple[DecisionRecord, ...]

    def render(self) -> str:
        cur, change = self.current, self.change
        refs = [f"Q{qid} (skipped by {cur.id})" for qid in self.task_references]
        refs += [f"{r.id} v{r.version} — {r.title}" for r in self.record_references]
        task = change.task_id
        return "\n".join(
            [
                f"{cur.id} v{cur.version} — {cur.title} ({cur.status} {cur.timestamp})",
                f"original decision: {cur.decision}",
                f"original rationale: {_or_not_stated(cur.rationale)}",
                f"original impact: {_or_not_stated(cur.impact)}",
                f"referenced by: {', '.join(refs) if refs else 'nothing recorded'}",
                f"proposed v{cur.version + 1}: {change.content}",
                f"proposed rationale: {change.rationale}",
                f"consequences of the change: {change.impact}",
                f"{change.ref} pending — approve: harness decision decide {task} {change.id}"
                " accept|reject|modify",
            ]
        )


class DecisionChangesMixin:
    """Mixed into TaskWorkspace, which provides conn, task_id, _one and _all."""

    conn: sqlite3.Connection
    task_id: str

    def propose_change(
        self,
        decision_id: str,
        base_version: int,
        title: str,
        content: str,
        rationale: str,
        scope: str | None = None,
        impact: str | None = None,
    ) -> DecisionChange:
        ident = required(decision_id, "decision id")
        values = (
            required(title, "title"),
            required(content, "decision"),
            required(rationale, "rationale"),
            (scope or "").strip() or NOT_STATED,
            (impact or "").strip() or NOT_STATED,
        )
        cursor = self.conn.execute(
            "INSERT INTO decision_changes (task_id, decision_id, base_version, title, content,"
            " rationale, scope, impact, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.task_id,
                ident,
                int(base_version),
                *values,
                ChangeStatus.PENDING,
                utcnow(),
            ),
        )
        return self.decision_change(cursor.lastrowid)

    def resolve_change(
        self, change_id: int, status: ChangeStatus, response: str | None = None
    ) -> DecisionChange:
        outcome = ChangeStatus(status)
        if outcome is ChangeStatus.PENDING:
            raise ValueError("a decision change must be resolved as accepted, rejected or modified")
        if outcome is ChangeStatus.MODIFIED:
            text = required(response, "modified decision change response")
        else:
            text = (response or "").strip() or None
        cursor = self.conn.execute(
            "UPDATE decision_changes SET status = ?, response = ?, resolved_at = ?"
            " WHERE id = ? AND task_id = ? AND status = ?",
            (outcome, text, utcnow(), change_id, self.task_id, ChangeStatus.PENDING),
        )
        if cursor.rowcount == 0:
            current = self.decision_change(change_id)
            raise ValueError(f"decision change {current.ref} is already {current.status}")
        return self.decision_change(change_id)

    def set_change_result(self, change_id: int, path: str) -> DecisionChange:
        self.conn.execute(
            "UPDATE decision_changes SET result_path = ? WHERE id = ? AND task_id = ?",
            (str(path), change_id, self.task_id),
        )
        return self.decision_change(change_id)

    def decision_change(self, change_id: int) -> DecisionChange:
        return decision_change_from_row(self._one("decision_changes", "decision change", change_id))

    def decision_changes(self, status: ChangeStatus | None = None) -> list[DecisionChange]:
        status = None if status is None else ChangeStatus(status)
        return [decision_change_from_row(row) for row in self._all("decision_changes", status)]
