"""Authoring read adapter; does not initialize or enqueue work."""

from pathlib import Path

from harness.authoring import AuthoringService, project_identity
from harness.authoring_policy import availability


def read_authoring(db_path, root, task_id=None, *, specs=None, hosts=False):
    from harness.dashboard.view import _snapshot, open_state

    if not Path(db_path).is_file():
        result = authoring_snapshot(None, root, None, specs=specs)
    else:
        with open_state(db_path) as controller, _snapshot(controller.conn):
            result = authoring_snapshot(controller, root, task_id, specs=specs)
    if hosts:
        result["hosts"] = [availability(host) for host in ("codex", "claude")]
    return result


def authoring_snapshot(controller, root, task_id, *, specs=None):
    if controller is None:
        return {
            "project": {"id": project_identity(root), "repository": str(Path(root).resolve())},
            "task": None,
            "initialized": False,
        }
    return AuthoringService(controller, root, task_id, specs=specs).snapshot()
