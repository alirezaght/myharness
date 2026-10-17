import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from harness.approval import GRANTED_EVENT, ApprovalGate, NotApproved, require_approval
from harness.db import transaction
from harness.phases import Phase
from harness.review import FinalReviewer
from harness.review_report import ReviewReport
from harness.spec_manager import SpecificationManager
from harness.spec_record import parse_spec
from harness.spec_store import SpecStore
from harness.state import TaskWorkspace
from tests.complete_task import settle

HARNESS = Path(__file__).resolve().parents[1] / "harness"
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _store(repo, task_id):
    return SpecStore(repo / "docs" / "specs" / task_id)


def _gate(controller, repo, task_id):
    return ApprovalGate(controller, task_id, _store(repo, task_id), repo)


def _require(controller, repo, task_id):
    return require_approval(controller, task_id, _store(repo, task_id), repo)


def _in_approval(controller):
    task = controller.create_task("fixture request", "trivial")
    settle(controller, task.id)
    controller.advance(task.id)
    assert controller.get_task(task.id).phase is Phase.APPROVAL
    return task.id


def _refused(controller, repo, task_id, fragment):
    with pytest.raises(NotApproved) as caught:
        _require(controller, repo, task_id)
    assert caught.value.task_id == task_id and fragment in caught.value.reason
    assert str(caught.value).startswith(f"task {task_id} is not approved for implementation: ")
    return caught.value


def _counts(conn):
    tables = ("approvals", "spec_versions", "events", "checkpoints")
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


@needs_git
def test_nothing_can_be_handed_off_without_an_approval_record(controller, sample_repo):
    fresh = controller.create_task("fixture request", "trivial").id
    _refused(controller, sample_repo, fresh, f"no approval record; run: harness approve {fresh}")
    task_id = _in_approval(controller)
    _refused(controller, sample_repo, task_id, "no approval record")
    record, _ = SpecificationManager(
        controller, task_id, _store(sample_repo, task_id), sample_repo
    ).approve()
    assert record.version == 1 and controller.workspace(task_id).spec_versions()
    _refused(controller, sample_repo, task_id, "no approval record")
    assert _gate(controller, sample_repo, task_id).status() == "none"
    assert controller.workspace(task_id).approvals() == []


@needs_git
def test_approval_seals_a_snapshot_and_records_an_immutable_bound_record(controller, sample_repo):
    task_id = _in_approval(controller)
    ws = controller.workspace(task_id)
    approval, record, previous = _gate(controller, sample_repo, task_id).approve()
    assert previous is None and record.version == 1
    row = ws.spec_version(1)
    assert (approval.spec_version, approval.path, approval.revision, approval.content_hash) == (
        1,
        str(record.path),
        record.revision,
        record.content_hash,
    )
    assert (row.path, row.revision, row.content_hash) == (
        approval.path,
        approval.revision,
        approval.content_hash,
    )
    assert approval.review_state_hash == ws.latest_checkpoint("final_review").payload["state_hash"]
    event, checkpoint = ws.events()[-1], ws.latest_checkpoint()
    assert event.kind == GRANTED_EVENT and event.payload["approval_id"] == approval.id
    assert checkpoint.label == "approval" and checkpoint.phase == "approval"
    assert checkpoint.payload == event.payload
    assert _require(controller, sample_repo, task_id) == approval
    status = _gate(controller, sample_repo, task_id).status()
    assert status == f"spec v1 {approval.content_hash} (current)"

    second, record2, previous2 = _gate(controller, sample_repo, task_id).approve()
    assert (record2.version, previous2.version, second.spec_version) == (2, 1, 2)
    assert parse_spec(record.path).status == "superseded"
    assert ws.approvals() == [approval, second]
    assert _require(controller, sample_repo, task_id) == second

    with transaction(controller.conn), pytest.raises(ValueError, match="already recorded"):
        ws.record_approval(1, approval.path, "r", "sha256:x", "sha256:y")
    with transaction(controller.conn), pytest.raises(ValueError, match="not bound"):
        ws.record_approval(9, approval.path, "r", "sha256:x", "sha256:y")
    assert ws.approvals() == [approval, second]
    sources = [p.read_text() for p in HARNESS.rglob("*.py")]
    sources += [p.read_text() for p in (HARNESS / "migrations").glob("*.sql")]
    assert not [s for s in sources if re.search(r"(UPDATE|DELETE FROM)\s+approvals", s, re.I)]


def _phase_back(controller, repo, task_id):
    controller.go_back(task_id, Phase.TEST_PLAN, "fixture")


def _phase_round_trip(controller, repo, task_id):
    controller.go_back(task_id, Phase.TEST_PLAN, "fixture")
    controller.advance(task_id)
    controller.advance(task_id)
    assert controller.get_task(task_id).phase is Phase.APPROVAL


def _bare_spec_approve(controller, repo, task_id):
    SpecificationManager(controller, task_id, _store(repo, task_id), repo).approve()


