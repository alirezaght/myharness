import json
import sqlite3

import pytest

from harness.design_models import EntryStatus
from harness.interview_report import Provenance
from harness.phases import Phase
from harness.testplan import TestPlanError, TestPlanner
from harness.testplan_models import TestCategory, parse_covers
from harness.testplan_report import ReviewState, plan_hash

C = TestCategory
TABLES = ("test_scenarios", "test_plan_approvals", "events", "checkpoints", "tasks")


def _snapshot(conn):
    return {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in TABLES}


def _to(controller, task_id, phase):
    while controller.get_task(task_id).phase is not phase:
        controller.advance(task_id)


@pytest.fixture
def setup(controller):
    task = controller.create_task("Add CSV export")
    ws = controller.workspace(task.id)
    answered = ws.answer(ws.ask("acceptance_criteria", "When done?").id, "rows export").id
    open_q = ws.ask("scope", "Which rows?").id
    entry = ws.state_design("components", "an Exporter").id
    gone = ws.withdraw_design(ws.state_design("interfaces", "a CLI flag").id).id
    _to(controller, task.id, Phase.TEST_PLAN)
    return TestPlanner(controller, task.id), answered, open_q, entry, gone


@pytest.fixture
def planner(setup):
    return setup[0]


def _states(planner):
    return [(i.ref, i.state) for i in planner.review().items]


def test_provenance_and_only_effective_scenarios_are_plan_content(setup):
    planner, q, _, e, _ = setup
    stated = planner.add(C.UNIT, "header row is written", [f"Q{q}"])
    accepted = planner.propose(C.INTEGRATION, "export via CLI", "end to end path", [f"E{e}"])
    modified = planner.propose(C.FAILURE, "disk full", "error path", [f"Q{q}"])
    rejected = planner.propose(C.E2E, "browser download", "ui", [f"Q{q}"])
    pending = planner.propose(C.REGRESSION, "old flag", "compat", [f"E{e}"])
    withdrawn = planner.add(C.EDGE_CASE, "empty table")
    planner.decide(accepted.id, EntryStatus.ACCEPTED, None)
    planner.decide(modified.id, EntryStatus.MODIFIED, "disk full aborts cleanly")
    planner.decide(rejected.id, EntryStatus.REJECTED, None)
    planner.withdraw(withdrawn.id)
    document = planner.document()
    assert [(s.ref, s.text, s.provenance) for s in document.scenarios] == [
        (stated.ref, "header row is written", Provenance.DEVELOPER),
        (accepted.ref, "export via CLI", Provenance.ACCEPTED_RECOMMENDATION),
        (modified.ref, "disk full aborts cleanly", Provenance.MODIFIED_RECOMMENDATION),
    ]
    rendered = document.render().split("### Review")[0]
    for absent in ("browser download", "old flag", "empty table"):
        assert absent not in rendered
    assert "- (none proposed)" in rendered
    assert (pending.ref, ReviewState.AWAITING_DECISION) in _states(planner)
    with pytest.raises(ValueError, match=f"scenario {rejected.ref} is already rejected"):
        planner.decide(rejected.id, EntryStatus.ACCEPTED, None)


def test_recommendations_must_trace_to_answered_requirements_or_effective_design(setup, conn):
    planner, q, open_q, e, gone = setup
    before = _snapshot(conn)
    with pytest.raises(ValueError, match="must cover at least one requirement"):
        planner.propose(C.UNIT, "x", "why", [])
    for ref, message in (
        (f"Q{open_q}", f"Q{open_q} is not an answered requirement"),
        ("Q999", "Q999 is not an answered requirement"),
        (f"E{gone}", f"E{gone} is not an effective design entry"),
        ("E999", "E999 is not an effective design entry"),
    ):
        with pytest.raises(TestPlanError, match=message):
            planner.propose(C.UNIT, "x", "why", [f"Q{q}", ref])
    with pytest.raises(ValueError, match="covers ref must look like Q<n> or E<n>: P1"):
        planner.add(C.UNIT, "x", ["P1"])
    assert _snapshot(conn) == before
    assert parse_covers([" Q1", "E2", "Q1"]) == ("Q1", "E2")
    scenario = planner.propose(C.UNIT, "x", "why", [f"Q{q}", f"E{e}", f"Q{q}"])
    assert scenario.covers == (f"Q{q}", f"E{e}")
    review = planner.review()
    assert set(review.missing_categories) == set(C)  # informational, pending is not content
    assert planner.add(C.UNIT, "developer case").covers == ()


