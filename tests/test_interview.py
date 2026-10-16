import json
from pathlib import Path

import pytest

from harness.classification import INTERVIEW_TOPICS, Complexity, interview_depth
from harness.decisions import DecisionRecord, DecisionStatus
from harness.interview import DEPTH_SKIP_REASON, InterviewEngine, InterviewError
from harness.interview_catalogue import CATALOGUE, catalogue_for
from harness.interview_report import ChecklistState, Provenance
from harness.investigation import InvestigationReport, OpenQuestion
from harness.state import TaskWorkspace
from harness.state_models import ProposalStatus, QuestionSource, QuestionStatus, SkipSource

TABLES = ("questions", "proposals", "events", "checkpoints", "tasks")


def _decision(dec_id: str, status: DecisionStatus) -> DecisionRecord:
    return DecisionRecord(
        dec_id, "Storage", status, 1, "2026-01-01", "Persistence", None, "d", "r", "i", Path("x")
    )


def _report(questions=()) -> InvestigationReport:
    return InvestigationReport(
        repo_root="/repo",
        revision="abc",
        request="add csv export",
        decisions=(
            _decision("DEC-0001", DecisionStatus.APPROVED),
            _decision("DEC-0002", DecisionStatus.SUPERSEDED),
        ),
        facts=(),
        assumptions=(),
        questions=tuple(OpenQuestion(topic, text) for topic, text in questions),
    )


def _snapshot(conn):
    return {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in TABLES}


def _start(controller, complexity=Complexity.TRIVIAL, questions=(), advance=True):
    task = controller.create_task("Add CSV export", complexity)
    controller.record_investigation(task.id, _report(questions))
    if advance:
        controller.advance(task.id)
    return task, InterviewEngine(controller, task.id)


def test_catalogue_covers_every_topic_with_unique_keys():
    assert {entry.topic for entry in CATALOGUE} == set(INTERVIEW_TOPICS)
    keys = [entry.key for entry in CATALOGUE]
    assert len(keys) == len(set(keys))
    assert all(entry.key.startswith(f"{entry.topic}.") for entry in CATALOGUE)
    trivial = catalogue_for(interview_depth(Complexity.TRIVIAL).topics)
    complex_ = catalogue_for(interview_depth(Complexity.COMPLEX).topics)
    assert set(trivial) < set(complex_) and complex_ == CATALOGUE
    assert {e.topic for e in trivial} == set(interview_depth(Complexity.TRIVIAL).topics)
    assert [INTERVIEW_TOPICS.index(e.topic) for e in CATALOGUE] == sorted(
        INTERVIEW_TOPICS.index(e.topic) for e in CATALOGUE
    )


def test_depth_seeds_skips_and_reopens_catalogue_questions(controller):
    task, engine = _start(controller)
    engine.next()
    ws = engine.ws
    trivial_topics = set(interview_depth(Complexity.TRIVIAL).topics)
    assert {q.topic for q in ws.questions()} == trivial_topics

    controller.set_complexity(task.id, Complexity.COMPLEX)
    [asked] = engine.next()
    assert asked.topic == "business_requirements"
    assert {q.catalogue_key for q in ws.questions()} == {e.key for e in CATALOGUE}
    security = next(q for q in ws.questions() if q.topic == "security")
    engine.unknown(security.id)

    controller.set_complexity(task.id, Complexity.TRIVIAL)
    engine.next()
    kept = {security.id, asked.id}
    extras = [q for q in ws.questions() if q.topic not in trivial_topics and q.id not in kept]
    assert extras and all(q.status is QuestionStatus.SKIPPED for q in extras)
    assert all(q.skip_source is SkipSource.NOT_RELEVANT for q in extras)
    assert {q.skip_reason for q in extras} == {f"{DEPTH_SKIP_REASON} for trivial"}
    assert all(ws.question(qid).status is QuestionStatus.OPEN for qid in kept)

    controller.set_complexity(task.id, Complexity.COMPLEX)
    engine.next()
    assert all(ws.question(q.id).status is QuestionStatus.OPEN for q in extras)
    assert len(ws.questions()) == len(CATALOGUE)


