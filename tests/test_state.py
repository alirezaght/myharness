import sqlite3

import pytest

from harness.classification import Complexity
from harness.controller import TaskNotFound
from harness.errors import RecordNotFound
from harness.phases import InvalidTransition, Phase
from harness.state import TaskWorkspace
from harness.state_models import ProposalStatus, QuestionSource, QuestionStatus


@pytest.fixture
def task(controller):
    return controller.create_task("Add CSV export")


@pytest.fixture
def ws(controller, task):
    return controller.workspace(task.id)


def _rows(conn, table):
    return [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]


def test_question_lifecycle(ws):
    q = ws.ask(" scope ", " Which formats? ", QuestionSource.INVESTIGATION)
    assert (q.topic, q.text, q.source, q.status) == (
        "scope",
        "Which formats?",
        QuestionSource.INVESTIGATION,
        QuestionStatus.OPEN,
    )
    assert ws.open_questions() == [q]
    answered = ws.answer(q.id, "  CSV only ")
    assert (answered.status, answered.answer) == (QuestionStatus.ANSWERED, "CSV only")
    assert ws.open_questions() == []
    skipped = ws.skip(q.id, "not relevant")
    assert (skipped.status, skipped.answer, skipped.skip_reason) == (
        QuestionStatus.SKIPPED,
        None,
        "not relevant",
    )
    reopened = ws.reopen(q.id)
    assert (reopened.status, reopened.answer, reopened.skip_reason) == (
        QuestionStatus.OPEN,
        None,
        None,
    )
    assert ws.questions() == [reopened] == ws.questions(QuestionStatus.OPEN)
    assert ws.question(q.id) == reopened


@pytest.mark.parametrize("answer", ["", "   \n", None])
def test_empty_answer_keeps_question_open_and_db_unchanged(ws, conn, answer):
    q = ws.ask("scope", "Which formats?")
    before = _rows(conn, "questions")
    with pytest.raises(ValueError):
        ws.answer(q.id, answer)
    with pytest.raises(ValueError):
        ws.skip(q.id, "  ")
    assert _rows(conn, "questions") == before
    assert ws.question(q.id).status is QuestionStatus.OPEN


def test_ask_rejects_empty_text_and_unknown_source(ws, conn):
    with pytest.raises(ValueError):
        ws.ask("scope", " ")
    with pytest.raises(ValueError):
        ws.ask(" ", "Which formats?")
    with pytest.raises(ValueError):
        ws.ask("scope", "Which formats?", "guess")
    assert _rows(conn, "questions") == []


def test_proposal_lifecycle(ws):
    q = ws.ask("storage", "Where to store exports?")
    p = ws.propose("Store on disk", "Use local files", "Simplest", question_id=q.id)
    assert (p.status, p.question_id, p.response, p.resolved_at) == (
        ProposalStatus.PENDING,
        q.id,
        None,
        None,
    )
    accepted = ws.resolve(p.id, ProposalStatus.ACCEPTED)
    assert accepted.status is ProposalStatus.ACCEPTED and accepted.resolved_at
    rejected = ws.resolve(ws.propose("A", "B", "C").id, ProposalStatus.REJECTED, " no ")
    assert (rejected.status, rejected.response) == (ProposalStatus.REJECTED, "no")
    modified = ws.resolve(ws.propose("D", "E", "F").id, "modified", "use S3 instead")
    assert (modified.status, modified.response) == (ProposalStatus.MODIFIED, "use S3 instead")
    assert [x.status for x in ws.proposals()] == ["accepted", "rejected", "modified"]
    assert ws.proposals(ProposalStatus.MODIFIED) == [modified]
    assert ws.proposal(p.id) == accepted


def test_proposal_resolves_exactly_once(ws, conn):
    p = ws.propose("Store on disk", "Use local files", "Simplest")
    first = ws.resolve(p.id, ProposalStatus.ACCEPTED)
    with pytest.raises(ValueError, match="already accepted"):
        ws.resolve(p.id, ProposalStatus.REJECTED, "changed mind")
    assert ws.proposal(p.id) == first


