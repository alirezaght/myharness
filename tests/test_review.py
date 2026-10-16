from collections.abc import Callable

import pytest

from harness.controller import HarnessController
from harness.db import transaction
from harness.design import ArchitectureDesigner
from harness.design_models import DesignSection, EntryStatus
from harness.interview import InterviewEngine
from harness.phases import Phase
from harness.review import PASSED_EVENT, FinalReviewer, ReviewBlocked
from harness.review_report import CHECK_TITLES, Check, ReviewState
from harness.spec_manager import SpecError, SpecificationManager
from harness.spec_store import SpecStore
from harness.state_models import ProposalStatus
from harness.testplan import TestPlanner
from harness.testplan_models import TestCategory
from tests.complete_task import settle
from tests.sample_repo import tree_snapshot

TABLES = ("tasks", "events", "checkpoints", "phase_transitions", "spec_versions")


def _bare(controller: HarnessController) -> str:
    task = controller.create_task("fixture request", "trivial")
    for _ in range(4):
        controller.advance(task.id)
    return task.id


def _snapshot(controller: HarnessController) -> dict[str, list[tuple]]:
    return {
        table: [tuple(row) for row in controller.conn.execute(f"SELECT * FROM {table}")]
        for table in TABLES
    }


def _item(report, check: Check):
    return next(item for item in report.items if item.check is check)


def _pending_change(controller: HarnessController, task_id: str) -> None:
    with transaction(controller.conn):
        controller.workspace(task_id).propose_change(
            "DEC-0001", 1, "fixture", "fixture content", "fixture", "fixture", "fixture"
        )


def test_bare_task_reports_all_ten_items_in_spec_order(controller):
    task_id = _bare(controller)
    report = FinalReviewer(controller, task_id).report()
    assert [item.check for item in report.items] == list(Check)
    assert [item.title for item in report.items] == list(CHECK_TITLES.values())
    assert report.blocking
    clear = _item(report, Check.REQUIREMENTS_CLEAR)
    assert clear.state is ReviewState.BLOCKED and clear.route is Phase.INTERVIEW
    assert any(f.startswith("catalogue:") for f in clear.findings)
    assert _item(report, Check.ACCEPTANCE_CRITERIA_TESTABLE).state is ReviewState.BLOCKED
    plan = _item(report, Check.TEST_PLAN_SUFFICIENT)
    assert plan.findings == ("plan not_approved",) and plan.route is Phase.TEST_PLAN
    diagrams = _item(report, Check.NO_DIAGRAM_COMMENTS)
    assert diagrams.state is ReviewState.NOT_APPLICABLE and "Phase 2" in diagrams.note
    assert report.route_back is Phase.INTERVIEW
    text = report.render().splitlines()
    assert text[0] == "final design review (SPEC §3 step 6):"
    assert f'    → harness phase {task_id} back interview --reason "<why>"' in text
    assert "blocking: yes" in text
    assert text[-1] == f'route back: harness phase {task_id} back interview --reason "<why>"'
    assert any(line.startswith("  n/a     No unresolved diagram comments") for line in text)


def _interview(controller, task_id, change: Callable[[InterviewEngine], None]) -> None:
    controller.go_back(task_id, Phase.INTERVIEW, "fixture")
    change(InterviewEngine(controller, task_id))


def _design(controller, task_id, change: Callable[[ArchitectureDesigner], None]) -> None:
    controller.go_back(task_id, Phase.DESIGN, "fixture")
    change(ArchitectureDesigner(controller, task_id))


def _plan(controller, task_id, change: Callable[[TestPlanner], None]) -> None:
    controller.go_back(task_id, Phase.TEST_PLAN, "fixture")
    change(TestPlanner(controller, task_id))


def _asked(engine: InterviewEngine) -> None:
    engine.ask("edge_cases", "fixture open question")
    engine.next()