def test_order_resume_determinism_and_groups(controller):
    _, engine = _start(controller, questions=[("tests", "Which framework?")])
    [first] = engine.next()
    assert (first.source, first.text) == (QuestionSource.INVESTIGATION, "Which framework?")
    [again] = engine.next()
    assert again == first and again.asked_at is not None
    engine.answer(first.id, "pytest")
    group = engine.next(group=True)
    assert [q.catalogue_key for q in group] == [
        "functional_behavior.success_path",
        "functional_behavior.existing",
    ]
    assert all(q.asked_at for q in group)
    assert engine.ws.latest_checkpoint("interview").payload["next_question_id"] == group[0].id


def _scripted(controller):
    """The Done-when script: every kind of resolved and unresolved item."""
    _, engine = _start(
        controller,
        questions=[("tests", "Which framework?"), ("config", "Which env var?"), ("scope", "Pkg?")],
    )
    ws = engine.ws
    engine.next()
    by_key = {q.catalogue_key: q.id for q in ws.questions()}
    engine.answer(1, "pytest")
    engine.unknown(2)
    rejected = engine.propose(3, "Root", "Use the root package", "Only one package exists")
    engine.decide(rejected.id, ProposalStatus.REJECTED, None)
    engine.skip(by_key["functional_behavior.success_path"], SkipSource.DECISION, "DEC-0001")
    engine.skip(by_key["acceptance_criteria.list"], SkipSource.CODEBASE, "tests/test_export.py")
    awaiting = engine.propose(
        by_key["functional_behavior.existing"], "Keep", "Keep the old export", "Callers rely on it"
    )
    accepted_q = engine.ask("security", "Who may export?", decision=True)
    accepted = engine.propose(accepted_q.id, "Admins", "Only admins export", "Least privilege")
    engine.decide(accepted.id, ProposalStatus.ACCEPTED, None)
    modified_q = engine.ask("data", "Which columns?")
    modified = engine.propose(modified_q.id, "All", "Export all columns", "Simplest")
    engine.decide(modified.id, ProposalStatus.MODIFIED, "only id and name")
    orphan = ws.propose("Encoding", "Use UTF-8", "Portable")
    return engine, by_key, {"rejected": rejected, "awaiting": awaiting, "orphan": orphan}


def test_checklist_lists_exactly_the_unresolved_items(controller):
    engine, by_key, p = _scripted(controller)
    checklist = engine.checklist()
    assert {(item.ref, item.state) for item in checklist.items} == {
        ("Q2", ChecklistState.UNANSWERED),
        ("Q3", ChecklistState.RECOMMENDATION_REJECTED),
        (f"Q{by_key['functional_behavior.existing']}", ChecklistState.AWAITING_DECISION),
        (f"Q{by_key['acceptance_criteria.verification']}", ChecklistState.NOT_ASKED),
        (f"P{p['orphan'].id}", ChecklistState.AWAITING_DECISION),
    }
    awaiting = next(i for i in checklist.items if i.state is ChecklistState.AWAITING_DECISION)
    assert awaiting.proposal_id == p["awaiting"].id
    assert checklist.blocking and checklist.to_dict()["blocking"] is True
    assert (checklist.answered, checklist.decided, checklist.skipped) == (1, 2, 2)

    engine.decide(p["orphan"].id, ProposalStatus.ACCEPTED, None)
    engine.decide(p["awaiting"].id, ProposalStatus.ACCEPTED, None)
    for qid in (2, 3, by_key["acceptance_criteria.verification"]):
        engine.answer(qid, "decided by the developer")
    assert engine.checklist().items == () and not engine.checklist().blocking
    stored = engine.ws.latest_checkpoint("interview").payload
    assert stored == {"next_question_id": None, "unresolved": [], "blocking": False}


def test_summary_contains_only_developer_provided_or_approved_requirements(controller):
    engine, _, p = _scripted(controller)
    summary = engine.summary()
    assert {(r.question_id, r.statement, r.provenance) for r in summary.requirements} == {
        (1, "pytest", Provenance.DEVELOPER),
        (8, "Only admins export", Provenance.ACCEPTED_RECOMMENDATION),
        (9, "only id and name", Provenance.MODIFIED_RECOMMENDATION),
    }
    requirements = json.dumps(summary.to_dict()["requirements"])
    for proposal in p.values():
        assert proposal.recommendation not in requirements
    assert "Export all columns" not in requirements
    assert {s.skip_source for s in summary.answered_elsewhere} == {"decision", "codebase"}
    assert summary.not_relevant == ()
    q2 = engine.ws.question(2)
    assert (q2.status, q2.answer) == (QuestionStatus.OPEN, None)
    assert engine.ws.question(8).answer is None


