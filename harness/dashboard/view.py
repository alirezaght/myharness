"""The dashboard's read model: plain JSON-ready data from the CLI's state DB and spec files.

No HTTP here. The DB is opened per call exactly like the CLI opens it and is never created.
The only write of a read is the Diagram Service recording a new diagram version of the
selected task when its design changed, committed before the view's read snapshot starts.
The dashboard's other writes are comments (comments_view.add_comment and move_comment) and the
approval snapshot of Approve & Implement (approve.approve_task, through the CLI's ApprovalGate).
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from harness.cli_spec import spec_store
from harness.controller import HarnessController, Task, TaskNotFound
from harness.dashboard.approval_view import approval_center
from harness.dashboard.comments_view import comments_section
from harness.dashboard.decisions_view import decisions_section
from harness.dashboard.diagrams_view import diagrams_section
from harness.dashboard.overview import overview, task_row
from harness.dashboard.spec_view import spec_section
from harness.dashboard.workspace_view import workspace_section
from harness.db import connect
from harness.diagrams import DiagramService

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


def _task_view(
    controller: HarnessController, task: Task, specs: str | None, sync_error: str | None
) -> dict[str, Any]:
    store = spec_store(controller, task.id, specs)
    shown = overview(controller, task, store)
    from harness.dashboard.execution_view import task_execution

    execution = task_execution(controller, task.id)
    if execution is not None:
        delivery = controller.conn.execute(
            "SELECT 1 FROM deliveries WHERE execution_id=?", (execution["id"],)
        ).fetchone()
        if delivery is not None:
            from harness.delivery_store import delivery_view

            execution["delivery"] = delivery_view(controller.conn, execution["id"])
    center = approval_center(
        controller, task, store, shown["pending_approvals"], shown["resume"]["approval"]
    )
    center["execution_blockers"] = execution["blockers"] if execution else []
    center["execution_decisions"] = execution["decisions"] if execution else []
    return (
        {
            "id": task.id,
            "execution": execution,
            "workspace": workspace_section(controller, task, store, execution),
            "request": task.request,
            "phase": str(task.phase),
            "complexity": str(task.complexity),
            "complexity_source": task.complexity_source,
            "spec": spec_section(store),
            "decisions": decisions_section(controller, task),
            "diagrams": diagrams_section(controller, task, sync_error, execution),
            "comments": comments_section(controller, task),
        }
        | shown
        | {"approval_center": center}
    )


def _rows(controller: HarnessController, specs: str | None) -> list[dict[str, Any]]:
    tasks = controller.list_tasks()
    ordered = sorted(sorted(tasks, key=lambda t: t.id), key=lambda t: t.updated_at, reverse=True)
    return [task_row(controller, task, specs) for task in ordered]


def _notice(task_id: str | None) -> str:
    return str(TaskNotFound(task_id)) if task_id else NO_ACTIVE_TASK


def _find(controller: HarnessController, task_id: str | None) -> Task | None:
    if not task_id:
        return None
    try:
        return controller.get_task(task_id)
    except TaskNotFound:
        return None


def _sync(controller: HarnessController, task_id: str) -> str | None:
    """Record a new diagram version if the design changed; a failure only marks the section."""
    try:
        DiagramService(controller, task_id).sync()
    except sqlite3.Error as error:
        return f"diagram version could not be recorded: {error}"
    return None


def compose_view(controller, task_id, specs, sync_error):
    task = _find(controller, task_id)
    shown = None if task is None else _task_view(controller, task, specs, sync_error)
    return {
        "task": shown,
        "notice": None if task else _notice(task_id),
        "tasks": _rows(controller, specs),
    }


def dashboard_view(db_path: Path, task_id: str | None, specs: str | None) -> dict[str, Any]:
    """Legacy read wrapper; diagram writes precede its single database snapshot."""
    return combined_view(db_path, task_id, specs)


def combined_view(db_path, task_id, specs, *, root=None):
    from harness.dashboard.authoring_view import authoring_snapshot

    if not Path(db_path).is_file():
        data = {"task": None, "notice": _notice(task_id), "tasks": []}
        if root is not None:
            data["authoring"] = authoring_snapshot(None, root, None, specs=specs)
        return data
    with open_state(db_path) as controller:
        found = _find(controller, task_id)
        sync_error = None if found is None else _sync(controller, found.id)
        with _snapshot(controller.conn):
            data = compose_view(controller, task_id, specs, sync_error)
            if root is not None:
                try:
                    data["authoring"] = authoring_snapshot(
                        controller, root, task_id if data["task"] else None, specs=specs
                    )
                except (LookupError, ValueError, OSError):
                    data["authoring"] = {
                        "error": "Selected task is outside the configured authoring project",
                        "task": task_id,
                    }
            return data
