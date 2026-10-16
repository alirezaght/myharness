"""Test scenarios of the test-planning phase: categories, coverage references and approvals."""

import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from harness.design_models import EFFECTIVE, EntryOrigin, EntryStatus, provenance_for
from harness.interview_report import Provenance

COVERS_REF = re.compile(r"^[QE][1-9]\d*$")


class TestCategory(StrEnum):
    __test__ = False

    UNIT = "unit"
    INTEGRATION = "integration"
    E2E = "e2e"
    REGRESSION = "regression"
    EDGE_CASE = "edge_case"
    FAILURE = "failure"

    @property
    def heading(self) -> str:
        return CATEGORY_TITLES[self]


CATEGORY_TITLES = {
    TestCategory.UNIT: "Unit tests",
    TestCategory.INTEGRATION: "Integration tests",
    TestCategory.E2E: "End-to-end tests",
    TestCategory.REGRESSION: "Regression tests",
    TestCategory.EDGE_CASE: "Edge cases",
    TestCategory.FAILURE: "Failure scenarios",
}


def parse_covers(values: Iterable[str] | None) -> tuple[str, ...]:
    """Requirement (`Q<n>`) and design entry (`E<n>`) refs: validated, deduplicated, in order."""
    refs: list[str] = []
    for value in values or ():
        ref = (value or "").strip().upper()
        if not COVERS_REF.match(ref):
            raise ValueError(f"covers ref must look like Q<n> or E<n>: {value}")
        if ref not in refs:
            refs.append(ref)
    return tuple(refs)


@dataclass(frozen=True)
class Scenario:
    id: int
    task_id: str
    category: TestCategory
    origin: EntryOrigin
    statement: str
    rationale: str | None
    covers: tuple[str, ...]
    mock: str | None
    mock_justification: str | None
    concern: str | None
    status: EntryStatus
    response: str | None
    created_at: str
    resolved_at: str | None
    updated_at: str

    @property
    def effective(self) -> bool:
        return self.status in EFFECTIVE

    @property
    def text(self) -> str:
        if self.status is EntryStatus.MODIFIED and self.response:
            return self.response
        return self.statement

    @property
    def provenance(self) -> Provenance | None:
        return provenance_for(self.status)

    @property
    def ref(self) -> str:
        return f"S{self.id}"

    @property
    def flagged(self) -> bool:
        return self.concern is not None


def scenario_from_row(row: sqlite3.Row) -> Scenario:
    return Scenario(
        id=row["id"],
        task_id=row["task_id"],
        category=TestCategory(row["category"]),
        origin=EntryOrigin(row["origin"]),
        statement=row["statement"],
        rationale=row["rationale"],
        covers=tuple(json.loads(row["covers"])),
        mock=row["mock"],
        mock_justification=row["mock_justification"],
        concern=row["concern"],
        status=EntryStatus(row["status"]),
        response=row["response"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
        updated_at=row["updated_at"],
    )


@dataclass(frozen=True)
class PlanApproval:
    id: int
    task_id: str
    content_hash: str
    scenario_count: int
    created_at: str


def plan_approval_from_row(row: sqlite3.Row) -> PlanApproval:
    return PlanApproval(
        id=row["id"],
        task_id=row["task_id"],
        content_hash=row["content_hash"],
        scenario_count=row["scenario_count"],
        created_at=row["created_at"],
    )
