"""Final design review (SPEC §3 Phase 6): the ten completeness checks and where each routes back.

Pure: the report is derived only from existing deterministic state; it never changes anything.
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from harness.design_models import DesignSection
from harness.design_report import CoverageState, DesignCoverage
from harness.interview_report import ChecklistState, InterviewChecklist, InterviewSummary
from harness.phases import PHASE_ORDER, Phase, is_backward
from harness.spec_document import ACCEPTANCE_TOPIC
from harness.state_decisions import DecisionChange
from harness.testplan_report import ReviewState as PlanState
from harness.testplan_report import TestPlanDocument


class Check(StrEnum):
    REQUIREMENTS_CLEAR = "requirements_clear"
    DECISIONS_APPROVED = "decisions_approved"
    ARCHITECTURE_CONSISTENT = "architecture_consistent"
    RESPONSIBILITIES_DEFINED = "responsibilities_defined"
    INTERACTIONS_UNDERSTOOD = "interactions_understood"
    ACCEPTANCE_CRITERIA_TESTABLE = "acceptance_criteria_testable"
    TEST_PLAN_SUFFICIENT = "test_plan_sufficient"
    NO_BLOCKING_QUESTIONS = "no_blocking_questions"
    NO_DIAGRAM_COMMENTS = "no_diagram_comments"
    NO_IGNORED_CONFLICTS = "no_ignored_conflicts"


CHECK_TITLES = {
    Check.REQUIREMENTS_CLEAR: "Requirements are clear",
    Check.DECISIONS_APPROVED: "Significant decisions are approved",
    Check.ARCHITECTURE_CONSISTENT: "Architecture is consistent",
    Check.RESPONSIBILITIES_DEFINED: "Component responsibilities are defined",
    Check.INTERACTIONS_UNDERSTOOD: "Component interactions are understood",
    Check.ACCEPTANCE_CRITERIA_TESTABLE: "Acceptance criteria are testable",
    Check.TEST_PLAN_SUFFICIENT: "The test plan is sufficient",
    Check.NO_BLOCKING_QUESTIONS: "No blocking questions remain",
    Check.NO_DIAGRAM_COMMENTS: "No unresolved diagram comments remain",
    Check.NO_IGNORED_CONFLICTS: "No known conflicts are ignored",
}

ROUTES: dict[Check, Phase | None] = {
    Check.REQUIREMENTS_CLEAR: Phase.INTERVIEW,
    Check.DECISIONS_APPROVED: Phase.INTERVIEW,
    Check.ARCHITECTURE_CONSISTENT: Phase.DESIGN,
    Check.RESPONSIBILITIES_DEFINED: Phase.DESIGN,
    Check.INTERACTIONS_UNDERSTOOD: Phase.DESIGN,
    Check.ACCEPTANCE_CRITERIA_TESTABLE: Phase.INTERVIEW,
    Check.TEST_PLAN_SUFFICIENT: Phase.TEST_PLAN,
    Check.NO_BLOCKING_QUESTIONS: Phase.INTERVIEW,
    Check.NO_DIAGRAM_COMMENTS: None,
    Check.NO_IGNORED_CONFLICTS: None,
}

SECTION_CHECKS = {
    DesignSection.COMPONENTS: Check.RESPONSIBILITIES_DEFINED,
    DesignSection.RESPONSIBILITIES: Check.RESPONSIBILITIES_DEFINED,
    DesignSection.INTERACTIONS: Check.INTERACTIONS_UNDERSTOOD,
    DesignSection.DATA_FLOW: Check.INTERACTIONS_UNDERSTOOD,
    DesignSection.INTERFACES: Check.INTERACTIONS_UNDERSTOOD,
    DesignSection.CONFIGURATION: Check.ARCHITECTURE_CONSISTENT,
    DesignSection.FAILURE_HANDLING: Check.ARCHITECTURE_CONSISTENT,
    DesignSection.REQUIRED_BEHAVIOUR: Check.ARCHITECTURE_CONSISTENT,
}
INTERVIEW_CHECKS = {
    ChecklistState.NOT_ASKED: Check.REQUIREMENTS_CLEAR,
    ChecklistState.AWAITING_DECISION: Check.DECISIONS_APPROVED,
    ChecklistState.UNANSWERED: Check.NO_BLOCKING_QUESTIONS,
    ChecklistState.RECOMMENDATION_REJECTED: Check.NO_BLOCKING_QUESTIONS,
}
DIAGRAM_NOTE = "diagrams and comments are Phase 2"
NO_CRITERIA = f"no requirement on topic {ACCEPTANCE_TOPIC}"
REASON = '--reason "<why>"'
TOKENS = {"ok": "ok     ", "blocked": "BLOCKED", "not_applicable": "n/a    "}


class ReviewState(StrEnum):
    OK = "ok"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class ReviewItem:
    check: Check
    state: ReviewState
    findings: tuple[str, ...] = ()
    route: Phase | None = None
    note: str | None = None
    changes: tuple[int, ...] = ()

    @property
    def title(self) -> str:
        return CHECK_TITLES[self.check]

    def hints(self, task_id: str, phase: Phase | None = None) -> list[str]:
        """Advice only: the review never moves the task."""
        if self.state is not ReviewState.BLOCKED:
            return []
        hints = [phase_hint(task_id, self.route, phase)] if self.route else []
        if self.check is Check.ACCEPTANCE_CRITERIA_TESTABLE:
            hints.append(f'harness interview ask {task_id} {ACCEPTANCE_TOPIC} "<text>"')
        hints += [
            f"harness decision decide {task_id} {c} accept|reject|modify" for c in self.changes
        ]
        return hints

    def render(self, task_id: str, phase: Phase | None = None) -> list[str]:
        detail = "; ".join(self.findings) if self.findings else (self.note or "")
        line = f"  {TOKENS[self.state]} {self.title}" + (f" — {detail}" if detail else "")
        return [line.rstrip(), *(f"    → {hint}" for hint in self.hints(task_id, phase))]

    def to_dict(self, task_id: str, phase: Phase | None = None) -> dict[str, Any]:
        return {
            "check": str(self.check),
            "title": self.title,
            "state": str(self.state),
            "findings": list(self.findings),
            "route": None if self.route is None else str(self.route),
            "note": self.note,
            "hints": self.hints(task_id, phase),
        }


def phase_hint(task_id: str, route: Phase, phase: Phase | None) -> str:
    if phase is None or is_backward(phase, route):
        return f"harness phase {task_id} back {route} {REASON}"
    return f"finish phase {route}, then: harness phase {task_id} next"


@dataclass(frozen=True)
class ReviewReport:
    task_id: str
    items: tuple[ReviewItem, ...]
    signals: dict[str, Any]
    phase: Phase | None = None

    @property
    def blocked(self) -> list[ReviewItem]:
        return [item for item in self.items if item.state is ReviewState.BLOCKED]

    @property
    def blocking(self) -> bool:
        return bool(self.blocked)

    @property
    def route_back(self) -> Phase | None:
        routes = [item.route for item in self.blocked if item.route is not None]
        return min(routes, key=PHASE_ORDER.index) if routes else None

    def _content(self) -> dict[str, Any]:
        route = self.route_back
        return {
            "blocking": self.blocking,
            "items": [item.to_dict(self.task_id, self.phase) for item in self.items],
            "route_back": None if route is None else str(route),
            "signals": self.signals,
        }

    @property
    def state_hash(self) -> str:
        text = json.dumps(self._content(), sort_keys=True)
        return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"

    def to_dict(self) -> dict[str, Any]:
        return {**self._content(), "state_hash": self.state_hash}

    def checkpoint_payload(self) -> dict[str, Any]:
        return {
            "blocking": self.blocking,
            "state_hash": self.state_hash,
            "items": {str(item.check): str(item.state) for item in self.items},
        }

    def render(self) -> str:
        lines = ["final design review (SPEC §3 step 6):"]
        lines += [line for item in self.items for line in item.render(self.task_id, self.phase)]
        lines.append(f"blocking: {'yes' if self.blocking else 'no'}")
        route = self.route_back
        if route is not None and (self.phase is None or is_backward(self.phase, route)):
            lines.append(f"route back: {phase_hint(self.task_id, route, self.phase)}")
        return "\n".join(lines)


@dataclass
class _Collector:
    findings: dict[Check, list[tuple[str, Phase]]] = field(
        default_factory=lambda: {check: [] for check in Check}
    )

    def add(self, check: Check, finding: str, phase: Phase) -> None:
        self.findings[check].append((finding, phase))


def _item(check: Check, found: list[tuple[str, Phase]]) -> ReviewItem:
    if not found:
        return ReviewItem(check, ReviewState.OK)
    if check is Check.DECISIONS_APPROVED:
        text = tuple(f"{finding} ({phase})" for finding, phase in found)
        return ReviewItem(
            check, ReviewState.BLOCKED, text, min((p for _, p in found), key=PHASE_ORDER.index)
        )
    return ReviewItem(check, ReviewState.BLOCKED, tuple(f for f, _ in found), ROUTES[check])


def _collect(
    checklist: InterviewChecklist, coverage: DesignCoverage, plan: TestPlanDocument
) -> _Collector:
    found = _Collector()
    for entry in checklist.items:
        found.add(INTERVIEW_CHECKS[entry.state], f"{entry.ref} {entry.state}", Phase.INTERVIEW)
    for entry in coverage.items:
        if entry.state is CoverageState.AWAITING_DECISION:
            check = Check.DECISIONS_APPROVED
        elif entry.state is CoverageState.CONFLICTING:
            check = Check.ARCHITECTURE_CONSISTENT
        else:
            check = SECTION_CHECKS[entry.section]
        found.add(check, f"{entry.ref} {entry.state}", Phase.DESIGN)
    for entry in plan.review.items:
        check = (
            Check.DECISIONS_APPROVED
            if entry.state is PlanState.AWAITING_DECISION
            else Check.TEST_PLAN_SUFFICIENT
        )
        found.add(check, f"{entry.ref} {entry.state}", Phase.TEST_PLAN)
    return found


def _signals(
    summary: InterviewSummary,
    coverage: DesignCoverage,
    plan: TestPlanDocument,
    criteria: list[str],
    pending: list[DecisionChange],
) -> dict[str, Any]:
    checklist = summary.checklist
    return {
        "interview": {
            "unresolved": [item.ref for item in checklist.items],
            "answered": checklist.answered,
            "decided": checklist.decided,
            "skipped": checklist.skipped,
            "requirements": len(summary.requirements),
        },
        "design": {
            "unresolved": [item.ref for item in coverage.items],
            "covered": [str(section) for section in coverage.covered],
            **coverage.counts,
        },
        "test_plan": {
            "approved": plan.approved,
            "unresolved": [item.ref for item in plan.review.items],
            **plan.review.counts,
        },
        "acceptance_criteria": criteria,
        "decision_changes": [change.ref for change in pending],
    }


def build_report(
    task_id: str,
    summary: InterviewSummary,
    coverage: DesignCoverage,
    plan: TestPlanDocument,
    pending_changes: Iterable[DecisionChange],
    phase: Phase | None = None,
) -> ReviewReport:
    pending = list(pending_changes)
    found = _collect(summary.checklist, coverage, plan)
    criteria = [
        f"Q{r.question_id}" if r.question_id is not None else r.text
        for r in summary.requirements
        if r.topic == ACCEPTANCE_TOPIC
    ]
    if not criteria:
        found.add(Check.ACCEPTANCE_CRITERIA_TESTABLE, NO_CRITERIA, Phase.INTERVIEW)
    items = []
    for check in Check:
        if check is Check.NO_DIAGRAM_COMMENTS:
            items.append(ReviewItem(check, ReviewState.NOT_APPLICABLE, note=DIAGRAM_NOTE))
        elif check is Check.NO_IGNORED_CONFLICTS:
            items.append(_conflicts(pending))
        else:
            items.append(_item(check, found.findings[check]))
    signals = _signals(summary, coverage, plan, criteria, pending)
    return ReviewReport(task_id, tuple(items), signals, phase)


def _conflicts(pending: list[DecisionChange]) -> ReviewItem:
    check = Check.NO_IGNORED_CONFLICTS
    if not pending:
        return ReviewItem(check, ReviewState.OK)
    findings = tuple(f"{c.ref} pending change to {c.decision_id}" for c in pending)
    return ReviewItem(check, ReviewState.BLOCKED, findings, changes=tuple(c.id for c in pending))
