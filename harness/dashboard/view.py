"""The dashboard's read model: plain JSON-ready data from the CLI's state DB and spec files.

No HTTP here. The DB is opened per call exactly like the CLI opens it and is never created.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from harness.cli_spec import spec_store
from harness.controller import HarnessController, Task, TaskNotFound
from harness.dashboard.decisions_view import decisions_section
from harness.dashboard.overview import overview, task_row
from harness.dashboard.spec_view import spec_section
from harness.db import connect

NO_ACTIVE_TASK = "no active task"


@contextmanager
def open_state(db_path: Path) -> Iterator[HarnessController]:
    """The CLI's own read-write store; the single seam every dashboard read or write uses."""
    conn = connect(db_path)
    try:
        yield HarnessController(conn)
    finally:
        conn.close()


@contextmanager
def _snapshot(conn: sqlite3.Connection) -> Iterator[None]:
    """One deferred read transaction: every SELECT of a view sees the same committed state."""
    conn.execute("BEGIN")
    try:
        yield
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")


def _task_view(controller: HarnessController, task: Task, specs: str | None) -> dict[str, Any]:
    store = spec_store(controller, task.id, specs)
    return {
        "id": task.id,
        "request": task.request,
        "phase": str(task.phase),
        "complexity": str(task.complexity),
        "complexity_source": task.complexity_source,
        "spec": spec_section(store),
        "decisions": decisions_section(controller, task),
    } | overview(controller, task, store)


def _rows(controller: HarnessController, specs: str | None) -> list[dict[str, Any]]:
    tasks = controller.list_tasks()
    ordered = sorted(sorted(tasks, key=lambda t: t.id), key=lambda t: t.updated_at, reverse=True)
    return [task_row(controller, task, specs) for task in ordered]


def _notice(task_id: str | None) -> str:
    return str(TaskNotFound(task_id)) if task_id else NO_ACTIVE_TASK


def dashboard_view(db_path: Path, task_id: str | None, specs: str | None) -> dict[str, Any]:
    """The selected task and every task's row; no or an unknown task gives a notice instead."""
    if not Path(db_path).exists():
        return {"task": None, "notice": _notice(task_id), "tasks": []}
    with open_state(db_path) as controller, _snapshot(controller.conn):
        task = None
        if task_id:
            try:
                task = controller.get_task(task_id)
            except TaskNotFound:
                pass
        shown = None if task is None else _task_view(controller, task, specs)
        notice = None if task is not None else _notice(task_id)
        return {"task": shown, "notice": notice, "tasks": _rows(controller, specs)}
