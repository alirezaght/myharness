"""Test plan outputs: the review of open items and the rendered (approved) test plan.

Only effective scenarios (stated, accepted, modified) are plan content; nothing is invented.
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from harness.design_models import EntryStatus
from harness.testplan_models import PlanApproval, Scenario, TestCategory

TITLE = "## Test plan"
PLAN_REF = "plan"


class ReviewState(StrEnum):
    AWAITING_DECISION = "awaiting_decision"
    MOCK_FLAGGED = "mock_flagged"
    NOT_APPROVED = "not_approved"
    CHANGED_SINCE_APPROVAL = "changed_since_approval"


@dataclass(frozen=True)
class ReviewItem:
    ref: str
    state: ReviewState
    category: TestCategory | None = None
    text: str | None = None

    def render(self) -> str:
        tag = f" [{self.category}]" if self.category else ""
        detail = f" — {self.text}" if self.text else ""
        return f"{self.ref}{tag} {self.state}{detail}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "state": str(self.state),
            "category": None if self.category is None else str(self.category),
            "text": self.text,
        }


def plan_hash(scenarios: Iterable[Scenario]) -> str:
    """The content hash of the effective scenarios an approval is bound to."""
    content = [
        [s.id, str(s.category), s.text, list(s.covers), s.mock, s.mock_justification]
        for s in sorted((s for s in scenarios if s.effective), key=lambda s: s.id)
    ]
    digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _approval_dict(approval: PlanApproval | None) -> dict[str, Any] | None:
    if approval is None:
        return None
    return {
        "id": approval.id,
        "content_hash": approval.content_hash,
        "scenarios": approval.scenario_count,
        "created_at": approval.created_at,
    }


@dataclass(frozen=True)
class PlanReview:
    items: tuple[ReviewItem, ...]
    missing_categories: tuple[TestCategory, ...]
    counts: dict[str, int]
    approval: PlanApproval | None
    current: bool

    @property
    def blocking(self) -> bool:
        return bool(self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocking": self.blocking,
            "unresolved": [item.to_dict() for item in self.items],
            "missing_categories": [str(c) for c in self.missing_categories],
            "counts": {"unresolved": len(self.items), **self.counts},
            "approval": _approval_dict(self.approval),
            "approved": self.current,
        }

    def render(self) -> str:
        lines = [f"test plan review ({self.counts['effective']} effective scenario(s)):"]
        lines += [f"  {item.render()}" for item in self.items] or ["  (none)"]
        missing = ", ".join(str(c) for c in self.missing_categories)
        lines.append(
            f"categories without scenarios: {missing} (not required)"
            if missing
            else "(every category has a scenario)"
        )
        approval = self.approval
        lines.append(
            f"approval: {approval.created_at} {approval.content_hash}"
            if approval
            else "approval: none"
        )
        lines.append(f"blocking: {'yes' if self.blocking else 'no'}")
        return "\n".join(lines)


def build_review(
    scenarios: Iterable[Scenario], approval: PlanApproval | None, flagged_since: bool = False
) -> PlanReview:
    """`flagged_since`: a mock was flagged after the approval, so it needs a new approval."""
    scenarios = list(scenarios)
    effective = [s for s in scenarios if s.effective]
    items = [
        ReviewItem(s.ref, ReviewState.AWAITING_DECISION, s.category, s.statement)
        for s in scenarios
        if s.status is EntryStatus.PENDING
    ]
    items += [
        ReviewItem(s.ref, ReviewState.MOCK_FLAGGED, s.category, s.concern)
        for s in effective
        if s.flagged
    ]
    matches = approval is not None and approval.content_hash == plan_hash(effective)
    matches = matches and not flagged_since
    if approval is None:
        items.append(ReviewItem(PLAN_REF, ReviewState.NOT_APPROVED))
    elif not matches:
        items.append(ReviewItem(PLAN_REF, ReviewState.CHANGED_SINCE_APPROVAL))
    flagged = sum(s.flagged for s in effective)
    counts = {
        "effective": len(effective),
        "stated": sum(s.status is EntryStatus.STATED for s in effective),
        "decided": sum(s.status is not EntryStatus.STATED for s in effective),
        "mocked": sum(s.mock is not None for s in effective),
        "flagged": flagged,
    }
    used = {s.category for s in effective}
    missing = tuple(c for c in TestCategory if c not in used)
    return PlanReview(tuple(items), missing, counts, approval, matches and not flagged)


def scenario_line(s: Scenario) -> str:
    covers = ", ".join(s.covers) or "(not stated)"
    mock = f"{s.mock} (justified: {s.mock_justification})" if s.mock else "none"
    flag = f" [flagged: {s.concern}]" if s.flagged else ""
    return f"- {s.ref} {s.text} — covers {covers} — mock: {mock} ({s.provenance}){flag}"


def _scenario_dict(s: Scenario) -> dict[str, Any]:
    return {
        "id": s.id,
        "ref": s.ref,
        "text": s.text,
        "covers": list(s.covers),
        "mock": s.mock,
        "mock_justification": s.mock_justification,
        "concern": s.concern,
        "provenance": str(s.provenance),
    }


@dataclass(frozen=True)
class TestPlanDocument:
    __test__ = False
    scenarios: tuple[Scenario, ...]
    review: PlanReview

    @property
    def approval(self) -> PlanApproval | None:
        """The recorded approval, only while it still matches the effective scenarios."""
        return self.review.approval if self.review.current else None

    @property
    def approved(self) -> bool:
        return self.approval is not None

    def section(self, category: TestCategory) -> list[Scenario]:
        return [s for s in self.scenarios if s.category is category]

    def section_lines(self, category: TestCategory) -> list[str]:
        return [scenario_line(s) for s in self.section(category)] or ["- (none proposed)"]

    def plan_lines(self) -> list[str]:
        """The approved plan as spec lines; only categories that have scenarios get a heading."""
        approval = self.approval
        if approval is None:
            return []
        lines = [
            f"- approved {approval.created_at} ({approval.scenario_count} scenario(s);"
            f" {approval.content_hash})"
        ]
        for category in TestCategory:
            if self.section(category):
                lines += [f"### {category.heading}", *self.section_lines(category)]
        return lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "categories": {
                str(c): [_scenario_dict(s) for s in self.section(c)] for c in TestCategory
            },
            "approved": self.approved,
            "review": self.review.to_dict(),
        }

    def render(self) -> str:
        lines = [TITLE]
        for category in TestCategory:
            lines += ["", f"### {category.heading}", *self.section_lines(category)]
        lines += ["", "### Review", self.review.render()]
        return "\n".join(lines)


def build_document(
    scenarios: Iterable[Scenario], approval: PlanApproval | None, flagged_since: bool = False
) -> TestPlanDocument:
    all_scenarios = tuple(scenarios)
    effective = tuple(s for s in all_scenarios if s.effective)
    return TestPlanDocument(effective, build_review(all_scenarios, approval, flagged_since))