def test_mock_requires_a_written_justification_in_python_and_the_db(planner, conn):
    before = _snapshot(conn)
    for mock, reason in (("smtp", None), ("smtp", "  "), (None, "external")):
        with pytest.raises(ValueError, match="a mock needs a written justification"):
            planner.add(C.UNIT, "sends mail", mock=mock, mock_reason=reason)
    assert _snapshot(conn) == before
    scenario = planner.add(C.UNIT, "sends mail", mock="smtp", mock_reason="external system")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE test_scenarios SET mock_justification = NULL WHERE id = ?", (scenario.id,)
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO test_scenarios (task_id, category, origin, statement, mock, status,"
            " created_at, updated_at)"
            " VALUES (?, 'unit', 'developer', 's', 'db', 'stated', 'n', 'n')",
            (planner.task_id,),
        )
    with pytest.raises(ValueError, match="a mock needs a written justification"):
        planner.edit(scenario.id, mock_reason=" ")
    edited = planner.edit(scenario.id, clear_mock=True)
    assert (edited.mock, edited.mock_justification) == (None, None)
    assert "mock: none" in planner.document().render()


def test_flagged_mock_blocks_approval_until_cleared(planner, conn):
    plain = planner.add(C.UNIT, "parses rows")
    mocked = planner.add(C.INTEGRATION, "writes file", mock="filesystem", mock_reason="no disk")
    with pytest.raises(ValueError, match=f"scenario {plain.ref} has no mock to flag"):
        planner.flag(plain.id, "hides nothing")
    planner.flag(mocked.id, "the writer is not implemented")
    assert (mocked.ref, ReviewState.MOCK_FLAGGED) in _states(planner)
    assert "[flagged: the writer is not implemented]" in planner.document().render()
    before = _snapshot(conn)
    with pytest.raises(TestPlanError, match=f"flagged scenarios: {mocked.ref}"):
        planner.approve()
    assert _snapshot(conn) == before
    with pytest.raises(TestPlanError, match="needs a written reason"):
        planner.clear_flag(mocked.id, " ")
    planner.clear_flag(mocked.id, "the writer exists; the mock only avoids disk writes")
    with pytest.raises(ValueError, match="is not flagged"):
        planner.clear_flag(mocked.id, "again")
    approval = planner.approve()
    effective = planner.document().scenarios
    assert approval.content_hash == plan_hash(effective) and approval.scenario_count == 2
    events = planner.ws.events()
    assert [e.kind for e in events[-2:]] == ["test_plan.flag_cleared", "test_plan.approved"]
    assert events[-2].payload["reason"].startswith("the writer exists")
    assert events[-1].payload == {
        "content_hash": approval.content_hash,
        "scenarios": 2,
        "approval_id": approval.id,
    }
    checkpoint = planner.ws.latest_checkpoint("test_plan")
    assert checkpoint.payload == {"pending": [], "flagged": [], "approved": True, "blocking": False}
    assert checkpoint.phase == "test_plan"
    # a flag raised after approval invalidates it until the plan is approved again
    planner.flag(mocked.id, "still hides it")
    assert not planner.document().approved
    assert planner.edit(mocked.id, mock="tmp dir", mock_reason="isolation").concern is None
    planner.edit(mocked.id, mock="filesystem", mock_reason="no disk")
    assert planner.document().review.to_dict()["approval"]["id"] == approval.id
    assert not planner.document().approved
    assert _states(planner) == [("plan", ReviewState.CHANGED_SINCE_APPROVAL)]
    planner.approve()
    assert planner.document().approved


