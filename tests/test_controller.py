import sqlite3

import pytest

from harness.classification import Complexity
from harness.controller import HarnessController, TaskNotFound
from harness.db import apply_migrations, connect
from harness.investigation import Assumption, Fact, FactCategory, InvestigationReport, OpenQuestion
from harness.phases import PHASE_ORDER, InvalidTransition, Phase
from harness.state import TaskWorkspace
from harness.state_models import QuestionSource
from tests.complete_task import settle


def _row_counts(conn):
    tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    transitions = conn.execute("SELECT COUNT(*) FROM phase_transitions").fetchone()[0]
    return tasks, transitions


def test_create_task_starts_in_investigation_with_initial_history(controller):
    task = controller.create_task("  Add CSV export  ")
    assert task.id.startswith("t-") and len(task.id) == 10
    assert task.request == "Add CSV export"
    assert task.phase is Phase.INVESTIGATION
    assert (task.complexity, task.complexity_source) == (Complexity.STANDARD, "proposed")
    [first] = controller.history(task.id)
    assert (first.from_phase, first.to_phase, first.reason) == (
        None,
        Phase.INVESTIGATION,
        "task created",
    )


def test_developer_complexity_is_recorded_as_developer(controller):
    task = controller.create_task("Fix a typo", Complexity.COMPLEX)
    assert (task.complexity, task.complexity_source) == (Complexity.COMPLEX, "developer")


@pytest.mark.parametrize("request_text", ["", "   \n"])
def test_empty_request_is_rejected_without_writing(controller, conn, request_text):
    with pytest.raises(ValueError):
        controller.create_task(request_text)
    assert _row_counts(conn) == (0, 0)


def test_step_through_every_phase_and_reject_past_approval(controller, conn):
    task = controller.create_task("Add CSV export")
    task = settle(controller, task.id)  # steps investigation..final_review, review passing
    assert task.phase is Phase.FINAL_REVIEW
    task = controller.advance(task.id)
    assert task.phase is Phase.APPROVAL
    before = _row_counts(conn)
    with pytest.raises(InvalidTransition):
        controller.advance(task.id)
    assert _row_counts(conn) == before
    assert [(t.from_phase, t.to_phase) for t in controller.history(task.id)] == list(
        zip((None, *PHASE_ORDER[:-1]), PHASE_ORDER, strict=True)
    )


def test_go_back_to_any_earlier_phase_records_reason(controller):
    task = controller.create_task("Add CSV export")
    settle(controller, task.id)
    controller.advance(task.id)  # final_review -> approval
    task = controller.go_back(task.id, Phase.INTERVIEW, "  design needs more detail ")
    assert task.phase is Phase.INTERVIEW
    last = controller.history(task.id)[-1]
    assert (last.from_phase, last.to_phase, last.reason) == (
        Phase.APPROVAL,
        Phase.INTERVIEW,
        "design needs more detail",
    )
    assert controller.advance(task.id).phase is Phase.DESIGN


@pytest.mark.parametrize("target", [Phase.DESIGN, Phase.TEST_PLAN, Phase.APPROVAL])
def test_go_back_to_current_or_later_phase_is_rejected_without_writing(controller, conn, target):
    task = controller.create_task("Add CSV export")
    controller.advance(task.id)
    controller.advance(task.id)
    before = _row_counts(conn)
    with pytest.raises(InvalidTransition):
        controller.go_back(task.id, target, "reason")
    assert _row_counts(conn) == before
    assert controller.get_task(task.id).phase is Phase.DESIGN


@pytest.mark.parametrize("reason", ["", "  ", None])
def test_go_back_requires_a_reason(controller, conn, reason):
    task = controller.create_task("Add CSV export")
    controller.advance(task.id)
    before = _row_counts(conn)
    with pytest.raises(ValueError):
        controller.go_back(task.id, Phase.INVESTIGATION, reason)
    assert _row_counts(conn) == before


def test_unknown_task_raises_task_not_found(controller):
    for call in (
        lambda: controller.get_task("t-missing"),
        lambda: controller.advance("t-missing"),
        lambda: controller.go_back("t-missing", Phase.INVESTIGATION, "x"),
        lambda: controller.set_complexity("t-missing", Complexity.SIMPLE),
        lambda: controller.history("t-missing"),
    ):
        with pytest.raises(TaskNotFound):
            call()


def test_set_complexity_marks_developer_override(controller):
    task = controller.create_task("Fix a typo in the README")
    assert task.complexity_source == "proposed"
    task = controller.set_complexity(task.id, Complexity.STANDARD)
    assert (task.complexity, task.complexity_source) == (Complexity.STANDARD, "developer")


