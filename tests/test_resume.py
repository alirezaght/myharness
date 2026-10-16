import shutil

import pytest

from harness.approval import ApprovalGate
from harness.db import transaction
from harness.design import ArchitectureDesigner
from harness.design_models import DesignSection
from harness.interview import InterviewEngine
from harness.phases import Phase
from harness.resume import NOTHING_TO_RESUME, resumable_tasks, resume_point
from harness.spec_manager import SpecificationManager
from harness.spec_store import SpecStore
from tests.complete_task import FIXTURE, settle

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
TABLES = ("tasks", "questions", "proposals", "events", "checkpoints", "approvals")


def _store(repo, task_id):
    return SpecStore(repo / "docs" / "specs" / task_id)


def _point(controller, repo, task_id):
    return resume_point(controller, task_id, _store(repo, task_id), repo)


def _next(controller, repo, task_id):
    return _point(controller, repo, task_id).next_command


def _counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def _investigated(controller, request="fixture request"):
    task_id = controller.create_task(request, "trivial").id
    with transaction(controller.conn):
        controller.workspace(task_id).checkpoint("investigation", {"decisions": []})
    return task_id


def _in_approval(controller):
    task_id = _investigated(controller)
    settle(controller, task_id)
    controller.advance(task_id)
    return task_id


def test_investigation_and_interview_next_commands(controller, sample_repo):
    fresh = controller.create_task("fixture request", "trivial").id
    point = _point(controller, sample_repo, fresh)
    assert point.next_command == f"harness investigate {fresh}" and point.checkpoint is None
    task_id = _investigated(controller)
    point = _point(controller, sample_repo, task_id)
    assert point.next_command == f"harness phase {task_id} next"
    assert point.checkpoint.label == "investigation" and point.resumable

    controller.advance(task_id)
    point = _point(controller, sample_repo, task_id)
    assert (point.next_command, point.pending_question, point.checkpoint) == (
        f"harness interview next {task_id}",
        None,
        None,
    )
    assert "checkpoint: none in this phase" in point.render()

    engine = InterviewEngine(controller, task_id)
    [asked] = engine.next()
    before = _counts(controller.conn)
    point = _point(controller, sample_repo, task_id)
    assert point.pending_question == controller.workspace(task_id).question(asked.id)
    assert point.next_command == f"harness interview next {task_id}"
    assert point.checkpoint.label == "interview" and point.checkpoint.phase is Phase.INTERVIEW
    assert engine.upcoming().id == asked.id
    assert _counts(controller.conn) == before

    while (question := engine.upcoming()) is not None:
        engine.unknown(question.id)
    point = _point(controller, sample_repo, task_id)
    assert point.pending_question is None
    assert point.next_command == f"harness interview checklist {task_id}"


def test_design_test_plan_and_final_review_next_commands(controller, sample_repo):
    task_id = _investigated(controller)
    controller.advance(task_id)
    settle(controller, task_id)
    assert _next(controller, sample_repo, task_id) == f"harness phase {task_id} next"

    controller.go_back(task_id, Phase.DESIGN, FIXTURE)
    assert _next(controller, sample_repo, task_id) == f"harness phase {task_id} next"
    designer = ArchitectureDesigner(controller, task_id)
    [entry] = [e for e in designer.document().entries if e.section is DesignSection.COMPONENTS]
    designer.withdraw(entry.id)
    assert _next(controller, sample_repo, task_id) == f"harness design coverage {task_id}"
    designer.none(DesignSection.COMPONENTS, FIXTURE)
    controller.advance(task_id)
    assert _next(controller, sample_repo, task_id) == f"harness phase {task_id} next"

    controller.go_back(task_id, Phase.INTERVIEW, FIXTURE)
    InterviewEngine(controller, task_id).ask("scope", "fixture follow-up")
    controller.advance(task_id)
    controller.advance(task_id)
    controller.advance(task_id)
    assert controller.get_task(task_id).phase is Phase.FINAL_REVIEW
    assert _next(controller, sample_repo, task_id) == f"harness review {task_id}"


@needs_git
def test_approval_current_stale_and_blocked(controller, sample_repo):
    task_id = _in_approval(controller)
    point = _point(controller, sample_repo, task_id)
    assert (point.approval, point.next_command) == ("none", f"harness approve {task_id}")
    assert point.render().endswith(f"approval: none\nnext: harness approve {task_id}")

    ApprovalGate(controller, task_id, _store(sample_repo, task_id), sample_repo).approve()
    point = _point(controller, sample_repo, task_id)
    assert point.approval.endswith("(current)") and not point.resumable
    assert point.next_command is None and point.render().endswith(NOTHING_TO_RESUME)
    assert point.checkpoint.label == "approval"
    assert point.to_dict()["next"] is None and point.to_dict()["resumable"] is False

    controller.go_back(task_id, Phase.FINAL_REVIEW, FIXTURE)
    controller.advance(task_id)
    point = _point(controller, sample_repo, task_id)
    assert "(stale: " in point.approval and point.resumable
    assert point.next_command == f"harness approve {task_id}"

    with transaction(controller.conn):
        controller.workspace(task_id).propose_change("DEC-0001", 1, "T", FIXTURE, FIXTURE)
    assert _next(controller, sample_repo, task_id) == f"harness review {task_id}"


@needs_git
def test_resumable_tasks_skip_approved_and_list_newest_first(controller, sample_repo):
    done = _in_approval(controller)
    ApprovalGate(controller, done, _store(sample_repo, done), sample_repo).approve()
    older, newer = _investigated(controller, "older"), _investigated(controller, "newer")
    stamps = {done: "2026-01-03T00:00:00+00:00", older: "2026-01-01T00:00:00+00:00"}
    stamps[newer] = "2026-01-02T00:00:00+00:00"
    with transaction(controller.conn):
        for task_id, stamp in stamps.items():
            controller.conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?", (stamp, task_id)
            )
    before = _counts(controller.conn)
    points = resumable_tasks(controller, lambda t: _store(sample_repo, t), lambda _t: sample_repo)
    assert [p.task.id for p in points] == [newer, older]
    assert _counts(controller.conn) == before


@needs_git
def test_an_unrecorded_draft_is_reported_and_a_recorded_one_is_not(controller, sample_repo):
    task_id = _investigated(controller)
    store = _store(sample_repo, task_id)
    assert _point(controller, sample_repo, task_id).notes == ()
    store.write_draft("interrupted draft\n")
    [note] = _point(controller, sample_repo, task_id).notes
    assert note.startswith(f"unrecorded draft {store.draft_path}")
    assert f"harness spec draft {task_id} rewrites it" in note
    assert f"note: {note}" in _point(controller, sample_repo, task_id).render()

    SpecificationManager(controller, task_id, store, sample_repo).draft()
    assert _point(controller, sample_repo, task_id).notes == ()
