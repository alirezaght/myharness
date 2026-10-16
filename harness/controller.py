"""Harness Controller: task lifecycle and persisted, validated phase transitions."""

import secrets
import sqlite3
from dataclasses import dataclass

from harness.classification import Complexity, classify_request
from harness.db import transaction, utcnow
from harness.phases import (
    PHASE_ORDER,
    InvalidTransition,
    Phase,
    is_backward,
    next_phase,
    validate_transition,
)

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


class TaskNotFound(LookupError):
    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(f"unknown task: {task_id}")


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
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> Task:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFound(task_id)
        return _task(row)

    def list_tasks(self) -> list[Task]:
        rows = self.conn.execute("SELECT * FROM tasks ORDER BY created_at, rowid").fetchall()
        return [_task(row) for row in rows]

    def advance(self, task_id: str) -> Task:
        with transaction(self.conn):
            current = self.get_task(task_id).phase
            target = next_phase(current)
            if target is None:
                raise InvalidTransition(current, current, f"{current} is the last phase")
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
        return self.get_task(task_id)

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

    def _record(
        self, task_id: str, from_phase: Phase | None, to: Phase, reason: str | None, now: str
    ) -> None:
        self.conn.execute(
            "INSERT INTO phase_transitions (task_id, from_phase, to_phase, reason, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (task_id, from_phase, to, reason, now),
        )
