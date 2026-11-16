"""Task-scoped knowledge graph and exact-preview planning recovery routes."""

import json
import sqlite3
from pathlib import Path

from harness.cli_knowledge import (
    evidence_knowledge,
    explain_knowledge,
    inspect_knowledge,
    query_knowledge,
    refresh_knowledge,
    repository_for_task,
)
from harness.dashboard.bodies import busy_failure, parse_body
from harness.dashboard.view import open_state
from harness.execution_store import get_execution


def read_knowledge(db_path, task_id):
    if not Path(db_path).is_file():
        raise LookupError("knowledge database not found")
    with open_state(db_path) as controller:
        repository = repository_for_task(controller, task_id)
        return inspect_knowledge(controller, repository)


def read_knowledge_evidence(db_path, task_id, bundle_hash, assertion_id):
    if not Path(db_path).is_file():
        raise LookupError("knowledge database not found")
    with open_state(db_path) as controller:
        repository = repository_for_task(controller, task_id)
        return evidence_knowledge(controller, repository, bundle_hash, assertion_id)


def knowledge_action(db_path, body, action):
    try:
        data = parse_body(body)
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        fields = {"task"}
        if action == "query":
            fields.add("query")
            if not isinstance(data, dict) or not isinstance(data.get("query"), str):
                raise ValueError("query text is required")
        elif action == "explain":
            fields |= {"bundle_hash", "assertion_id"}
        if not isinstance(data, dict) or set(data) != fields:
            raise ValueError("unexpected knowledge action fields")
        if not isinstance(data["task"], str) or not data["task"]:
            raise ValueError("explicit task ID required")
        with open_state(db_path) as controller:
            repository = repository_for_task(controller, data["task"])
            if action == "refresh":
                result = refresh_knowledge(controller, repository, task_id=data["task"])
            elif action == "query":
                result = query_knowledge(controller, repository, data["query"])
            else:
                result = explain_knowledge(
                    controller, repository, data["bundle_hash"], data["assertion_id"]
                )
        return 200, result
    except LookupError as error:
        return 404, {"error": str(error)}
    except (ValueError, TypeError, KeyError) as error:
        return 409, {"error": str(error)}
    except PermissionError as error:
        return 403, {"error": str(error)}
    except sqlite3.OperationalError as error:
        return busy_failure(error)
    except OSError as error:
        return 503, {"error": str(error)}


def planning_preview(db_path, task_id, execution_id, epoch_id, generation):
    if not Path(db_path).is_file():
        raise LookupError("execution database not found")
    with open_state(db_path) as controller:
        execution = get_execution(controller.conn, execution_id)
        if execution["task_id"] != task_id:
            raise LookupError("execution not found for task")
        from harness.execution_planning_recovery import preview_planning

        return preview_planning(
            controller,
            execution_id,
            expected_epoch_id=epoch_id,
            expected_authority_generation=generation,
        )


def spec_restoration_preview(db_path, task_id, execution_id, epoch_id, generation):
    if not Path(db_path).is_file():
        raise LookupError("execution database not found")
    with open_state(db_path) as controller:
        execution = get_execution(controller.conn, execution_id)
        if execution["task_id"] != task_id:
            raise LookupError("execution not found for task")
        from harness.execution_spec_recovery import preview_spec_restoration

        return preview_spec_restoration(
            controller,
            execution_id,
            expected_epoch_id=epoch_id,
            expected_authority_generation=generation,
        )


