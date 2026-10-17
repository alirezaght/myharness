"""P2-04: the Diagram Service over real SQLite — versions, race-safe sync, nothing else written."""

import hashlib
import sqlite3

import pytest

from harness.controller import HarnessController
from harness.db import connect, transaction
from harness.design_models import DesignSection
from harness.diagrams import DiagramService
from harness.errors import RecordNotFound, TaskNotFound
from tests.sample_design import TYPES, add_sample_design


def _rows(conn: sqlite3.Connection, table: str) -> list[tuple]:
    return [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


def _state(conn: sqlite3.Connection) -> tuple:
    tasks = [tuple(r) for r in conn.execute("SELECT * FROM tasks ORDER BY id")]
    return tasks, _rows(conn, "events"), _rows(conn, "checkpoints")


def _state_design(controller: HarnessController, task_id: str, text: str) -> int:
    ws = controller.workspace(task_id)
    with transaction(controller.conn):
        return ws.state_design(DesignSection.COMPONENTS, text).id


def test_a_task_without_notation_records_nothing(controller):
    task = controller.create_task("csv export")
    service = DiagramService(controller, task.id)
    assert service.sync() is None and service.versions() == [] and service.latest() is None
    _state_design(controller, task.id, "One module writes the rows")
    assert service.sync() is None and service.versions() == []
    assert controller.conn.execute("SELECT COUNT(*) FROM diagram_versions").fetchone()[0] == 0


def test_versions_are_recorded_once_per_change_and_never_altered(controller, db_path):
    task = controller.create_task("csv export")
    add_sample_design(controller, task.id)
    service = DiagramService(controller, task.id)
    first = service.sync()
    assert (first.version, first.task_id) == (1, task.id)
    assert first.content_hash == "sha256:" + hashlib.sha256(first.model_json.encode()).hexdigest()
    assert first.content_hash == service.model().content_hash
    assert service.sync() is None
    other = connect(db_path)
    try:
        assert DiagramService(HarnessController(other), task.id).sync() is None
    finally:
        other.close()
    assert [v.version for v in service.versions()] == [1]
    assert [s["type"] for s in service.scenes(first)] == TYPES

    entry = _state_design(controller, task.id, "component Exporter")
    second = service.sync()
    assert second.version == 2 and second.content_hash != first.content_hash
    assert service.version(1) == first and service.latest() == second
    ws = controller.workspace(task.id)
    with transaction(controller.conn):
        for e in ws.design_entries():
            if e.effective:
                ws.withdraw_design(e.id)
    assert ws.design_entry(entry).status == "withdrawn"
    after_design = _state(controller.conn)
    third = service.sync()
    assert third.version == 3 and service.model().empty
    assert service.scenes(third) == [] and third.model["components"] == []
    assert service.sync() is None
    assert [v.version for v in service.versions()] == [1, 2, 3]
    assert service.version(1) == first and service.version(2) == second
    assert _state(controller.conn) == after_design
    with pytest.raises(RecordNotFound):
        service.version(99)


def test_sync_writes_only_diagram_versions(controller):
    task = controller.create_task("csv export")
    add_sample_design(controller, task.id)
    before = _state(controller.conn)
    DiagramService(controller, task.id).sync()
    assert _state(controller.conn) == before


def test_versions_are_per_task(controller):
    one, two = controller.create_task("one"), controller.create_task("two")
    _state_design(controller, one.id, "component A")
    _state_design(controller, two.id, "component B")
    assert DiagramService(controller, one.id).sync().version == 1
    assert DiagramService(controller, two.id).sync().version == 1
    with pytest.raises(TaskNotFound):
        DiagramService(controller, "t-00000000")


def test_sync_refuses_an_open_transaction_and_reports_a_held_lock(controller, db_path):
    task = controller.create_task("csv export")
    _state_design(controller, task.id, "component A")
    service = DiagramService(controller, task.id)
    controller.conn.execute("BEGIN")
    try:
        with pytest.raises(sqlite3.OperationalError, match="outside a transaction"):
            service.sync()
    finally:
        controller.conn.execute("ROLLBACK")
    holder = connect(db_path)
    try:
        holder.execute("BEGIN IMMEDIATE")
        controller.conn.execute("PRAGMA busy_timeout=50")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            service.sync()
        assert not controller.conn.in_transaction
        holder.execute("ROLLBACK")
    finally:
        holder.close()
    assert service.sync().version == 1


def test_a_concurrent_writer_between_check_and_lock_records_one_version(controller, db_path):
    task = controller.create_task("csv export")
    _state_design(controller, task.id, "component A")
    service = DiagramService(controller, task.id)
    other = connect(db_path)
    original = service.latest
    calls = []

    def racing_latest():
        calls.append(1)
        if len(calls) == 1:
            found = original()
            assert DiagramService(HarnessController(other), task.id).sync().version == 1
            return found
        return original()

    try:
        service.latest = racing_latest
        assert service.sync() is None
    finally:
        other.close()
    assert [v.version for v in DiagramService(controller, task.id).versions()] == [1]


def test_the_unique_constraint_backs_up_the_version_numbering(controller):
    task = controller.create_task("csv export")
    _state_design(controller, task.id, "component A")
    DiagramService(controller, task.id).sync()
    with pytest.raises(sqlite3.IntegrityError):
        controller.conn.execute(
            "INSERT INTO diagram_versions (task_id, version, model, content_hash, created_at)"
            " VALUES (?, 1, '{}', 'sha256:00', 'then')",
            (task.id,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        controller.conn.execute(
            "INSERT INTO diagram_versions (task_id, version, model, content_hash, created_at)"
            " VALUES (?, 0, '{}', 'sha256:00', 'then')",
            (task.id,),
        )
