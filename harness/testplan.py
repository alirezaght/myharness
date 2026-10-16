"""Test Planner (SPEC §3 Phase 5): developer-approved scenarios that trace to the task."""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from harness.controller import HarnessController
from harness.db import transaction, utcnow
from harness.design_models import EntryStatus
from harness.errors import RecordNotFound
from harness.phases import PHASE_ORDER, Phase
from harness.state_models import QuestionStatus
from harness.state_testplan import UNSET
from harness.testplan_models import PlanApproval, Scenario, TestCategory, parse_covers
from harness.testplan_report import (
    PlanReview,
    ReviewState,
    TestPlanDocument,
    build_document,
    plan_hash,
)

__all__ = ["TestPlanError", "TestPlanner"]
AWAITING, FLAGGED = ReviewState.AWAITING_DECISION, ReviewState.MOCK_FLAGGED


class TestPlanError(ValueError):
    __test__ = False


class TestPlanner:
    __test__ = False

    def __init__(self, controller: HarnessController, task_id: str):
        self.controller = controller
        self.conn = controller.conn
        self.task_id = controller.get_task(task_id).id
        self.ws = controller.workspace(task_id)

    # Read-only views

    def document(self) -> TestPlanDocument:
        approval = self.ws.latest_plan_approval()
        return build_document(self.ws.scenarios(), approval, self._flagged_since_approval())

    def review(self) -> PlanReview:
        return self.document().review

    def scenario(self, scenario_id: int) -> Scenario:
        return self.ws.scenario(scenario_id)

    # Mutations: each one transaction, one event and one `test_plan` checkpoint

    def add(
        self,
        category: TestCategory,
        text: str,
        covers: Iterable[str] = (),
        mock: str | None = None,
        mock_reason: str | None = None,
    ) -> Scenario:
        with self._mutation():
            refs = self._check_covers(covers)
            scenario = self.ws.state_scenario(category, text, refs, mock, mock_reason)
            self._event("test_plan.stated", scenario)
        return scenario

    def propose(
        self,
        category: TestCategory,
        text: str,
        rationale: str,
        covers: Iterable[str],
        mock: str | None = None,
        mock_reason: str | None = None,
    ) -> Scenario:
        with self._mutation():
            refs = self._check_covers(covers)
            scenario = self.ws.propose_scenario(category, text, rationale, refs, mock, mock_reason)
            self._event("test_plan.proposed", scenario)
        return scenario

    def decide(self, scenario_id: int, status: EntryStatus, response: str | None) -> Scenario:
        with self._mutation():
            scenario = self.ws.resolve_scenario(scenario_id, status, response)
            self._event("test_plan.decided", scenario, status=str(scenario.status))
        return scenario

    def edit(
        self,
        scenario_id: int,
        text: str | None = None,
        category: TestCategory | None = None,
        covers: Iterable[str] | None = None,
        mock: str | None = UNSET,
        mock_reason: str | None = UNSET,
        clear_mock: bool = False,
    ) -> Scenario:
        with self._mutation():
            refs = None if covers is None else self._check_covers(covers)
            scenario = self.ws.edit_scenario(
                scenario_id, text, category, refs, mock, mock_reason, clear_mock
            )
            self._event("test_plan.edited", scenario, status=str(scenario.status))
        return scenario

    def withdraw(self, scenario_id: int) -> Scenario:
        with self._mutation():
            scenario = self.ws.withdraw_scenario(scenario_id)
            self._event("test_plan.withdrawn", scenario)
        return scenario

    def flag(self, scenario_id: int, concern: str) -> Scenario:
        with self._mutation():
            scenario = self.ws.flag_scenario(scenario_id, concern)
            self._event("test_plan.flagged", scenario, concern=scenario.concern)
        return scenario

    def clear_flag(self, scenario_id: int, reason: str) -> Scenario:
        why = (reason or "").strip()
        if not why:
            raise TestPlanError("clearing a flag needs a written reason (--reason)")
        with self._mutation():
            scenario = self.ws.clear_scenario_flag(scenario_id)
            self._event("test_plan.flag_cleared", scenario, reason=why)
        return scenario

    def approve(self) -> PlanApproval:
        """Record the developer's approval of exactly the current effective scenarios."""
        with self._mutation():
            document = self.document()
            for state, what in ((AWAITING, "undecided"), (FLAGGED, "flagged")):
                refs = self._refs(document.review, state)
                if refs:
                    raise TestPlanError(f"the test plan has {what} scenarios: {', '.join(refs)}")
            if not document.scenarios:
                raise TestPlanError("the test plan has no scenarios")
            digest = plan_hash(document.scenarios)
            approval = self.ws.record_plan_approval(digest, len(document.scenarios))
            payload = {"content_hash": digest, "scenarios": approval.scenario_count}
            self.ws.record_event("test_plan.approved", {**payload, "approval_id": approval.id})
        return approval

    # Internals

    def _flagged_since_approval(self) -> bool:
        """A flag raised after the latest approval invalidates it, even once it is cleared."""
        kinds = [e.kind for e in self.ws.events() if e.kind.startswith("test_plan.")]
        last = max((i for i, k in enumerate(kinds) if k == "test_plan.approved"), default=-1)
        return "test_plan.flagged" in kinds[last + 1 :] if last >= 0 else False

    def _event(self, kind: str, scenario: Scenario, **extra: object) -> None:
        payload = {"id": scenario.id, "category": str(scenario.category), **extra}
        self.ws.record_event(kind, payload)

    def _check_covers(self, covers: Iterable[str]) -> tuple[str, ...]:
        """Every ref names an answered requirement or an effective design entry of this task."""
        refs = parse_covers(covers)
        for ref in refs:
            if not self._resolves(ref):
                what = "an answered requirement" if ref[0] == "Q" else "an effective design entry"
                raise TestPlanError(f"{ref} is not {what} of task {self.task_id}")
        return refs

    def _resolves(self, ref: str) -> bool:
        number = int(ref[1:])
        try:
            if ref[0] == "Q":
                return self.ws.question(number).status is QuestionStatus.ANSWERED
            return self.ws.design_entry(number).effective
        except RecordNotFound:
            return False

    def _refs(self, review: PlanReview, state: ReviewState) -> list[str]:
        return [item.ref for item in review.items if item.state is state]

    @contextmanager
    def _mutation(self) -> Iterator[None]:
        with transaction(self.conn):
            task = self.controller.get_task(self.task_id)
            if task.phase is not Phase.TEST_PLAN:
                before = PHASE_ORDER.index(task.phase) < PHASE_ORDER.index(Phase.TEST_PLAN)
                hint = (
                    f"harness phase {task.id} next"
                    if before
                    else f'harness phase {task.id} back test_plan --reason "<why>"'
                )
                raise TestPlanError(f"task {task.id} is in phase {task.phase}; run: {hint}")
            yield
            review = self.review()
            self.ws.checkpoint(
                "test_plan",
                {
                    "pending": self._refs(review, AWAITING),
                    "flagged": self._refs(review, FLAGGED),
                    "approved": review.current,
                    "blocking": review.blocking,
                },
            )
            self.conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?", (utcnow(), self.task_id)
            )