def _tamper(controller, repo, task_id):
    path = _store(repo, task_id).version_path(1)
    path.write_bytes(path.read_bytes().replace(b"fixture", b"altered"))


def _delete(controller, repo, task_id):
    _store(repo, task_id).version_path(1).unlink()


def _content_change(controller, repo, task_id):
    decision = {"id": "DEC-0009", "version": 1, "title": "Fixture", "status": "approved"}
    with transaction(controller.conn):
        controller.workspace(task_id).checkpoint("investigation", {"decisions": [decision]})


def _pending_change(controller, repo, task_id):
    with transaction(controller.conn):
        controller.workspace(task_id).propose_change("DEC-0001", 1, "T", "fixture", "fixture")


@needs_git
@pytest.mark.parametrize(
    "invalidate, fragment",
    [
        (_phase_back, "the task is in phase test_plan, not approval"),
        (_phase_round_trip, "left the approval phase after the approval was granted"),
        (_bare_spec_approve, "spec v1 was superseded by v2"),
        (_tamper, "was modified after approval (run: harness spec verify"),
        (_delete, "cannot be read"),
        (_content_change, "the specification content changed since approval"),
        (_pending_change, "the final design review state changed since approval"),
    ],
)
def test_every_invalidation_makes_the_approval_stale(controller, sample_repo, invalidate, fragment):
    task_id = _in_approval(controller)
    approval, _, _ = _gate(controller, sample_repo, task_id).approve()
    invalidate(controller, sample_repo, task_id)
    error = _refused(controller, sample_repo, task_id, fragment)
    status = _gate(controller, sample_repo, task_id).status()
    assert status == f"spec v1 {approval.content_hash} (stale: {error.reason})"
    assert controller.workspace(task_id).approvals() == [approval]


@needs_git
def test_a_failing_record_insert_leaves_nothing_behind(controller, sample_repo, monkeypatch):
    task_id = _in_approval(controller)
    store = _store(sample_repo, task_id)
    SpecificationManager(controller, task_id, store, sample_repo).draft()
    files = {p.name: p.read_bytes() for p in store.directory.iterdir()}
    before = _counts(controller.conn)

    def failing(self, *args):
        raise sqlite3.IntegrityError("injected")

    monkeypatch.setattr(TaskWorkspace, "record_approval", failing)
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        _gate(controller, sample_repo, task_id).approve()
    assert {p.name: p.read_bytes() for p in store.directory.iterdir()} == files
    assert _counts(controller.conn) == before

    monkeypatch.undo()
    original = TaskWorkspace.checkpoint

    def failing_checkpoint(self, label, payload=None):
        if label == "approval":
            raise OSError("disk full")
        return original(self, label, payload)

    monkeypatch.setattr(TaskWorkspace, "checkpoint", failing_checkpoint)
    with pytest.raises(OSError, match="disk full"):
        _gate(controller, sample_repo, task_id).approve()
    assert {p.name: p.read_bytes() for p in store.directory.iterdir()} == files
    assert _counts(controller.conn) == before


@needs_git
def test_approve_outside_approval_or_while_blocked_writes_nothing(controller, sample_repo):
    task_id = controller.create_task("fixture request", "trivial").id
    before = _counts(controller.conn)
    with pytest.raises(ValueError, match="is in phase investigation"):
        _gate(controller, sample_repo, task_id).approve()
    assert _counts(controller.conn) == before
    task_id = _in_approval(controller)
    _pending_change(controller, sample_repo, task_id)
    before = _counts(controller.conn)
    with pytest.raises(ValueError, match="final design review blocks approval"):
        _gate(controller, sample_repo, task_id).approve()
    assert _counts(controller.conn) == before
    assert not _store(sample_repo, task_id).directory.exists()


@needs_git
def test_a_raising_guard_sees_the_review_and_leaves_nothing_behind(controller, sample_repo):
    task_id = _in_approval(controller)
    store = _store(sample_repo, task_id)
    SpecificationManager(controller, task_id, store, sample_repo).draft()
    expected = FinalReviewer(controller, task_id).report().state_hash
    seen = []

    def raising(report):
        seen.append(report)
        raise RuntimeError("guard")

    def snapshot():
        return {p.name: p.read_bytes() for p in store.directory.iterdir()}, _counts(controller.conn)

    before = snapshot()
    with pytest.raises(RuntimeError, match="guard"):
        _gate(controller, sample_repo, task_id).approve(guard=raising)
    assert isinstance(seen[0], ReviewReport) and seen[0].state_hash == expected
    assert snapshot() == before and not controller.conn.in_transaction

    approval, record, _ = _gate(controller, sample_repo, task_id).approve(guard=lambda r: None)
    assert record.version == 1 and approval.spec_version == 1
    sealed = snapshot()
    with pytest.raises(RuntimeError, match="guard"):
        _gate(controller, sample_repo, task_id).approve(guard=raising)
    assert snapshot() == sealed and parse_spec(record.path).status == "approved"
    assert _require(controller, sample_repo, task_id) == approval