def _proposed(engine: InterviewEngine):
    question = engine.ask("edge_cases", "fixture decision", decision=True)
    return engine.propose(question.id, "fixture", "fixture rec", "fixture why")


def _rejected(engine: InterviewEngine) -> None:
    engine.decide(_proposed(engine).id, ProposalStatus.REJECTED, None)


def _withdraw(section: DesignSection):
    def change(designer: ArchitectureDesigner) -> None:
        entry = next(e for e in designer.document().entries if e.section is section)
        designer.withdraw(entry.id)

    return change


def _design_pending(designer: ArchitectureDesigner) -> None:
    _withdraw(DesignSection.INTERFACES)(designer)
    designer.propose(DesignSection.INTERFACES, "fixture interface", "fixture why")


def _conflicting(designer: ArchitectureDesigner) -> None:
    _withdraw(DesignSection.DATA_FLOW)(designer)
    entry = designer.propose(DesignSection.DATA_FLOW, "fixture flow", "fixture why")
    designer.none(DesignSection.DATA_FLOW, "fixture")
    designer.decide(entry.id, EntryStatus.ACCEPTED, None)


def _flagged(planner: TestPlanner) -> None:
    mocked = planner.add(TestCategory.UNIT, "fixture mocked", mock="fixture", mock_reason="fixture")
    planner.approve()
    planner.flag(mocked.id, "fixture concern")


def _plan_pending(planner: TestPlanner) -> None:
    criterion = next(q for q in planner.ws.questions() if q.answer is not None)
    planner.propose(TestCategory.FAILURE, "fixture failure", "fixture why", [f"Q{criterion.id}"])


SIGNALS = [
    ("not_asked", lambda c, t: _interview(c, t, lambda e: e.ask("edge_cases", "fixture q")),
     Check.REQUIREMENTS_CLEAR, Phase.INTERVIEW, "not_asked"),
    ("unanswered", lambda c, t: _interview(c, t, _asked),
     Check.NO_BLOCKING_QUESTIONS, Phase.INTERVIEW, "unanswered"),
    ("rejected", lambda c, t: _interview(c, t, _rejected),
     Check.NO_BLOCKING_QUESTIONS, Phase.INTERVIEW, "recommendation_rejected"),
    ("interview_decision", lambda c, t: _interview(c, t, _proposed),
     Check.DECISIONS_APPROVED, Phase.INTERVIEW, "awaiting_decision (interview)"),
    ("design_decision", lambda c, t: _design(c, t, _design_pending),
     Check.DECISIONS_APPROVED, Phase.DESIGN, "awaiting_decision (design)"),
    ("conflicting", lambda c, t: _design(c, t, _conflicting),
     Check.ARCHITECTURE_CONSISTENT, Phase.DESIGN, "section:data_flow conflicting"),
    ("configuration", lambda c, t: _design(c, t, _withdraw(DesignSection.CONFIGURATION)),
     Check.ARCHITECTURE_CONSISTENT, Phase.DESIGN, "section:configuration uncovered"),
    ("components", lambda c, t: _design(c, t, _withdraw(DesignSection.COMPONENTS)),
     Check.RESPONSIBILITIES_DEFINED, Phase.DESIGN, "section:components uncovered"),
    ("interfaces", lambda c, t: _design(c, t, _withdraw(DesignSection.INTERFACES)),
     Check.INTERACTIONS_UNDERSTOOD, Phase.DESIGN, "section:interfaces uncovered"),
    ("plan_decision", lambda c, t: _plan(c, t, _plan_pending),
     Check.DECISIONS_APPROVED, Phase.TEST_PLAN, "awaiting_decision (test_plan)"),
    ("flagged", lambda c, t: _plan(c, t, _flagged),
     Check.TEST_PLAN_SUFFICIENT, Phase.TEST_PLAN, "mock_flagged"),
    ("changed", lambda c, t: _plan(c, t, lambda p: p.add(TestCategory.E2E, "fixture e2e")),
     Check.TEST_PLAN_SUFFICIENT, Phase.TEST_PLAN, "plan changed_since_approval"),
    ("conflict", _pending_change, Check.NO_IGNORED_CONFLICTS, None, "pending change to DEC-0001"),
]  # fmt: skip


