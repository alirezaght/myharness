"""Strict proposal-only dashboard write route."""

import sqlite3
from pathlib import Path

from harness.cli_spec import spec_store
from harness.dashboard.bodies import BAD_BODY, busy_failure, keys_problem, parse_body
from harness.dashboard.execution_view import task_execution
from harness.workspace_proposals import ProposalConflict, WorkspaceProposalService


def add_proposal(db_path, body, specs=None):
    try:
        data = parse_body(body)
        if not isinstance(data, dict):
            raise ValueError(BAD_BODY)
        if problem := keys_problem(data, frozenset({"task", "base", "operations", "request_id"})):
            raise ValueError(problem)
        if not isinstance(data["task"], str) or not data["task"]:
            raise ValueError("task must be a non-empty string")
        if not Path(db_path).exists():
            return 404, {"error": "task state unavailable"}
        from harness.dashboard.view import open_state

        with open_state(db_path) as controller:
            proposal, created = WorkspaceProposalService(controller, data["task"]).add(
                data["base"],
                data["operations"],
                data["request_id"],
                spec_store(controller, data["task"], specs),
                lambda: task_execution(controller, data["task"]),
            )
            return 201 if created else 200, {"proposal": proposal}
    except ProposalConflict as error:
        return 409, {"error": str(error), "reload": True}
    except LookupError as error:
        return 404, {"error": str(error)}
    except (ValueError, TypeError, OverflowError) as error:
        return 400, {"error": str(error)}
    except sqlite3.OperationalError as error:
        return busy_failure(error)
    except (sqlite3.Error, OSError) as error:
        return 500, {"error": str(error)}
