"""Local State Manager: a per-task workspace over the shared SQLite state DB (SPEC §7, §13).

Each write is a single statement so it joins an enclosing transaction() when one is open.
"""

import json
import sqlite3
from typing import Any

from harness.db import utcnow
from harness.errors import RecordNotFound, TaskNotFound
from harness.state_decisions import DecisionChangesMixin
from harness.state_design import DesignMixin
from harness.state_models import (
    Checkpoint,
    Event,
    Question,
    QuestionKind,
    QuestionSource,
    QuestionStatus,
    SkipSource,
    checkpoint_from_row,
    event_from_row,
    question_from_row,
)
from harness.state_proposals import ProposalsMixin, required
from harness.state_specs import SpecsMixin


def _payload(payload: dict[str, Any] | None) -> str:
    return json.dumps(payload or {}, sort_keys=True)


class TaskWorkspace(ProposalsMixin, DesignMixin, DecisionChangesMixin, SpecsMixin):
    def __init__(self, conn: sqlite3.Connection, task_id: str):
        if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
            raise TaskNotFound(task_id)
        self.conn = conn
        self.task_id = task_id

    def _one(self, table: str, kind: str, record_id: int) -> sqlite3.Row:
        row = self.conn.execute(
            f"SELECT * FROM {table} WHERE id = ? AND task_id = ?", (record_id, self.task_id)
        ).fetchone()
        if row is None:
            raise RecordNotFound(kind, record_id, self.task_id)
        return row

    def _all(self, table: str, status: str | None) -> list[sqlite3.Row]:
        sql, args = f"SELECT * FROM {table} WHERE task_id = ?", [self.task_id]
        if status is not None:
            sql, args = sql + " AND status = ?", [*args, status]
        return self.conn.execute(sql + " ORDER BY id", args).fetchall()

    # Questions

    def ask(
        self,
        topic: str,
        text: str,
        source: QuestionSource = QuestionSource.INTERVIEW,
        kind: QuestionKind = QuestionKind.QUESTION,
        catalogue_key: str | None = None,
    ) -> Question:
        topic, text = required(topic, "topic"), required(text, "question")
        origin, form = QuestionSource(source), QuestionKind(kind)
        now = utcnow()
        cursor = self.conn.execute(
            "INSERT INTO questions (task_id, topic, text, source, status, kind, catalogue_key,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.task_id, topic, text, origin, QuestionStatus.OPEN, form, catalogue_key, now, now),
        )
        return self.question(cursor.lastrowid)

    def answer(self, question_id: int, answer: str) -> Question:
        text = required(answer, "answer")
        return self._set_question(question_id, QuestionStatus.ANSWERED, text, None, None)

    def skip(
        self, question_id: int, reason: str, source: SkipSource = SkipSource.NOT_RELEVANT
    ) -> Question:
        why, origin = required(reason, "skip reason"), SkipSource(source)
        return self._set_question(question_id, QuestionStatus.SKIPPED, None, why, origin)

    def reopen(self, question_id: int) -> Question:
        return self._set_question(question_id, QuestionStatus.OPEN, None, None, None)

    def mark_decided(self, question_id: int) -> Question:
        """Resolve a question by a developer-approved proposal; no answer text is invented."""
        return self._set_question(question_id, QuestionStatus.ANSWERED, None, None, None)

    def mark_asked(self, question_id: int) -> Question:
        now = utcnow()
        cursor = self.conn.execute(
            "UPDATE questions SET asked_at = COALESCE(asked_at, ?), updated_at = ?"
            " WHERE id = ? AND task_id = ?",
            (now, now, question_id, self.task_id),
        )
        if cursor.rowcount == 0:
            raise RecordNotFound("question", question_id, self.task_id)
        return self.question(question_id)

    def question(self, question_id: int) -> Question:
        return question_from_row(self._one("questions", "question", question_id))

    def questions(self, status: QuestionStatus | None = None) -> list[Question]:
        status = None if status is None else QuestionStatus(status)
        return [question_from_row(row) for row in self._all("questions", status)]

    def open_questions(self) -> list[Question]:
        return self.questions(QuestionStatus.OPEN)

    def _set_question(
        self,
        question_id: int,
        status: QuestionStatus,
        answer: str | None,
        reason: str | None,
        source: SkipSource | None,
    ) -> Question:
        cursor = self.conn.execute(
            "UPDATE questions SET status = ?, answer = ?, skip_reason = ?, skip_source = ?,"
            " updated_at = ? WHERE id = ? AND task_id = ?",
            (status, answer, reason, source, utcnow(), question_id, self.task_id),
        )
        if cursor.rowcount == 0:
            raise RecordNotFound("question", question_id, self.task_id)
        return self.question(question_id)

    # Events

    def record_event(self, kind: str, payload: dict[str, Any] | None = None) -> Event:
        name = required(kind, "event kind")
        cursor = self.conn.execute(
            "INSERT INTO events (task_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
            (self.task_id, name, _payload(payload), utcnow()),
        )
        return event_from_row(self._one("events", "event", cursor.lastrowid))

    def events(self) -> list[Event]:
        return [event_from_row(row) for row in self._all("events", None)]

    # Checkpoints

    def checkpoint(self, label: str, payload: dict[str, Any] | None = None) -> Checkpoint:
        name = required(label, "checkpoint label")
        cursor = self.conn.execute(
            "INSERT INTO checkpoints (task_id, phase, label, payload, created_at)"
            " SELECT id, phase, ?, ?, ? FROM tasks WHERE id = ?",
            (name, _payload(payload), utcnow(), self.task_id),
        )
        if cursor.rowcount == 0:
            raise TaskNotFound(self.task_id)
        return checkpoint_from_row(self._one("checkpoints", "checkpoint", cursor.lastrowid))

    def latest_checkpoint(self, label: str | None = None) -> Checkpoint | None:
        sql, args = "SELECT * FROM checkpoints WHERE task_id = ?", [self.task_id]
        if label is not None:
            sql, args = sql + " AND label = ?", [*args, label]
        row = self.conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
        return None if row is None else checkpoint_from_row(row)

    def checkpoints(self) -> list[Checkpoint]:
        return [checkpoint_from_row(row) for row in self._all("checkpoints", None)]
