"""Exact displayed approval tokens and explicit dashboard launch."""

import sqlite3
from pathlib import Path

from harness.approval import require_approval
from harness.cli_commands import task_repo_root
from harness.dashboard.bodies import busy_failure, parse_body
from harness.execution import approval_binding, start_execution


def launch_descriptor(controller, task_id, store):
    try:
        approval = require_approval(controller, task_id, store, task_repo_root(controller, task_id))
        return {"binding": approval_binding(task_id, approval), "diagnostic": None}
    except (ValueError, OSError, LookupError, sqlite3.Error) as error:
        return {"binding": None, "diagnostic": str(error)}


def start_task(db_path, body, specs=None):
    from harness.dashboard.view import open_state

    try:
        data = parse_body(body)
        if not isinstance(data, dict) or set(data) != {"task", "host", "approval_binding"}:
            raise ValueError("expected task, host and approval_binding only")
        binding = data["approval_binding"]
        if (
            not isinstance(data["task"], str)
            or not data["task"]
            or data["host"] not in ("codex", "claude")
            or not isinstance(binding, dict)
            or set(binding)
            != {
                "task_id",
                "approval_id",
                "spec_version",
                "content_hash",
                "revision",
                "review_state_hash",
            }
            or binding["task_id"] != data["task"]
            or any(
                type(binding[key]) is not int or binding[key] < 1
                for key in ("approval_id", "spec_version")
            )
            or any(
                not isinstance(binding[key], str) or not binding[key]
                for key in ("task_id", "content_hash", "revision", "review_state_hash")
            )
        ):
            raise ValueError("invalid explicit host or approval binding")
    except (ValueError, TypeError, KeyError) as error:
        return 400, {"error": str(error)}
    if not Path(db_path).is_file():
        return 404, {"error": "execution database not found"}
    try:
        with open_state(db_path) as controller:
            result = start_execution(
                controller,
                data["task"],
                host=data["host"],
                specs=specs,
                expected_approval_binding=binding,
            )
        return 200, result
    except LookupError as error:
        return 404, {"error": str(error)}
    except (ValueError, TypeError) as error:
        return 409, {"error": str(error), "reload": True}
    except sqlite3.OperationalError as error:
        return busy_failure(error)
    except OSError as error:
        return 503, {"error": str(error)}
