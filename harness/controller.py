"""Harness Controller: task lifecycle and persisted, validated phase transitions."""

import secrets
import sqlite3
from dataclasses import dataclass

from harness.classification import Complexity, classify_request
from harness.db import transaction, utcnow
from harness.errors import TaskNotFound
from harness.investigation import InvestigationReport
from harness.phases import (
    PHASE_ORDER,
    InvalidTransition,
    Phase,
    is_backward,
    next_phase,
    validate_transition,
)
from harness.state import TaskWorkspace
from harness.state_models import Checkpoint, QuestionSource

__all__ = ["HarnessController", "PhaseTransition", "Task", "TaskNotFound"]

SOURCE_DEVELOPER = "developer"
SOURCE_PROPOSED = "proposed"


@dataclass(frozen=True)
class Task:
    id: str
    request: str
    complexity: Complexity
    complexity_source: str
    phase: Phase
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PhaseTransition:
    id: int
    task_id: str
    from_phase: Phase | None
    to_phase: Phase
    reason: str | None
    created_at: str


def _task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        request=row["request"],
        complexity=Complexity(row["complexity"]),
        complexity_source=row["complexity_source"],
        phase=Phase(row["phase"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class HarnessController:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_task(self, request: str, complexity: Complexity | None = None) -> Task:
        text = request.strip()
        if not text:
            raise ValueError("request must not be empty")
        if complexity is None:
            level, source = classify_request(text), SOURCE_PROPOSED
        else:
            level, source = Complexity(complexity), SOURCE_DEVELOPER
        task_id, now, first = f"t-{secrets.token_hex(4)}", utcnow(), PHASE_ORDER[0]
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO tasks (id, request, complexity, complexity_source, phase,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, text, level, source, first, now, now),
            )
            self._record(task_id, None, first, "task created", now)
            self.workspace(task_id).record_event(
                "task.created", {"complexity": level, "source": source}
            )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> Task:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFound(task_id)
        return _task(row)

    def workspace(self, task_id: str) -> TaskWorkspace:
        return TaskWorkspace(self.conn, task_id)

    def list_tasks(self) -> list[Task]:
        rows = self.conn.execute("SELECT * FROM tasks ORDER BY created_at, rowid").fetchall()
        return [_task(row) for row in rows]

    def advance(self, task_id: str) -> Task:
        with transaction(self.conn):
            current = self.get_task(task_id).phase
            target = next_phase(current)
            if target is None:
                raise InvalidTransition(current, current, f"{current} is the last phase")
            if target is Phase.APPROVAL:
                from harness.review import pass_gate  # lazy: review imports the engines

                pass_gate(self, task_id)
            self._move(task_id, current, target, None)
        return self.get_task(task_id)

    def go_back(self, task_id: str, to: Phase, reason: str) -> Task:
        target, why = Phase(to), (reason or "").strip()
        if not why:
            raise ValueError("going back to an earlier phase requires a reason")
        with transaction(self.conn):
            current = self.get_task(task_id).phase
            if not is_backward(current, target):
                raise InvalidTransition(
                    current, target, f"cannot go back from {current} to {target}"
                )
            self._move(task_id, current, target, why)
        return self.get_task(task_id)

    def set_complexity(self, task_id: str, complexity: Complexity) -> Task:
        level = Complexity(complexity)
        with transaction(self.conn):
            self.get_task(task_id)
            self.conn.execute(
                "UPDATE tasks SET complexity = ?, complexity_source = ?, updated_at = ?"
                " WHERE id = ?",
                (level, SOURCE_DEVELOPER, utcnow(), task_id),
            )
            self.workspace(task_id).record_event("complexity.set", {"complexity": level})
        return self.get_task(task_id)

    def record_investigation(self, task_id: str, report: InvestigationReport) -> Checkpoint:
        """Store a report atomically: new open questions, one checkpoint and one event."""
        with transaction(self.conn):
            workspace = self.workspace(task_id)
            known = {(q.topic, q.text) for q in workspace.questions()}
            new = [q for q in report.questions if (q.topic, q.text) not in known]
            for question in new:
                workspace.ask(question.topic, question.text, QuestionSource.INVESTIGATION)
            checkpoint = workspace.checkpoint("investigation", report.to_dict())
            counts = report.to_dict()["counts"]
            workspace.record_event(
                "investigation.completed",
                {
                    "repo": report.repo_root,
                    "revision": report.revision,
                    "facts": counts["facts"],
                    "assumptions": counts["assumptions"],
                    "questions": counts["questions"],
                    "new_questions": len(new),
                    "decisions": counts["decisions"],
                },
            )
            self.conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (utcnow(), task_id))
        return checkpoint

    def history(self, task_id: str) -> list[PhaseTransition]:
        self.get_task(task_id)
        rows = self.conn.execute(
            "SELECT * FROM phase_transitions WHERE task_id = ? ORDER BY id", (task_id,)
        ).fetchall()
        return [
            PhaseTransition(
                id=row["id"],
                task_id=row["task_id"],
                from_phase=Phase(row["from_phase"]) if row["from_phase"] else None,
                to_phase=Phase(row["to_phase"]),
                reason=row["reason"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def _move(self, task_id: str, current: Phase, target: Phase, reason: str | None) -> None:
        validate_transition(current, target)
        now = utcnow()
        self.conn.execute(
            "UPDATE tasks SET phase = ?, updated_at = ? WHERE id = ?", (target, now, task_id)
        )
        self._record(task_id, current, target, reason, now)
        self.workspace(task_id).record_event(
            "phase.changed", {"from": current, "to": target, "reason": reason}
        )

    def _record(
        self, task_id: str, from_phase: Phase | None, to: Phase, reason: str | None, now: str
    ) -> None:
        self.conn.execute(
            "INSERT INTO phase_transitions (task_id, from_phase, to_phase, reason, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (task_id, from_phase, to, reason, now),
        )
