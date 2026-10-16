"""Test helper: settle a task up to the final review with synthetic, obviously fixture content."""

from harness.controller import HarnessController, Task
from harness.db import transaction
from harness.design import ArchitectureDesigner
from harness.design_models import DesignSection
from harness.interview import InterviewEngine
from harness.phases import PHASE_ORDER, Phase
from harness.review import FinalReviewer
from harness.spec_document import ACCEPTANCE_TOPIC
from harness.state_models import ProposalStatus, Question, SkipSource
from harness.testplan import TestPlanner
from harness.testplan_models import TestCategory

FIXTURE = "test fixture"
FIXTURE_CRITERION = "fixture acceptance criterion"
FIXTURE_SCENARIO = "fixture scenario"


def settle(controller: HarnessController, task_id: str) -> Task:
    """Walk a task from investigation..final_review into final_review with nothing blocking.

    A blocked task already in final_review goes back to the interview first. Idempotent for
    parts already covered; never decides a pending recommendation or decision change.
    """
    task = controller.get_task(task_id)
    if PHASE_ORDER.index(task.phase) > PHASE_ORDER.index(Phase.FINAL_REVIEW):
        raise ValueError(f"task {task_id} is already in {task.phase}")
    _investigation(controller, task_id)
    if task.phase is Phase.FINAL_REVIEW and FinalReviewer(controller, task_id).report().blocking:
        controller.go_back(task_id, Phase.INTERVIEW, FIXTURE)
    steps = {
        Phase.INVESTIGATION: lambda *_: None,
        Phase.INTERVIEW: _interview,
        Phase.DESIGN: _design,
        Phase.TEST_PLAN: _test_plan,
    }
    while (task := controller.get_task(task_id)).phase is not Phase.FINAL_REVIEW:
        steps[task.phase](controller, task_id)
        controller.advance(task_id)
    report = FinalReviewer(controller, task_id).report()
    assert not report.blocking, report.render()
    return task


def _investigation(controller: HarnessController, task_id: str) -> None:
    ws = controller.workspace(task_id)
    if ws.latest_checkpoint("investigation") is None:
        with transaction(controller.conn):
            ws.checkpoint("investigation", {"decisions": []})


def _has_criterion(engine: InterviewEngine) -> bool:
    return any(r.topic == ACCEPTANCE_TOPIC for r in engine.summary().requirements)


def _settle_question(engine: InterviewEngine, question: Question) -> None:
    assert not engine.pending_proposals(question.id), f"Q{question.id} has a pending proposal"
    if question.topic == ACCEPTANCE_TOPIC and not _has_criterion(engine):
        engine.answer(question.id, FIXTURE_CRITERION)
    else:
        engine.skip(question.id, SkipSource.NOT_RELEVANT, FIXTURE)


def _interview(controller: HarnessController, task_id: str) -> None:
    engine, ws = InterviewEngine(controller, task_id), controller.workspace(task_id)
    while asked := engine.next():
        for question in asked:
            _settle_question(engine, question)
    for question in ws.open_questions():
        _settle_question(engine, question)
    assert not ws.proposals(ProposalStatus.PENDING), "pending proposals need the developer"
    if not _has_criterion(engine):
        engine.answer(engine.ask(ACCEPTANCE_TOPIC, FIXTURE_CRITERION).id, FIXTURE_CRITERION)
    assert not engine.checklist().blocking, engine.checklist().render()


def _design(controller: HarnessController, task_id: str) -> None:
    designer = ArchitectureDesigner(controller, task_id)
    for section in DesignSection:
        if section not in designer.coverage().covered:
            designer.none(section, FIXTURE)
    assert not designer.coverage().blocking, designer.coverage().render()


def _test_plan(controller: HarnessController, task_id: str) -> None:
    planner = TestPlanner(controller, task_id)
    if planner.document().approved:
        return
    if not planner.document().scenarios:
        planner.add(TestCategory.UNIT, FIXTURE_SCENARIO)
    planner.approve()