@pytest.mark.parametrize("response", [None, "", "   "])
def test_modified_requires_text_and_pending_is_not_a_resolution(ws, conn, response):
    p = ws.propose("Store on disk", "Use local files", "Simplest")
    before = _rows(conn, "proposals")
    with pytest.raises(ValueError):
        ws.resolve(p.id, ProposalStatus.MODIFIED, response)
    with pytest.raises(ValueError):
        ws.resolve(p.id, ProposalStatus.PENDING)
    with pytest.raises(ValueError):
        ws.propose("t", " ", "r")
    assert _rows(conn, "proposals") == before


def test_events_round_trip_in_order(ws):
    a = ws.record_event("custom.one", {"b": [1, 2], "a": {"nested": True}})
    b = ws.record_event("custom.two")
    events = ws.events()
    assert events[-2:] == [a, b]
    assert (a.payload, b.payload) == ({"b": [1, 2], "a": {"nested": True}}, {})
    with pytest.raises(ValueError):
        ws.record_event("  ")


def test_latest_event_is_the_newest_of_this_task_only(controller, ws, conn):
    conn.execute("DELETE FROM events WHERE task_id = ?", (ws.task_id,))
    assert ws.latest_event() is None
    first = ws.record_event("custom.one")
    other = controller.create_task("Another task")
    assert ws.latest_event() == first
    last = ws.record_event("custom.two", {"n": 2})
    controller.workspace(other.id).record_event("custom.other")
    assert ws.latest_event() == last == ws.events()[-1]
    assert controller.workspace(other.id).latest_event().kind == "custom.other"


def test_controller_records_events_with_state_changes(controller):
    task = controller.create_task("Add CSV export", Complexity.SIMPLE)
    controller.advance(task.id)
    controller.set_complexity(task.id, Complexity.COMPLEX)
    controller.go_back(task.id, Phase.INVESTIGATION, "missed a constraint")
    events = controller.workspace(task.id).events()
    assert [(e.kind, e.payload) for e in events] == [
        ("task.created", {"complexity": "simple", "source": "developer"}),
        ("phase.changed", {"from": "investigation", "to": "interview", "reason": None}),
        ("complexity.set", {"complexity": "complex"}),
        (
            "phase.changed",
            {"from": "interview", "to": "investigation", "reason": "missed a constraint"},
        ),
    ]


def test_rejected_transition_records_no_event(controller, task, conn):
    before = _rows(conn, "events")
    with pytest.raises(InvalidTransition):
        controller.go_back(task.id, Phase.DESIGN, "forward is not back")
    assert _rows(conn, "events") == before


