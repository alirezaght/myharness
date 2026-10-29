"""Fixed execution-bound dashboard reads/actions with strict request validation."""

import sqlite3
from pathlib import Path

from harness.dashboard.action_readiness import RESTART, permission_failure, require_ready
from harness.dashboard.bodies import busy_failure, parse_body
from harness.dashboard.view import open_state
from harness.execution_decisions import record_execution_decision, retry_execution_decision
from harness.execution_logs import read_log
from harness.execution_projection import event_page, execution_view
from harness.execution_recovery import reconcile_execution, reconnect_execution, resume_execution
from harness.execution_store import get_execution, latest_execution


def task_execution(controller, task_id):
    latest = latest_execution(controller.conn, task_id)
    return execution_view(controller.conn, latest["id"]) if latest else None


def read_execution(
    db_path, task_id, execution_id, *, action="status", after=0, limit=100, log_id=None
):
    if not Path(db_path).is_file():
        raise LookupError("execution database not found")
    with open_state(db_path) as controller:
        if get_execution(controller.conn, execution_id)["task_id"] != task_id:
            raise LookupError("execution not found for task")
        if action == "events":
            return event_page(controller.conn, execution_id, after, limit)
        if action == "logs":
            return read_log(controller.conn, execution_id, log_id)
        return execution_view(controller.conn, execution_id)


def execution_action(db_path, body, action):
    try:
        try:
            data = parse_body(body)
            fields = {"task", "execution"}
            epoch_fields = {"epoch_id", "authority_generation"}
            supplied_epoch = set(data) & epoch_fields if isinstance(data, dict) else set()
            if action in {"recover", "retry-checks"} and not supplied_epoch:
                raise ValueError("recovery requires the current execution epoch identity")
            if supplied_epoch and supplied_epoch != epoch_fields:
                raise ValueError("epoch ID and authority generation must be supplied together")
            fields |= supplied_epoch
            if action == "decision":
                fields |= {
                    "blocker",
                    "revision",
                    "request_id",
                    "verdict",
                    "text",
                    "tasks",
                    "criteria",
                    "amendment",
                }
            elif action == "decision-retry":
                fields |= {"delivery"}
            elif action == "reconcile":
                fields |= {"outcome", "evidence"}
            if action == "reconcile" and isinstance(data, dict) and "native_id" in data:
                fields |= {"native_id"}
            if not isinstance(data, dict) or set(data) != fields:
                raise ValueError("unexpected execution action fields")
            if any(not isinstance(data[k], str) or not data[k] for k in ("task", "execution")):
                raise ValueError("explicit task and execution IDs required")
            if supplied_epoch and (
                not isinstance(data["epoch_id"], str)
                or not data["epoch_id"]
                or type(data["authority_generation"]) is not int
                or data["authority_generation"] < 0
            ):
                raise ValueError("invalid execution epoch identity")
        except (ValueError, TypeError, KeyError) as error:
            return 400, {"error": str(error)}
        if not Path(db_path).is_file():
            raise LookupError("execution database not found")
        with open_state(db_path) as controller:
            epoch_kwargs = (
                {
                    "expected_epoch_id": data["epoch_id"],
                    "expected_authority_generation": data["authority_generation"],
                }
                if supplied_epoch
                else {}
            )
            if get_execution(controller.conn, data["execution"])["task_id"] != data["task"]:
                raise LookupError("execution not found for task")
            require_ready(
                get_execution(controller.conn, data["execution"]),
                "resume" if action in {"recover", "retry-checks"} else action,
            )
            if action in {"recover", "retry-checks"}:
                from harness.execution_controls import recover_and_continue

                result = recover_and_continue(
                    controller, data["execution"], action=action, **epoch_kwargs
                )
            elif action == "decision":
                result = record_execution_decision(
                    controller,
                    data["execution"],
                    data["blocker"],
                    blocker_revision=data["revision"],
                    request_id=data["request_id"],
                    verdict=data["verdict"],
                    decision_text=data["text"],
                    tasks=data["tasks"],
                    criteria=data["criteria"],
                    amendment=data["amendment"],
                    **epoch_kwargs,
                )
                if result["state"] == "Delivered":
                    reconnect_execution(controller, data["execution"], **epoch_kwargs)
            elif action == "decision-retry":
                result = retry_execution_decision(
                    controller, data["execution"], data["delivery"], **epoch_kwargs
                )
                if result["state"] == "Delivered":
                    reconnect_execution(controller, data["execution"], **epoch_kwargs)
            elif action == "reconcile":
                result = reconcile_execution(
                    controller,
                    data["execution"],
                    outcome=data["outcome"],
                    evidence=data["evidence"],
                    native_id=data.get("native_id"),
                    **epoch_kwargs,
                )
            else:
                result = (reconnect_execution if action == "reconnect" else resume_execution)(
                    controller, data["execution"], **epoch_kwargs
                )
            if action in {"decision", "decision-retry"}:
                result = next(
                    d
                    for d in execution_view(controller.conn, data["execution"])["decisions"]
                    if d["id"] == result["id"]
                )
        from harness.execution_projection import presentation

        return 200, presentation(result)
    except LookupError as error:
        return 404, {"error": str(error)}
    except (ValueError, TypeError, KeyError) as error:
        return 409, {"error": str(error)}
    except sqlite3.OperationalError as error:
        return busy_failure(error)
    except OSError as error:
        return 503, {"error": str(error) + (" " + RESTART if permission_failure(error) else "")}
