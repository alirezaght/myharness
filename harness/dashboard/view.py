"""The dashboard's read model: plain JSON-ready data from the CLI's state DB and spec files.

No HTTP here. The DB is opened per call exactly like the CLI opens it and is never created.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from harness.cli_spec import spec_store
from harness.controller import HarnessController, Task, TaskNotFound
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


def _task_view(controller: HarnessController, task: Task, specs: str | None) -> dict[str, Any]:
    store = spec_store(controller, task.id, specs)
    return {
        "id": task.id,
        "request": task.request,
        "phase": str(task.phase),
        "complexity": str(task.complexity),
        "complexity_source": task.complexity_source,
        "spec": {
            "directory": str(store.directory),
            "versions": [
                {"version": record.version, "status": str(record.status)}
                for record in store.records()
            ],
            "draft": store.draft_path.is_file(),
        },
    }


def _fallback(task_id: str | None, tasks: list[Task]) -> dict[str, Any]:
    ordered = sorted(sorted(tasks, key=lambda t: t.id), key=lambda t: t.updated_at, reverse=True)
    return {
        "task": None,
        "notice": str(TaskNotFound(task_id)) if task_id else NO_ACTIVE_TASK,
        "tasks": [
            {"id": t.id, "phase": str(t.phase), "request": t.request, "updated_at": t.updated_at}
            for t in ordered
        ],
    }


def dashboard_view(db_path: Path, task_id: str | None, specs: str | None) -> dict[str, Any]:
    """The active task; with no or an unknown task the task list (or none) instead of an error."""
    if not Path(db_path).exists():
        return _fallback(task_id, [])
    with open_state(db_path) as controller:
        if task_id:
            try:
                task = controller.get_task(task_id)
            except TaskNotFound:
                pass
            else:
                return {"task": _task_view(controller, task, specs), "notice": None, "tasks": []}
        return _fallback(task_id, controller.list_tasks())