def test_failed_event_rolls_back_the_state_change(controller, task, conn, monkeypatch):
    def boom(self, kind, payload=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr(TaskWorkspace, "record_event", boom)
    with pytest.raises(RuntimeError):
        controller.advance(task.id)
    assert controller.get_task(task.id).phase is Phase.INVESTIGATION
    assert len(controller.history(task.id)) == 1


def test_checkpoints(controller, task, ws):
    assert ws.latest_checkpoint() is None
    first = ws.checkpoint("investigation done", {"files": 3})
    assert (first.phase, first.label, first.payload) == (
        Phase.INVESTIGATION,
        "investigation done",
        {"files": 3},
    )
    controller.advance(task.id)
    second = ws.checkpoint("interview started")
    assert (second.phase, second.payload) == (Phase.INTERVIEW, {})
    assert ws.latest_checkpoint() == second
    assert ws.checkpoints() == [first, second]
    with pytest.raises(ValueError):
        ws.checkpoint(" ")


def test_workspace_is_isolated_per_task(controller, conn):
    a = controller.workspace(controller.create_task("Task A").id)
    b = controller.workspace(controller.create_task("Task B").id)
    qa, qb = a.ask("x", "A question"), b.ask("y", "B question")
    pb = b.propose("t", "r", "why")
    b.record_event("b.only")
    b.checkpoint("b checkpoint")
    snapshot = {t: _rows(conn, t) for t in ("questions", "proposals", "events", "checkpoints")}

    assert a.questions() == [qa]
    assert a.proposals() == [] and a.checkpoints() == [] and a.latest_checkpoint() is None
    assert all(e.task_id == a.task_id for e in a.events())
    for op in (
        lambda: a.question(qb.id),
        lambda: a.answer(qb.id, "hijack"),
        lambda: a.skip(qb.id, "hijack"),
        lambda: a.reopen(qb.id),
        lambda: a.proposal(pb.id),
        lambda: a.resolve(pb.id, ProposalStatus.ACCEPTED),
        lambda: a.propose("t", "r", "why", question_id=qb.id),
    ):
        with pytest.raises(RecordNotFound):
            op()
    assert {t: _rows(conn, t) for t in snapshot} == snapshot


def test_unknown_task_and_record(conn, ws):
    with pytest.raises(TaskNotFound):
        TaskWorkspace(conn, "t-missing")
    with pytest.raises(RecordNotFound, match="unknown question 999 for task"):
        ws.answer(999, "x")


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO questions (task_id, topic, text, source, status, created_at, updated_at)"
        " VALUES ('nope', 't', 'q', 'interview', 'open', 'n', 'n')",
        "INSERT INTO proposals (task_id, title, recommendation, rationale, status, created_at)"
        " VALUES ('nope', 't', 'r', 'r', 'pending', 'n')",
        "INSERT INTO events (task_id, kind, payload, created_at) VALUES ('nope', 'k', '{}', 'n')",
        "INSERT INTO checkpoints (task_id, phase, label, payload, created_at)"
        " VALUES ('nope', 'design', 'l', '{}', 'n')",
        "INSERT INTO design_entries (task_id, section, kind, origin, binding, statement, status,"
        " created_at) VALUES ('nope', 'components', 'element', 'developer', 'fixed', 's',"
        " 'stated', 'n')",
    ],
)
def test_child_rows_require_an_existing_task(conn, sql):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)


def test_interview_question_columns(ws, conn):
    from harness.state_models import QuestionKind, SkipSource

    q = ws.ask("data_schemas", "Which schema changes?", kind="decision", catalogue_key="k.x")
    assert (q.kind, q.catalogue_key, q.asked_at, q.skip_source) == (
        QuestionKind.DECISION,
        "k.x",
        None,
        None,
    )
    first = ws.mark_asked(q.id)
    assert first.asked_at is not None
    assert ws.mark_asked(q.id).asked_at == first.asked_at
    skipped = ws.skip(q.id, "src/schema.sql", SkipSource.CODEBASE)
    assert (skipped.skip_source, skipped.skip_reason) == (SkipSource.CODEBASE, "src/schema.sql")
    reopened = ws.reopen(q.id)
    assert (reopened.skip_source, reopened.asked_at) == (None, first.asked_at)
    ws.skip(q.id, "n/a")
    assert ws.question(q.id).skip_source is SkipSource.NOT_RELEVANT
    answered = ws.answer(q.id, "none")
    assert (answered.skip_source, answered.skip_reason) == (None, None)
    decided = ws.mark_decided(q.id)
    assert (decided.status, decided.answer) == (QuestionStatus.ANSWERED, None)
    with pytest.raises(RecordNotFound):
        ws.mark_asked(999)
    with pytest.raises(ValueError):
        ws.ask("scope", "Which?", kind="bogus")


def test_latest_checkpoint_by_label(ws):
    first = ws.checkpoint("investigation", {"n": 1})
    later = ws.checkpoint("interview", {"n": 2})
    assert ws.latest_checkpoint() == later
    assert ws.latest_checkpoint("investigation") == first
    assert ws.latest_checkpoint("missing") is None


