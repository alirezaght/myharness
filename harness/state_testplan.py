"""Test-scenario methods of the task workspace: statements, recommendations, mocks, approvals."""

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from harness.db import utcnow
from harness.design_models import DECISIONS, EFFECTIVE, EntryOrigin, EntryStatus
from harness.state_proposals import required
from harness.testplan_models import (
    PlanApproval,
    Scenario,
    TestCategory,
    parse_covers,
    plan_approval_from_row,
    scenario_from_row,
)

UNSET: Any = object()
NO_COVERS = (
    "a recommendation must cover at least one requirement or design entry (--covers Q<n>|E<n>)"
)


def mock_pair(mock: str | None, justification: str | None) -> tuple[str | None, str | None]:
    """Both a mock target and its written justification, or neither."""
    target, why = (mock or "").strip() or None, (justification or "").strip() or None
    if (target is None) != (why is None):
        raise ValueError("a mock needs a written justification (--mock-reason)")
    return target, why


class TestPlanMixin:
    """Mixed into TaskWorkspace, which provides conn, task_id, _one and _all."""

    __test__ = False
    conn: sqlite3.Connection
    task_id: str

    def state_scenario(
        self,
        category: TestCategory,
        statement: str,
        covers: Iterable[str] = (),
        mock: str | None = None,
        mock_justification: str | None = None,
    ) -> Scenario:
        text = required(statement, "test scenario")
        pair = mock_pair(mock, mock_justification)
        return self._insert_scenario(
            category, EntryOrigin.DEVELOPER, text, None, covers, pair, EntryStatus.STATED
        )

    def propose_scenario(
        self,
        category: TestCategory,
        statement: str,
        rationale: str,
        covers: Iterable[str],
        mock: str | None = None,
        mock_justification: str | None = None,
    ) -> Scenario:
        text, why = required(statement, "recommendation"), required(rationale, "rationale")
        if not parse_covers(covers):
            raise ValueError(NO_COVERS)
        pair = mock_pair(mock, mock_justification)
        return self._insert_scenario(
            category, EntryOrigin.RECOMMENDATION, text, why, covers, pair, EntryStatus.PENDING
        )

    def resolve_scenario(
        self, scenario_id: int, status: EntryStatus, response: str | None = None
    ) -> Scenario:
        outcome = EntryStatus(status)
        if outcome not in DECISIONS:
            raise ValueError("a recommendation must be resolved as accepted, rejected or modified")
        if outcome is EntryStatus.MODIFIED:
            text = required(response, "modified recommendation response")
        else:
            text = (response or "").strip() or None
        now = utcnow()
        cursor = self.conn.execute(
            "UPDATE test_scenarios SET status = ?, response = ?, resolved_at = ?, updated_at = ?"
            " WHERE id = ? AND task_id = ? AND status = ?",
            (outcome, text, now, now, scenario_id, self.task_id, EntryStatus.PENDING),
        )
        if cursor.rowcount == 0:
            current = self.scenario(scenario_id)
            raise ValueError(f"scenario S{scenario_id} is already {current.status}")
        return self.scenario(scenario_id)

    def edit_scenario(
        self,
        scenario_id: int,
        statement: str | None = None,
        category: TestCategory | None = None,
        covers: Iterable[str] | None = None,
        mock: str | None = UNSET,
        mock_justification: str | None = UNSET,
        clear_mock: bool = False,
    ) -> Scenario:
        """Change an effective scenario; an edited recommendation becomes `modified`."""
        current = self._effective_scenario(scenario_id, "edit")
        text = current.text if statement is None else required(statement, "test scenario")
        kind = current.category if category is None else TestCategory(category)
        refs = current.covers if covers is None else parse_covers(covers)
        pair = self._edited_mock(current, mock, mock_justification, clear_mock)
        mock_changed = pair != (current.mock, current.mock_justification)
        if (text, kind, refs) == (current.text, current.category, current.covers) and not (
            mock_changed
        ):
            raise ValueError(f"the edit changes nothing in scenario S{scenario_id}")
        stated = current.status is EntryStatus.STATED
        status = EntryStatus.STATED if stated else EntryStatus.MODIFIED
        concern = None if mock_changed else current.concern
        self.conn.execute(
            "UPDATE test_scenarios SET statement = ?, response = ?, status = ?, category = ?,"
            " covers = ?, mock = ?, mock_justification = ?, concern = ?, updated_at = ?"
            " WHERE id = ? AND task_id = ?",
            (
                text if stated else current.statement,
                None if stated else text,
                status,
                kind,
                json.dumps(list(refs)),
                *pair,
                concern,
                utcnow(),
                scenario_id,
                self.task_id,
            ),
        )
        return self.scenario(scenario_id)

    def withdraw_scenario(self, scenario_id: int) -> Scenario:
        self._effective_scenario(scenario_id, "withdraw")
        return self._update(scenario_id, status=EntryStatus.WITHDRAWN, resolved_at=utcnow())

    def flag_scenario(self, scenario_id: int, concern: str) -> Scenario:
        """Mark a mock as hiding missing behaviour; a flagged scenario blocks plan approval."""
        why = required(concern, "flag reason")
        current = self._effective_scenario(scenario_id, "flag")
        if current.mock is None:
            raise ValueError(f"scenario S{scenario_id} has no mock to flag")
        return self._update(scenario_id, concern=why)

    def clear_scenario_flag(self, scenario_id: int) -> Scenario:
        if not self.scenario(scenario_id).flagged:
            raise ValueError(f"scenario S{scenario_id} is not flagged")
        return self._update(scenario_id, concern=None)

    def scenario(self, scenario_id: int) -> Scenario:
        return scenario_from_row(self._one("test_scenarios", "test scenario", scenario_id))

    def scenarios(self, status: EntryStatus | None = None) -> list[Scenario]:
        status = None if status is None else EntryStatus(status)
        return [scenario_from_row(row) for row in self._all("test_scenarios", status)]

    def record_plan_approval(self, content_hash: str, scenario_count: int) -> PlanApproval:
        digest = required(content_hash, "test plan content hash")
        cursor = self.conn.execute(
            "INSERT INTO test_plan_approvals (task_id, content_hash, scenario_count, created_at)"
            " VALUES (?, ?, ?, ?)",
            (self.task_id, digest, scenario_count, utcnow()),
        )
        row = self._one("test_plan_approvals", "test plan approval", cursor.lastrowid)
        return plan_approval_from_row(row)

    def plan_approvals(self) -> list[PlanApproval]:
        return [plan_approval_from_row(row) for row in self._all("test_plan_approvals", None)]

    def latest_plan_approval(self) -> PlanApproval | None:
        approvals = self.plan_approvals()
        return approvals[-1] if approvals else None

    # Internals

    def _effective_scenario(self, scenario_id: int, verb: str) -> Scenario:
        current = self.scenario(scenario_id)
        if current.status not in EFFECTIVE:
            raise ValueError(f"scenario S{scenario_id} is {current.status}; nothing to {verb}")
        return current

    @staticmethod
    def _edited_mock(
        current: Scenario, mock: Any, justification: Any, clear: bool
    ) -> tuple[str | None, str | None]:
        if clear:
            if mock is not UNSET or justification is not UNSET:
                raise ValueError("a mock cannot be set and removed in the same edit")
            return None, None
        target = current.mock if mock is UNSET else mock
        why = current.mock_justification if justification is UNSET else justification
        return mock_pair(target, why)

    def _update(self, scenario_id: int, **columns: Any) -> Scenario:
        assignments = ", ".join(f"{name} = ?" for name in columns)
        self.conn.execute(
            f"UPDATE test_scenarios SET {assignments}, updated_at = ? WHERE id = ? AND task_id = ?",
            (*columns.values(), utcnow(), scenario_id, self.task_id),
        )
        return self.scenario(scenario_id)

    def _insert_scenario(
        self,
        category: TestCategory,
        origin: EntryOrigin,
        statement: str,
        rationale: str | None,
        covers: Iterable[str],
        pair: tuple[str | None, str | None],
        status: EntryStatus,
    ) -> Scenario:
        refs, now = json.dumps(list(parse_covers(covers))), utcnow()
        cursor = self.conn.execute(
            "INSERT INTO test_scenarios (task_id, category, origin, statement, rationale, covers,"
            " mock, mock_justification, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.task_id, TestCategory(category), origin, statement, rationale, refs, *pair,
             status, now, now),
        )  # fmt: skip
        return self.scenario(cursor.lastrowid)