@pytest.mark.parametrize(
    ("signal", "check", "route", "finding"),
    [pytest.param(s, c, r, f, id=name) for name, s, c, r, f in SIGNALS],
)
def test_each_signal_blocks_its_item_and_routes_back(controller, signal, check, route, finding):
    task_id = controller.create_task("fixture request", "trivial").id
    settle(controller, task_id)
    signal(controller, task_id)
    while controller.get_task(task_id).phase is not Phase.FINAL_REVIEW:
        controller.advance(task_id)
    report = FinalReviewer(controller, task_id).report()
    item = _item(report, check)
    assert item.state is ReviewState.BLOCKED and item.route is route
    assert any(finding in f for f in item.findings), item.findings
    assert report.blocking
    hints = item.hints(task_id, Phase.FINAL_REVIEW)
    if route is None:
        change = controller.workspace(task_id).decision_changes()[0]
        assert hints == [f"harness decision decide {task_id} {change.id} accept|reject|modify"]
        assert "route back:" not in report.render()
    else:
        assert hints[0] == f'harness phase {task_id} back {route} --reason "<why>"'


def test_blocked_advance_writes_nothing(controller):
    task_id = _bare(controller)
    before = _snapshot(controller)
    with pytest.raises(ReviewBlocked) as raised:
        controller.advance(task_id)
    assert raised.value.report.blocking
    assert f"task {task_id} cannot enter approval" in str(raised.value)
    assert "blocking: yes" in str(raised.value)
    assert _snapshot(controller) == before
    assert controller.get_task(task_id).phase is Phase.FINAL_REVIEW


def test_pass_records_event_and_checkpoint_with_matching_hash(controller):
    task_id = controller.create_task("fixture request", "trivial").id
    settle(controller, task_id)
    expected = FinalReviewer(controller, task_id).report()
    assert not expected.blocking
    assert controller.advance(task_id).phase is Phase.APPROVAL
    ws = controller.workspace(task_id)
    kinds = [e.kind for e in ws.events()]
    assert kinds[-2:] == [PASSED_EVENT, "phase.changed"]
    event = ws.events()[-2]
    checkpoint = ws.latest_checkpoint("final_review")
    assert checkpoint.phase is Phase.FINAL_REVIEW
    assert checkpoint.payload["blocking"] is False
    assert checkpoint.payload["state_hash"] == event.payload["state_hash"] == expected.state_hash
    assert FinalReviewer(controller, task_id).report().state_hash == expected.state_hash
    assert checkpoint.payload["items"] == {str(i.check): str(i.state) for i in expected.items}


def _manager(controller, task_id, repo):
    return SpecificationManager(
        controller, task_id, SpecStore(repo / "docs" / "specs" / task_id), repo
    )


def test_spec_approve_refused_after_pending_decision_change(controller, sample_repo):
    task_id = controller.create_task("fixture request", "trivial").id
    settle(controller, task_id)
    controller.advance(task_id)
    _pending_change(controller, task_id)
    before, files = _snapshot(controller), tree_snapshot(sample_repo)
    with pytest.raises(SpecError, match="final design review blocks approval") as raised:
        _manager(controller, task_id, sample_repo).approve()
    assert "No known conflicts are ignored" in str(raised.value)
    assert (_snapshot(controller), tree_snapshot(sample_repo)) == (before, files)


def test_spec_approve_succeeds_when_review_passes(controller, sample_repo):
    task_id = controller.create_task("fixture request", "trivial").id
    settle(controller, task_id)
    controller.advance(task_id)
    record, previous = _manager(controller, task_id, sample_repo).approve()
    assert (record.version, previous) == (1, None)