def test_approval_blockers_and_invalidation(planner):
    with pytest.raises(TestPlanError, match="the test plan has no scenarios"):
        planner.approve()
    stated = planner.add(C.UNIT, "parses rows")
    first = planner.approve()
    document = planner.document()
    assert document.approved and document.approval == first
    assert planner.review().items == ()
    for change in (
        lambda: planner.edit(stated.id, text="parses quoted rows"),
        lambda: planner.add(C.FAILURE, "bad encoding"),
        lambda: planner.withdraw(planner.document().scenarios[-1].id),
    ):
        change()
        assert not planner.document().approved
        assert _states(planner) == [("plan", ReviewState.CHANGED_SINCE_APPROVAL)]
        planner.approve()
        assert planner.document().approved
    assert len(planner.ws.plan_approvals()) == 4
    edited = planner.scenario(stated.id)
    assert (edited.statement, edited.status) == ("parses quoted rows", EntryStatus.STATED)
    assert edited.updated_at >= edited.created_at
    with pytest.raises(ValueError, match="changes nothing"):
        planner.edit(stated.id, text="parses quoted rows")


def test_editing_an_accepted_recommendation_makes_it_modified(setup):
    planner, q, *_ = setup
    rec = planner.propose(C.UNIT, "header row", "format", [f"Q{q}"])
    pending_states = _states(planner)
    assert (rec.ref, ReviewState.AWAITING_DECISION) in pending_states
    with pytest.raises(TestPlanError, match=f"undecided scenarios: {rec.ref}"):
        planner.approve()
    planner.decide(rec.id, EntryStatus.ACCEPTED, None)
    edited = planner.edit(rec.id, category=C.REGRESSION)
    assert (edited.status, edited.statement, edited.text, edited.category) == (
        EntryStatus.MODIFIED,
        "header row",
        "header row",
        C.REGRESSION,
    )
    assert edited.provenance is Provenance.MODIFIED_RECOMMENDATION
    planner.withdraw(rec.id)
    with pytest.raises(ValueError, match=f"scenario {rec.ref} is withdrawn; nothing to edit"):
        planner.edit(rec.id, text="again")


@pytest.mark.parametrize("phase", [Phase.DESIGN, Phase.FINAL_REVIEW])
def test_mutations_refused_outside_test_plan_and_leave_the_db_unchanged(controller, conn, phase):
    task = controller.create_task("Add CSV export")
    planner = TestPlanner(controller, task.id)
    _to(controller, task.id, Phase.TEST_PLAN)
    scenario = planner.add(C.UNIT, "parses rows", mock="clock", mock_reason="time")
    if phase is Phase.DESIGN:
        controller.go_back(task.id, Phase.DESIGN, "rethink")
        hint = f"harness phase {task.id} next"
    else:
        controller.advance(task.id)
        hint = f'harness phase {task.id} back test_plan --reason "<why>"'
    before = _snapshot(conn)
    for op in (
        lambda: planner.add(C.UNIT, "x"),
        lambda: planner.propose(C.UNIT, "x", "why", ["E1"]),
        lambda: planner.decide(scenario.id, EntryStatus.ACCEPTED, None),
        lambda: planner.edit(scenario.id, text="y"),
        lambda: planner.withdraw(scenario.id),
        lambda: planner.flag(scenario.id, "hides"),
        lambda: planner.clear_flag(scenario.id, "ok"),
        planner.approve,
    ):
        with pytest.raises(TestPlanError, match=f"is in phase {phase}; run: {hint}"):
            op()
    assert _snapshot(conn) == before
    assert planner.document().scenarios == (scenario,)


def test_a_failure_inside_a_mutation_rolls_everything_back(planner, conn, monkeypatch):
    planner.add(C.UNIT, "parses rows")
    before = _snapshot(conn)

    def failing(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(planner.ws, "checkpoint", failing)
    with pytest.raises(OSError, match="disk full"):
        planner.approve()
    with pytest.raises(OSError, match="disk full"):
        planner.add(C.UNIT, "another")
    assert _snapshot(conn) == before
    stored = conn.execute("SELECT covers FROM test_scenarios").fetchall()
    assert [json.loads(row[0]) for row in stored] == [[]]
