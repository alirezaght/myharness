"""Proposal methods of the task workspace: a recommendation the developer resolves once."""

import sqlite3

from harness.db import utcnow
from harness.state_models import Proposal, ProposalStatus, proposal_from_row


def required(value: str | None, what: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{what} must not be empty")
    return text


class ProposalsMixin:
    """Mixed into TaskWorkspace, which provides conn, task_id, _one, _all and question()."""

    conn: sqlite3.Connection
    task_id: str

    def propose(
        self, title: str, recommendation: str, rationale: str, question_id: int | None = None
    ) -> Proposal:
        title = required(title, "title")
        recommendation = required(recommendation, "recommendation")
        rationale = required(rationale, "rationale")
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
            text = required(response, "modified proposal response")
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