def spec_restoration_action(db_path, body):
    try:
        data = parse_body(body)
        fields = {
            "task",
            "execution",
            "epoch_id",
            "authority_generation",
            "request_id",
            "preview_sha256",
            "confirmation",
        }
        if not isinstance(data, dict) or set(data) != fields:
            raise ValueError("unexpected specification restoration fields")
        if data["confirmation"] != "Restore approved specification":
            raise ValueError('confirmation must exactly equal "Restore approved specification"')
        if any(
            not isinstance(data[key], str) or not data[key]
            for key in ("task", "execution", "epoch_id", "request_id", "preview_sha256")
        ):
            raise ValueError("explicit task, execution, epoch, request and preview are required")
        if type(data["authority_generation"]) is not int or data["authority_generation"] < 0:
            raise ValueError("invalid authority generation")
        if not Path(db_path).is_file():
            raise LookupError("execution database not found")
        with open_state(db_path) as controller:
            execution = get_execution(controller.conn, data["execution"])
            if execution["task_id"] != data["task"]:
                raise LookupError("execution not found for task")
            from harness.execution_spec_recovery import restore_specification

            result = restore_specification(
                controller,
                data["execution"],
                request_id=data["request_id"],
                preview_sha256=data["preview_sha256"],
                expected_epoch_id=data["epoch_id"],
                expected_authority_generation=data["authority_generation"],
            )
        return 200, result
    except LookupError as error:
        return 404, {"error": str(error)}
    except PermissionError as error:
        return 403, {"error": str(error)}
    except (ValueError, TypeError, KeyError) as error:
        return 409, {"error": str(error)}
    except sqlite3.OperationalError as error:
        return busy_failure(error)
    except OSError as error:
        return 503, {"error": str(error)}


def planning_action(db_path, body, action):
    try:
        data = parse_body(body)
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        fields = {"task", "execution", "epoch_id", "authority_generation"}
        if action == "planning-start":
            fields |= {"preview_sha256", "request_id", "confirmation"}
            if data.get("confirmation") != "Start new planning":
                raise ValueError('confirmation must exactly equal "Start new planning"')
        elif action in {"planning-inspect", "planning-finish"}:
            fields |= {"request_id", "preview_sha256"}
        if not isinstance(data, dict) or set(data) != fields:
            raise ValueError("unexpected planning recovery fields")
        if any(
            not isinstance(data[key], str) or not data[key]
            for key in ("task", "execution", "epoch_id")
        ):
            raise ValueError("explicit task, execution and epoch IDs required")
        if type(data["authority_generation"]) is not int or data["authority_generation"] < 0:
            raise ValueError("invalid authority generation")
        if not Path(db_path).is_file():
            raise LookupError("execution database not found")
        with open_state(db_path) as controller:
            execution = get_execution(controller.conn, data["execution"])
            if execution["task_id"] != data["task"]:
                raise LookupError("execution not found for task")
            if action != "planning-start":
                row = controller.conn.execute(
                    "SELECT request FROM execution_runtime_transitions "
                    "WHERE execution_id=? AND request_id=?",
                    (data["execution"], data["request_id"]),
                ).fetchone()
                if not row:
                    raise LookupError("planning recovery request not found for this execution")
                request = json.loads(row["request"])
                if (
                    request.get("transition_mode")
                    not in {"fresh-planning-missing-state", "fresh-planning-intact"}
                    or request.get("preview_sha256") != data["preview_sha256"]
                    or request.get("expected_epoch_id") != data["epoch_id"]
                    or request.get("expected_authority_generation") != data["authority_generation"]
                ):
                    raise ValueError(
                        "planning recovery request is bound to another preview or epoch"
                    )
            from harness.execution_planning_recovery import (
                finish_planning,
                inspect_planning,
                start_planning,
            )

            if action == "planning-start":
                result = start_planning(
                    controller,
                    data["execution"],
                    request_id=data["request_id"],
                    preview_sha256=data["preview_sha256"],
                    expected_epoch_id=data["epoch_id"],
                    expected_authority_generation=data["authority_generation"],
                )
            elif action == "planning-inspect":
                result = inspect_planning(
                    controller, data["execution"], request_id=data["request_id"]
                )
            else:
                result = finish_planning(
                    controller,
                    data["execution"],
                    request_id=data["request_id"],
                    preview_sha256=data["preview_sha256"],
                )
        return 200, result
    except LookupError as error:
        return 404, {"error": str(error)}
    except PermissionError as error:
        return 403, {"error": str(error)}
    except (ValueError, TypeError, KeyError) as error:
        return 409, {"error": str(error)}
    except sqlite3.OperationalError as error:
        return busy_failure(error)
    except OSError as error:
        return 503, {"error": str(error)}
