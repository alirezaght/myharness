"""P2-05: the Comment Service over real SQLite — add rules, states and one transaction per write."""

import sqlite3

import pytest

from harness.comments import (
    CommentService,
    IllegalTransition,
    RequestConflict,
    StaleDiagramVersion,
    UnknownElement,
)
from harness.controller import HarnessController
from harness.db import connect, transaction
from harness.design_models import DesignSection
from harness.diagrams import DiagramService
from harness.errors import RecordNotFound, TaskNotFound
from harness.state_comments import CommentKind, CommentStatus

OPEN, ADDRESSED, RESOLVED = CommentStatus.OPEN, CommentStatus.ADDRESSED, CommentStatus.RESOLVED
MOVES = {ADDRESSED: "address", RESOLVED: "resolve", OPEN: "reopen"}
EVENTS = {ADDRESSED: "comment.addressed", RESOLVED: "comment.resolved", OPEN: "comment.reopened"}
LEGAL = {(OPEN, ADDRESSED), (ADDRESSED, RESOLVED), (ADDRESSED, OPEN), (RESOLVED, OPEN)}
A, B = "component:a", "component:b"


def _rows(conn: sqlite3.Connection, table: str) -> list[tuple]:
    return [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


def _everything(conn: sqlite3.Connection) -> tuple:
    tables = ("tasks", "events", "checkpoints", "comments", "diagram_versions")
    return tuple(tuple(_rows(conn, table)) for table in tables)


def _state(controller: HarnessController, task_id: str, text: str) -> int:
    ws = controller.workspace(task_id)
    with transaction(controller.conn):
        return ws.state_design(DesignSection.COMPONENTS, text).id


def _task(controller: HarnessController) -> tuple[str, CommentService]:
    """A task whose current diagram version 1 has components A and B and a connection."""
    task = controller.create_task("comment on diagrams")
    _state(controller, task.id, "component A\ncomponent B\nconnect A -> B: calls")
    assert DiagramService(controller, task.id).sync().version == 1
    return task.id, CommentService(controller, task.id)


def _in(service: CommentService, status: CommentStatus) -> int:
    comment, _ = service.add(1, A, CommentKind.CORRECTION, "wrong arrow")
    if status in (ADDRESSED, RESOLVED):
        service.address(comment.id)
    if status == RESOLVED:
        service.resolve(comment.id)
    assert service.ws.comment(comment.id).status == status
    return comment.id


@pytest.mark.parametrize("start", [OPEN, ADDRESSED, RESOLVED])
@pytest.mark.parametrize("target", [OPEN, ADDRESSED, RESOLVED])
def test_every_state_pair_follows_open_addressed_resolved(controller, start, target):
    _, service = _task(controller)
    comment_id = _in(service, start)
    before = _everything(controller.conn)
    before_comment = service.ws.comment(comment_id)
    move = getattr(service, MOVES[target])
    if start == target:
        assert move(comment_id) == (before_comment, False)
        assert _everything(controller.conn) == before
    elif (start, target) in LEGAL:
        moved, changed = move(comment_id)
        assert changed and moved.status == target
        same = ("id", "task_id", "diagram_version", "element_id", "kind", "text", "created_at")
        assert all(getattr(moved, f) == getattr(before_comment, f) for f in same)
        latest = service.ws.latest_event()
        assert latest.kind == EVENTS[target]
        assert latest.payload == {"id": comment_id, "from": str(start), "to": str(target)}
        assert len(service.ws.events()) == len(before[1]) + 1
    else:
        with pytest.raises(IllegalTransition, match=f"comment {comment_id} is {start}: only an"):
            move(comment_id)
        assert _everything(controller.conn) == before


def test_an_added_comment_is_tied_to_element_and_version_with_its_event(controller):
    task_id, service = _task(controller)
    tasks_before = _rows(controller.conn, "tasks")
    edge = "connection:a->b"
    comment, created = service.add(1, edge, "redesign", "  <b>split</b> this\n", "req-00000001")
    assert created and (comment.task_id, comment.diagram_version) == (task_id, 1)
    assert (comment.element_id, comment.kind, comment.status) == (edge, "redesign", "open")
    assert (comment.text, comment.request_id) == ("<b>split</b> this", "req-00000001")
    assert comment.created_at == comment.updated_at
    event = service.ws.latest_event()
    assert event.kind == "comment.added"
    assert event.payload == {"id": comment.id, "version": 1, "element": edge, "kind": "redesign"}
    assert service.ws.comments() == [comment] and service.ws.comments(OPEN) == [comment]
    assert _rows(controller.conn, "tasks") == tasks_before
    assert _rows(controller.conn, "checkpoints") == []
    assert len(_rows(controller.conn, "diagram_versions")) == 1
    other, _ = service.add(1, A, CommentKind.CLARIFICATION, "who owns A?")
    assert len(other.request_id) == 36 and other.id > comment.id


def test_a_retried_request_returns_the_same_comment_and_a_changed_one_conflicts(controller):
    task_id, service = _task(controller)
    first, created = service.add(1, A, "correction", "wrong", "req-00000001")
    again = service.add(1, A, "correction", " wrong ", "req-00000001")
    assert created and again == (first, False)
    _state(controller, task_id, "component C")
    assert service.add(1, A, "correction", "wrong", "req-00000001") == (first, False)
    before = _everything(controller.conn)
    for args in (
        (1, B, "correction", "wrong"),
        (1, A, "redesign", "wrong"),
        (1, A, "correction", "x"),
    ):
        with pytest.raises(RequestConflict, match=f"already used for comment {first.id}"):
            service.add(*args, "req-00000001")
    assert _everything(controller.conn) == before


def test_unknown_versions_and_elements_are_refused(controller):
    _, service = _task(controller)
    before = _everything(controller.conn)
    for version in (0, 2, 2**64):
        with pytest.raises(RecordNotFound, match=f"unknown diagram version {version} "):
            service.add(version, A, "correction", "x")
    with pytest.raises(UnknownElement, match="element component:zz is not in diagram version 1"):
        service.add(1, "component:zz", "correction", "x")
    with pytest.raises(ValueError):
        service.add(1, A, "praise", "x")
    with pytest.raises(ValueError, match="comment text must not be empty"):
        service.add(1, A, "correction", " \n ")
    with pytest.raises(RecordNotFound):
        service.resolve(999)
    with pytest.raises(TaskNotFound):
        CommentService(controller, "t-00000000")
    assert _everything(controller.conn) == before


def test_a_comment_on_an_outdated_version_is_refused_with_a_reload_hint(controller):
    task_id, service = _task(controller)
    _state(controller, task_id, "component Exporter")
    before = _everything(controller.conn)
    with pytest.raises(
        StaleDiagramVersion, match="the design changed since diagram version 1; rel"
    ):
        service.add(1, A, "correction", "x")
    assert _everything(controller.conn) == before
    assert DiagramService(controller, task_id).sync().version == 2
    with pytest.raises(StaleDiagramVersion) as stale:
        service.add(1, A, "correction", "x")
    assert str(stale.value) == (
        "diagram version 1 is no longer current (current: version 2);"
        " reload and comment on the current version"
    )
    assert service.add(2, "component:exporter", "correction", "x")[1] is True


def test_a_removed_element_keeps_its_comment_which_can_still_move(controller):
    """Q7c: the element is gone from the current version; its comment stays on version 1."""
    task_id, service = _task(controller)
    comment, _ = service.add(1, A, "correction", "remove A")
    ws = controller.workspace(task_id)
    with transaction(controller.conn):
        for entry in ws.design_entries():
            ws.withdraw_design(entry.id)
    _state(controller, task_id, "component B")
    assert DiagramService(controller, task_id).sync().version == 2
    with pytest.raises(UnknownElement, match="element component:a is not in diagram version 2"):
        service.add(2, A, "correction", "again")
    assert service.address(comment.id)[0].status == ADDRESSED
    assert service.resolve(comment.id)[0].status == RESOLVED
    reopened, changed = service.reopen(comment.id)
    assert changed and (reopened.status, reopened.diagram_version) == (OPEN, 1)


def test_a_failing_event_rolls_the_comment_back(controller, monkeypatch):
    _, service = _task(controller)
    comment, _ = service.add(1, A, "correction", "x")
    before = _everything(controller.conn)

    def broken(kind, payload=None):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(service.ws, "record_event", broken)
    with pytest.raises(sqlite3.OperationalError):
        service.add(1, A, "correction", "y")
    with pytest.raises(sqlite3.OperationalError):
        service.address(comment.id)
    assert _everything(controller.conn) == before and not controller.conn.in_transaction


def test_writes_refuse_an_open_transaction_and_a_held_lock_writes_nothing(controller, db_path):
    _, service = _task(controller)
    controller.conn.execute("BEGIN")
    try:
        with pytest.raises(sqlite3.OperationalError, match="outside a transaction"):
            service.add(1, A, "correction", "x")
    finally:
        controller.conn.execute("ROLLBACK")
    holder = connect(db_path)
    try:
        holder.execute("BEGIN IMMEDIATE")
        controller.conn.execute("PRAGMA busy_timeout=50")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            service.add(1, A, "correction", "x")
        holder.execute("ROLLBACK")
    finally:
        holder.close()
    assert service.ws.comments() == [] and not controller.conn.in_transaction


def test_the_schema_backs_up_the_rules(controller):
    task_id, service = _task(controller)
    comment, _ = service.add(1, A, "correction", "x", "req-00000001")
    insert = (
        "INSERT INTO comments (task_id, diagram_version, element_id, kind, text, status,"
        " request_id, created_at, updated_at) VALUES (?, ?, ?, ?, 'x', ?, ?, 'then', 'then')"
    )
    bad = [
        (task_id, 1, A, "correction", "open", "req-00000001"),
        (task_id, 9, A, "correction", "open", "req-00000002"),
        ("t-00000000", 1, A, "correction", "open", "req-00000003"),
        (task_id, 1, A, "praise", "open", "req-00000004"),
        (task_id, 1, A, "correction", "closed", "req-00000005"),
    ]
    for values in bad:
        with pytest.raises(sqlite3.IntegrityError):
            controller.conn.execute(insert, values)
    with pytest.raises(ValueError, match=f"comment {comment.id} is not addressed"):
        service.ws.set_comment_status(comment.id, ADDRESSED, RESOLVED)
    assert service.ws.comments() == [comment]
