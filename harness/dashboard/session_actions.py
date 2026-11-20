"""Explicit local-user session recovery. Browser never supplies an agent credential."""

from harness.authoring_contract import decode, exact, request_identity
from harness.dashboard.view import _snapshot, open_state
from harness.planning_session import PlanningSessionService
from harness.planning_session_contract import SessionError


def session_action(db_path, root, body):
    import sqlite3

    from harness.dashboard.bodies import busy_failure

    try:
        request = decode(body)
        exact(request, {"task", "request_id", "expected", "epoch", "action"})
        request_identity(request["request_id"])
        if (
            request["action"]
            not in {"pause", "request-reattach", "choose-standalone", "choose-manual"}
            or type(request["epoch"]) is not int
        ):
            raise SessionError("SESSION_PROTOCOL_UNSUPPORTED")
        with open_state(db_path) as controller:
            bridge = PlanningSessionService(controller, root, request["task"])
            with bridge._transaction(origin="browser") as (service, _):
                scope = f"session-browser:{bridge.project}:{bridge.task_id}:{request['epoch']}"
                prior = bridge.store.receipt(scope, request["request_id"], request)
                if prior:
                    return 200, prior
                bridge._check_base(request["expected"])
                owner = bridge.store.owner()
                if (owner["epoch"] if owner else 0) != request["epoch"]:
                    raise SessionError("SESSION_OWNER_LOST")
                action = request["action"]
                if action in {"pause", "request-reattach"}:
                    if not owner or owner["mode"] != "attached":
                        raise SessionError("SESSION_OPERATION_INCOMPATIBLE")
                    if action == "pause":
                        bridge.store.touch(*bridge.clock(), "disconnected", 0)
                else:
                    bridge._unsettled()
                    from harness.investigation_eligibility import require_eligible

                    require_eligible(service)
                    mode = "standalone" if action == "choose-standalone" else "manual"
                    if owner:
                        controller.conn.execute(
                            (
                                "UPDATE planning_session_owners SET "
                                "mode=?,epoch=epoch+1,attachment_id=NULL,status_revision=status_re"
                                "vision+1 WHERE task_id=?"
                            ),
                            (mode, bridge.task_id),
                        )
                    else:
                        controller.conn.execute(
                            (
                                "INSERT INTO planning_session_owners(task_id,project,mode,epoch) "
                                "VALUES(?,?,?,1)"
                            ),
                            (bridge.task_id, bridge.project, mode),
                        )
                bridge.store.append_event(
                    "session." + action,
                    {"request_id": request["request_id"]},
                    origin="browser",
                    deliverable=action == "request-reattach",
                )
                result = {
                    "task": bridge.task_id,
                    "base": bridge.base(),
                    "session": bridge.status(),
                    "provider_invoked": False,
                }
                bridge.store.record_receipt(scope, request["request_id"], request, result)
                return 200, result
    except SessionError as error:
        return 409, error.public()
    except (ValueError, KeyError, TypeError):
        return 400, {
            "error": "Invalid session recovery request",
            "code": "SESSION_PROTOCOL_UNSUPPORTED",
        }
    except LookupError:
        return 404, {"error": "Task not found"}
    except sqlite3.OperationalError as error:
        return busy_failure(error)


def session_history(db_path, root, task_id, *, before=None, limit=50):
    with open_state(db_path) as controller, _snapshot(controller.conn):
        bridge = PlanningSessionService(controller, root, task_id)
        controller.get_task(task_id)
        return {
            "task": task_id,
            "base": bridge.base(),
            "session": bridge.status(before=before, limit=limit),
        }