def test_design_entry_lifecycle_and_isolation(controller, conn, ws):
    from harness.design_models import Binding, EntryKind, EntryOrigin, EntryStatus

    stated = ws.state_design("components", " an Exporter ", Binding.DELEGATED)
    assert (stated.statement, stated.origin, stated.status, stated.binding) == (
        "an Exporter",
        EntryOrigin.DEVELOPER,
        EntryStatus.STATED,
        Binding.DELEGATED,
    )
    na = ws.state_design("configuration", "none", kind=EntryKind.NOT_APPLICABLE)
    pending = ws.propose_design("data_flow", "rows to csv", "streaming")
    assert (pending.status, pending.rationale, pending.resolved_at) == (
        EntryStatus.PENDING,
        "streaming",
        None,
    )
    assert ws.design_entries(EntryStatus.PENDING) == [pending]
    resolved = ws.resolve_design(pending.id, EntryStatus.ACCEPTED, "  ")
    assert resolved.response is None and resolved.resolved_at is not None
    assert ws.withdraw_design(na.id).status is EntryStatus.WITHDRAWN
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE design_entries SET section = 'bogus' WHERE id = ?", (stated.id,))

    other = controller.workspace(controller.create_task("Task B").id)
    snapshot = _rows(conn, "design_entries")
    assert other.design_entries() == []
    for op in (
        lambda: other.design_entry(stated.id),
        lambda: other.resolve_design(pending.id, EntryStatus.REJECTED),
        lambda: other.withdraw_design(stated.id),
    ):
        with pytest.raises(RecordNotFound):
            op()
    assert _rows(conn, "design_entries") == snapshot


def test_spec_versions_are_isolated_unique_and_superseded(controller, conn):
    a = controller.workspace(controller.create_task("Task A").id)
    b = controller.workspace(controller.create_task("Task B").id)
    v1 = a.record_spec_version(1, "a/v1.md", "rev1", "clean", "sha256:1")
    assert b.spec_versions() == [] and b.spec_version(1) is None
    before = _rows(conn, "spec_versions")
    with pytest.raises(ValueError, match="already recorded"):
        a.record_spec_version(1, "a/v1.md", "rev2", "clean", "sha256:2")
    with pytest.raises(ValueError, match="must not be empty"):
        a.record_spec_version(2, "a/v2.md", " ", "clean", "sha256:2")
    with pytest.raises(ValueError, match="not recorded"):
        b.mark_spec_superseded(1, 2)
    assert _rows(conn, "spec_versions") == before
    b.record_spec_version(1, "b/v1.md", "rev1", "2 uncommitted paths", "sha256:b")
    v2 = a.record_spec_version(2, "a/v2.md", "rev2", "clean", "sha256:2")
    old = a.mark_spec_superseded(1, 2)
    assert (old.status, old.superseded_by, old.content_hash) == ("superseded", 2, v1.content_hash)
    assert a.current_spec_version() == v2
    assert [v.version for v in a.spec_versions()] == [1, 2]
    assert [v.path for v in b.spec_versions()] == ["b/v1.md"]


def test_test_scenario_lifecycle_and_isolation(controller, conn, ws):
    from harness.design_models import EntryOrigin, EntryStatus

    stated = ws.state_scenario("unit", " header row ", ["Q1"], " clock ", " stable time ")
    assert (stated.statement, stated.origin, stated.covers, stated.mock) == (
        "header row",
        EntryOrigin.DEVELOPER,
        ("Q1",),
        "clock",
    )
    assert stated.mock_justification == "stable time" and stated.updated_at == stated.created_at
    pending = ws.propose_scenario("failure", "disk full", "error path", ["E1"])
    assert ws.scenarios(EntryStatus.PENDING) == [pending]
    with pytest.raises(ValueError, match="must cover at least one"):
        ws.propose_scenario("unit", "x", "why", [])
    ws.flag_scenario(stated.id, "hides missing writer")
    approval = ws.record_plan_approval("sha256:abc", 1)
    assert ws.latest_plan_approval() == approval
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE test_scenarios SET category = 'bogus' WHERE id = ?", (stated.id,))

    other = controller.workspace(controller.create_task("Task B").id)
    snapshot = {t: _rows(conn, t) for t in ("test_scenarios", "test_plan_approvals")}
    assert other.scenarios() == [] and other.latest_plan_approval() is None
    for op in (
        lambda: other.scenario(stated.id),
        lambda: other.resolve_scenario(pending.id, EntryStatus.REJECTED),
        lambda: other.edit_scenario(stated.id, statement="y"),
        lambda: other.withdraw_scenario(stated.id),
        lambda: other.flag_scenario(stated.id, "x"),
        lambda: other.clear_scenario_flag(stated.id),
    ):
        with pytest.raises(RecordNotFound):
            op()
    assert {t: _rows(conn, t) for t in snapshot} == snapshot