@pytest.mark.parametrize("text", ["I don't know", "  idk ", "Not sure.", "?", "", "I DON’T KNOW"])
def test_non_answers_are_rejected_and_change_nothing(controller, conn, text):
    _, engine = _start(controller)
    [question] = engine.next()
    before = _snapshot(conn)
    with pytest.raises(InterviewError, match="harness interview unknown"):
        engine.answer(question.id, text)
    assert _snapshot(conn) == before


def test_read_only_views_write_nothing(controller, conn):
    _, engine = _start(controller)
    before = _snapshot(conn)
    checklist = engine.checklist()
    engine.summary().render()
    assert _snapshot(conn) == before
    assert {i.state for i in checklist.items} == {ChecklistState.NOT_ASKED}
    assert len(checklist.items) == len(catalogue_for(interview_depth(Complexity.TRIVIAL).topics))


def test_mutations_require_investigation_then_interview_phase(controller, conn):
    task = controller.create_task("Add CSV export", Complexity.TRIVIAL)
    engine = InterviewEngine(controller, task.id)
    before = _snapshot(conn)
    with pytest.raises(InterviewError, match="investigate"):
        engine.next()
    assert _snapshot(conn) == before
    controller.record_investigation(task.id, _report())
    before = _snapshot(conn)
    with pytest.raises(InterviewError, match=f"harness phase {task.id} next"):
        engine.next()
    assert _snapshot(conn) == before
    controller.advance(task.id)
    controller.advance(task.id)
    before = _snapshot(conn)
    with pytest.raises(InterviewError, match="back interview --reason"):
        engine.ask("scope", "Anything else?")
    assert _snapshot(conn) == before


def test_skip_provenance_and_open_only_guards(controller):
    _, engine = _start(controller)
    [q] = engine.next()
    for ref in ("DEC-9999", "DEC-0002", ""):
        with pytest.raises(InterviewError, match="approved: DEC-0001"):
            engine.skip(q.id, SkipSource.DECISION, ref)
    with pytest.raises(ValueError):
        engine.skip(q.id, SkipSource.CODEBASE, "  ")
    skipped = engine.skip(q.id, SkipSource.DECISION, "DEC-0001")
    assert (skipped.skip_source, skipped.skip_reason) == (SkipSource.DECISION, "DEC-0001")
    for call in (
        lambda: engine.unknown(q.id),
        lambda: engine.propose(q.id, "t", "r", "why"),
        lambda: engine.skip(q.id, SkipSource.CODEBASE, "x"),
    ):
        with pytest.raises(InterviewError, match="already skipped"):
            call()


def test_decide_twice_and_modify_without_response_fail(controller):
    _, engine = _start(controller)
    [q] = engine.next()
    proposal = engine.propose(q.id, "t", "rec", "why")
    with pytest.raises(ValueError, match="modified proposal response"):
        engine.decide(proposal.id, ProposalStatus.MODIFIED, None)
    engine.decide(proposal.id, ProposalStatus.REJECTED, None)
    with pytest.raises(ValueError, match="already rejected"):
        engine.decide(proposal.id, ProposalStatus.ACCEPTED, None)


def test_failing_event_rolls_back_the_whole_command(controller, conn, monkeypatch):
    _, engine = _start(controller)
    [q] = engine.next()
    before = _snapshot(conn)

    def broken(self, kind, payload=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr(TaskWorkspace, "record_event", broken)
    with pytest.raises(RuntimeError):
        engine.answer(q.id, "exports a CSV file")
    assert _snapshot(conn) == before


def test_deferred_questions_are_passed_over_but_stay_unresolved(controller):
    _, engine = _start(controller)
    asked = []
    while batch := engine.next():
        [question] = batch
        assert question.id not in asked
        asked.append(question.id)
        engine.unknown(question.id)
    assert len(asked) == len(catalogue_for(interview_depth(Complexity.TRIVIAL).topics))
    items = engine.checklist().items
    assert [i.question_id for i in items] == asked
    assert {i.state for i in items} == {ChecklistState.UNANSWERED}
    engine.answer(asked[0], "a CSV file per export")
    assert engine.ws.latest_checkpoint("interview").payload["next_question_id"] is None
