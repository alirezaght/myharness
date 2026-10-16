"""Local State Manager: a per-task workspace over the shared SQLite state DB (SPEC §7, §13).

Each write is a single statement so it joins an enclosing transaction() when one is open.
"""

import json
import sqlite3
from typing import Any

from harness.db import utcnow
from harness.errors import RecordNotFound, TaskNotFound
from harness.state_models import (
    Checkpoint,
    Event,
    Proposal,
    ProposalStatus,
    Question,
    QuestionSource,
    QuestionStatus,
    checkpoint_from_row,
    event_from_row,
    proposal_from_row,
    question_from_row,
)


def _required(value: str | None, what: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{what} must not be empty")
    return text


def _payload(payload: dict[str, Any] | None) -> str:
    return json.dumps(payload or {}, sort_keys=True)


class TaskWorkspace:
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
        self, topic: str, text: str, source: QuestionSource = QuestionSource.INTERVIEW
    ) -> Question:
        topic, text, origin = (
            _required(topic, "topic"),
            _required(text, "question"),
            QuestionSource(source),
        )
        now = utcnow()
        cursor = self.conn.execute(
            "INSERT INTO questions (task_id, topic, text, source, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.task_id, topic, text, origin, QuestionStatus.OPEN, now, now),
        )
        return self.question(cursor.lastrowid)

    def answer(self, question_id: int, answer: str) -> Question:
        text = _required(answer, "answer")
        return self._set_question(question_id, QuestionStatus.ANSWERED, text, None)

    def skip(self, question_id: int, reason: str) -> Question:
        why = _required(reason, "skip reason")
        return self._set_question(question_id, QuestionStatus.SKIPPED, None, why)

    def reopen(self, question_id: int) -> Question:
        return self._set_question(question_id, QuestionStatus.OPEN, None, None)

    def question(self, question_id: int) -> Question:
        return question_from_row(self._one("questions", "question", question_id))

    def questions(self, status: QuestionStatus | None = None) -> list[Question]:
        status = None if status is None else QuestionStatus(status)
        return [question_from_row(row) for row in self._all("questions", status)]

    def open_questions(self) -> list[Question]:
        return self.questions(QuestionStatus.OPEN)

    def _set_question(
        self, question_id: int, status: QuestionStatus, answer: str | None, reason: str | None
    ) -> Question:
        cursor = self.conn.execute(
            "UPDATE questions SET status = ?, answer = ?, skip_reason = ?, updated_at = ?"
            " WHERE id = ? AND task_id = ?",
            (status, answer, reason, utcnow(), question_id, self.task_id),
        )
        if cursor.rowcount == 0:
            raise RecordNotFound("question", question_id, self.task_id)
        return self.question(question_id)

    # Proposals

    def propose(
        self, title: str, recommendation: str, rationale: str, question_id: int | None = None
    ) -> Proposal:
        title = _required(title, "title")
        recommendation = _required(recommendation, "recommendation")
        rationale = _required(rationale, "rationale")
        if question_id is not None:
            self.question(question_id)
        cursor = self.conn.execute(
            "INSERT INTO proposals (task_id, question_id, title, recommendation, rationale,"
            " status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                self.task_id,
                question_id,
                title,
                recommendation,
                rationale,
                ProposalStatus.PENDING,
                utcnow(),
            ),
        )
        return self.proposal(cursor.lastrowid)

    def resolve(
        self, proposal_id: int, status: ProposalStatus, response: str | None = None
    ) -> Proposal:
        outcome = ProposalStatus(status)
        if outcome is ProposalStatus.PENDING:
            raise ValueError("a proposal must be resolved as accepted, rejected or modified")
        if outcome is ProposalStatus.MODIFIED:
            text = _required(response, "modified proposal response")
        else:
            text = (response or "").strip() or None
        cursor = self.conn.execute(
            "UPDATE proposals SET status = ?, response = ?, resolved_at = ?"
            " WHERE id = ? AND task_id = ? AND status = ?",
            (outcome, text, utcnow(), proposal_id, self.task_id, ProposalStatus.PENDING),
        )
        if cursor.rowcount == 0:
            current = self.proposal(proposal_id)
            raise ValueError(f"proposal {proposal_id} is already {current.status}")
        return self.proposal(proposal_id)

    def proposal(self, proposal_id: int) -> Proposal:
        return proposal_from_row(self._one("proposals", "proposal", proposal_id))

    def proposals(self, status: ProposalStatus | None = None) -> list[Proposal]:
        status = None if status is None else ProposalStatus(status)
        return [proposal_from_row(row) for row in self._all("proposals", status)]

    # Events

    def record_event(self, kind: str, payload: dict[str, Any] | None = None) -> Event:
        name = _required(kind, "event kind")
        cursor = self.conn.execute(
            "INSERT INTO events (task_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
            (self.task_id, name, _payload(payload), utcnow()),
        )
        return event_from_row(self._one("events", "event", cursor.lastrowid))

    def events(self) -> list[Event]:
        return [event_from_row(row) for row in self._all("events", None)]

    # Checkpoints

    def checkpoint(self, label: str, payload: dict[str, Any] | None = None) -> Checkpoint:
        name = _required(label, "checkpoint label")
        cursor = self.conn.execute(
            "INSERT INTO checkpoints (task_id, phase, label, payload, created_at)"
            " SELECT id, phase, ?, ?, ? FROM tasks WHERE id = ?",
            (name, _payload(payload), utcnow(), self.task_id),
        )
        if cursor.rowcount == 0:
            raise TaskNotFound(self.task_id)
        return checkpoint_from_row(self._one("checkpoints", "checkpoint", cursor.lastrowid))

    def latest_checkpoint(self) -> Checkpoint | None:
        row = self.conn.execute(
            "SELECT * FROM checkpoints WHERE task_id = ? ORDER BY id DESC LIMIT 1",
            (self.task_id,),
        ).fetchone()
        return None if row is None else checkpoint_from_row(row)

    def checkpoints(self) -> list[Checkpoint]:
        return [checkpoint_from_row(row) for row in self._all("checkpoints", None)]