def test_state_survives_reconnect(db_path):
    first = connect(db_path)
    task = HarnessController(first).create_task("Add CSV export")
    HarnessController(first).advance(task.id)
    first.close()
    second = connect(db_path)
    try:
        controller = HarnessController(second)
        assert controller.get_task(task.id).phase is Phase.INTERVIEW
        assert len(controller.history(task.id)) == 2
        assert [t.id for t in controller.list_tasks()] == [task.id]
    finally:
        second.close()


def test_two_connections_cannot_double_advance(db_path):
    a, b = connect(db_path), connect(db_path)
    try:
        task = HarnessController(a).create_task("Add CSV export")
        HarnessController(a).advance(task.id)
        HarnessController(b).advance(task.id)
        assert HarnessController(a).get_task(task.id).phase is Phase.DESIGN
        assert len(HarnessController(b).history(task.id)) == 3
    finally:
        a.close()
        b.close()


def test_migrations_are_tracked_and_idempotent(conn):
    assert apply_migrations(conn) == []
    rows = conn.execute("SELECT version, name FROM schema_migrations").fetchall()
    assert [tuple(r) for r in rows] == [
        (1, "0001_tasks.sql"),
        (2, "0002_workspace.sql"),
        (3, "0003_interview.sql"),
        (4, "0004_design.sql"),
        (5, "0005_decision_changes.sql"),
        (6, "0006_spec_versions.sql"),
        (7, "0007_test_plan.sql"),
        (8, "0008_approvals.sql"),
        (9, "0009_diagram_versions.sql"),
    ]
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_fresh_connect_reports_applied_migration(tmp_path):
    raw = sqlite3.connect(tmp_path / "raw.db", autocommit=True)
    try:
        assert apply_migrations(raw) == [
            "0001_tasks.sql",
            "0002_workspace.sql",
            "0003_interview.sql",
            "0004_design.sql",
            "0005_decision_changes.sql",
            "0006_spec_versions.sql",
            "0007_test_plan.sql",
            "0008_approvals.sql",
            "0009_diagram_versions.sql",
        ]
        assert apply_migrations(raw) == []
    finally:
        raw.close()


def test_transition_rows_require_an_existing_task(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO phase_transitions (task_id, to_phase, created_at)"
            " VALUES ('nope', 'x', 'y')"
        )


def _report(questions):
    return InvestigationReport(
        repo_root="/repo",
        revision="abc",
        request="add csv export",
        decisions=(),
        facts=(Fact(FactCategory.FILES, "`a.py` matches", "keyword match: csv"),),
        assumptions=(Assumption("x", "y"),),
        questions=tuple(OpenQuestion(topic, text) for topic, text in questions),
    )


def test_record_investigation_persists_questions_checkpoint_and_event(controller):
    task = controller.create_task("Add CSV export")
    report = _report([("scope", "Which package?"), ("tests", "No tests found?")])
    checkpoint = controller.record_investigation(task.id, report)
    workspace = controller.workspace(task.id)
    questions = workspace.open_questions()
    assert [(q.topic, q.text) for q in questions] == [
        ("scope", "Which package?"),
        ("tests", "No tests found?"),
    ]
    assert all(q.source is QuestionSource.INVESTIGATION for q in questions)
    assert (checkpoint.label, checkpoint.payload) == ("investigation", report.to_dict())
    assert checkpoint.phase is Phase.INVESTIGATION
    [event] = [e for e in workspace.events() if e.kind == "investigation.completed"]
    assert event.payload["new_questions"] == 2
    assert event.payload["facts"] == 1


def test_rerunning_investigation_adds_no_duplicate_questions(controller):
    task = controller.create_task("Add CSV export")
    controller.record_investigation(task.id, _report([("scope", "Which package?")]))
    workspace = controller.workspace(task.id)
    workspace.answer(workspace.open_questions()[0].id, "the root one")
    controller.record_investigation(
        task.id, _report([("scope", "Which package?"), ("tests", "New?")])
    )
    assert [(q.topic, q.text) for q in workspace.questions()] == [
        ("scope", "Which package?"),
        ("tests", "New?"),
    ]
    assert len([c for c in workspace.checkpoints() if c.label == "investigation"]) == 2
    events = [e for e in workspace.events() if e.kind == "investigation.completed"]
    assert [e.payload["new_questions"] for e in events] == [1, 1]


def test_record_investigation_rolls_back_on_failure(controller, monkeypatch):
    task = controller.create_task("Add CSV export")

    def broken(self, label, payload=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr(TaskWorkspace, "checkpoint", broken)
    with pytest.raises(RuntimeError):
        controller.record_investigation(task.id, _report([("scope", "Which package?")]))
    workspace = controller.workspace(task.id)
    assert workspace.questions() == []
    assert [e.kind for e in workspace.events()] == ["task.created"]


def test_record_investigation_unknown_task(controller):
    with pytest.raises(TaskNotFound):
        controller.record_investigation("t-deadbeef", _report([]))
