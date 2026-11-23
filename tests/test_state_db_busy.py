"""Issues #1/#2: a busy state DB is reported as busy, and long commands do not starve readers."""

import json
import re
import sqlite3
import time

import pytest

from harness.cli import main
from harness.db import apply_migrations, connect
from tests.test_dashboard import fetch, serve

TASK_ID = re.compile(r"task (t-[0-9a-f]{8}) created")


@pytest.fixture
def task_id(db_path, capsys):
    assert main(["--db", str(db_path), "start", "--complexity", "trivial", "fix", "it"]) == 0
    return TASK_ID.search(capsys.readouterr().out).group(1)


def _locked_error(db_path) -> sqlite3.OperationalError:
    """A real `database is locked` error, raised by a second writer that does not wait."""
    holder = sqlite3.connect(db_path, autocommit=True)
    holder.execute("BEGIN IMMEDIATE")
    try:
        waiter = sqlite3.connect(db_path, autocommit=True)
        waiter.execute("PRAGMA busy_timeout=0")
        try:
            waiter.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            return error
        finally:
            waiter.close()
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    raise AssertionError("the second writer was not refused")


def _run_raising(monkeypatch, capsys, db_path, task_id, error):
    def handler(args):
        raise error

    monkeypatch.setattr("harness.cli.cmd_show", handler)
    code = main(["--db", str(db_path), "show", task_id])
    return code, capsys.readouterr().err


def test_locked_state_db_is_reported_as_busy_not_as_upgrade(db_path, task_id, monkeypatch, capsys):
    error = _locked_error(db_path)
    code, err = _run_raising(monkeypatch, capsys, db_path, task_id, error)
    assert code == 1
    assert "SESSION_UPGRADE_REQUIRED" not in err
    assert "Upgrade Harness" not in err
    assert err.startswith("harness show: state database is busy")
    assert "retry" in err


def test_unrelated_sqlite_error_is_reported_as_itself(db_path, task_id, monkeypatch, capsys):
    error = sqlite3.IntegrityError("UNIQUE constraint failed: tasks.id")
    code, err = _run_raising(monkeypatch, capsys, db_path, task_id, error)
    assert code == 1
    assert "SESSION_UPGRADE_REQUIRED" not in err
    assert "UNIQUE constraint failed: tasks.id" in err
    assert err.startswith("harness show: ")


@pytest.mark.parametrize(
    "message",
    [
        "SESSION_UPGRADE_REQUIRED",
        "no such function: harness_session_protocol_version",
        "no such function: harness_session_write_allowed",
        "Incomplete planning session schema; upgrade Harness",
    ],
)
def test_real_session_version_mismatch_still_asks_for_upgrade(
    db_path, task_id, monkeypatch, capsys, message
):
    error = sqlite3.OperationalError(message)
    code, err = _run_raising(monkeypatch, capsys, db_path, task_id, error)
    assert code == 1
    assert "SESSION_UPGRADE_REQUIRED" in err


def test_incompatible_session_operation_keeps_its_code(db_path, task_id, monkeypatch, capsys):
    error = sqlite3.IntegrityError("SESSION_OPERATION_INCOMPATIBLE")
    code, err = _run_raising(monkeypatch, capsys, db_path, task_id, error)
    assert code == 1
    assert "SESSION_OPERATION_INCOMPATIBLE" in err


def test_opening_a_migrated_db_needs_no_write_lock(db_path, task_id):
    """Readers (dashboard, show) open the DB while another command holds the write lock."""
    holder = sqlite3.connect(db_path, autocommit=True)
    holder.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        conn = connect(db_path)
        try:
            assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
            assert apply_migrations(conn) == []
        finally:
            conn.close()
        assert time.monotonic() - started < 2
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_read_command_works_while_another_command_writes(db_path, task_id, capsys):
    holder = sqlite3.connect(db_path, autocommit=True)
    holder.execute("BEGIN IMMEDIATE")
    try:
        assert main(["--db", str(db_path), "show", task_id]) == 0
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert task_id in capsys.readouterr().out


def test_state_db_runs_in_wal_mode(db_path, task_id):
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_dashboard_view_still_works_while_another_command_writes(db_path, task_id):
    holder = sqlite3.connect(db_path, autocommit=True)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with serve(db_path, task_id) as server:
            status, _, body = fetch(server, "/api/dashboard")
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert status == 200, body
    assert json.loads(body)["task"]["id"] == task_id


def test_dashboard_launcher_reports_busy_db_at_startup(db_path, task_id, monkeypatch, capsys):
    from harness.dashboard import launch

    error = _locked_error(db_path)

    def busy_start(*args, **kwargs):
        raise error

    monkeypatch.setattr(launch, "start_dashboard", busy_start)
    assert launch.main([task_id, "--db", str(db_path), "--no-browser"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("harness dashboard: state database is busy")
    assert "SESSION_UPGRADE_REQUIRED" not in err
