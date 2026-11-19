"""Disposable SQLite startup atomicity and receipt replay."""

import pytest

from harness.authoring import AuthoringService
from harness.authoring_contract import AuthoringConflict
from harness.controller import HarnessController
from harness.db import connect, transaction


def test_startup_replay_and_rollback(controller, tmp_path, monkeypatch):
    service = AuthoringService(controller, tmp_path)
    monkeypatch.setattr("harness.authoring_policy.availability", lambda host: {"available": False})
    first = service.start(request_id="startup-1234", host="codex", request="Build a task tool")
    assert (
        service.start(request_id="startup-1234", host="codex", request="Build a task tool") == first
    )
    assert len(controller.list_tasks()) == 1
    with pytest.raises(AuthoringConflict):
        service.start(request_id="startup-1234", host="codex", request="Different")
    original = controller.create_task_in_transaction

    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected failure")

    monkeypatch.setattr(controller, "create_task_in_transaction", failed)
    with pytest.raises(RuntimeError):
        service.start(request_id="startup-5678", host="codex", request="Another")
    assert len(controller.list_tasks()) == 1
    assert controller.conn.execute("SELECT count(*) FROM authoring_jobs").fetchone()[0] == 1


def test_composed_task_creation(controller, db_path):
    with pytest.raises(RuntimeError):
        controller.create_task_in_transaction("x", task_id="t-12345678")
    with pytest.raises(RuntimeError), transaction(controller.conn):
        controller.create_task_in_transaction(" x ", task_id="t-12345678")
        raise RuntimeError("rollback")
    assert controller.list_tasks() == []
    task = controller.create_task(" x ", "trivial")
    other = connect(db_path)
    try:
        assert HarnessController(other).get_task(task.id).request == "x"
    finally:
        other.close()
    assert len(controller.history(task.id)) == 1
    assert controller.workspace(task.id).events()[0].kind == "task.created"


def test_two_connections_start_the_same_request_once(db_path, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr("harness.authoring_policy.availability", lambda host: {"available": False})
    connect(db_path).close()

    def start():
        conn = connect(db_path)
        try:
            return AuthoringService(HarnessController(conn), tmp_path).start(
                request_id="concurrent-start", host="codex", request="Build scheduler"
            )
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: start(), range(2)))
    assert first == second
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM authoring_jobs").fetchone()[0] == 1
    finally:
        conn.close()


def test_migration_17_upgrade_does_not_initialize_authoring(tmp_path, monkeypatch):
    from harness import db

    original = db._migrations
    monkeypatch.setattr(db, "_migrations", lambda: [m for m in original() if m[0] <= 17])
    path = tmp_path / "upgrade.db"
    conn = connect(path)
    task = HarnessController(conn).create_task("preserved task")
    conn.close()
    monkeypatch.setattr(db, "_migrations", original)
    conn = connect(path)
    try:
        assert HarnessController(conn).get_task(task.id) == task
        assert conn.execute("SELECT count(*) FROM authoring_tasks").fetchone()[0] == 0
        assert db.apply_migrations(conn) == []
    finally:
        conn.close()


def test_uninvestigated_spec_projection_uses_configured_project(controller, tmp_path):
    task = controller.create_task("Create project-scoped spec")
    project = tmp_path / "separate-project"
    project.mkdir()
    service = AuthoringService(controller, project, task.id)
    service.start(expected=service.store.base(), request_id="resume-12345", host="codex")
    service.save_spec(
        expected=service.store.base(), request_id="save-12345", body="## Trusted project\n"
    )
    revision = service.store.base()["working"]
    preview = service.reconcile_preview(revision)
    service.reconcile(
        expected=service.store.base(),
        request_id="adopt-12345",
        revision=revision,
        token=preview["token"],
        confirm_graph=True,
        acknowledge=True,
        actions=[],
    )
    assert service.manager().store.draft_path.is_relative_to(project)
    assert service.manager().store.draft_path.is_file()
