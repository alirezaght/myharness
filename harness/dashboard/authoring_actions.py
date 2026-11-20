"""Strict task-scoped authoring HTTP actions; repository identity comes from the server."""

import re
import sqlite3

from harness.authoring import AuthoringService, NonAnswer
from harness.authoring_actions_contract import SCHEMAS
from harness.authoring_contract import (
    BASE_KEYS,
    AuthoringConflict,
    AuthoringContextTooLarge,
    decode,
    exact,
    request_identity,
    text,
)
from harness.dashboard.authoring_view import read_authoring
from harness.investigation_policy import AcquisitionError


def _scalars(data):
    for name in ("target", "target_revision", "revision", "token", "job", "host", "mode", "status"):
        if name in data:
            text(data[name], name, 200)
    for name in ("question", "proposal"):
        if name in data and (type(data[name]) is not int or data[name] < 1):
            raise ValueError("invalid record ID")
    for name in ("answer", "response"):
        if name in data:
            text(data[name], name, 16000, empty=True)
    for name in ("source", "ref"):
        if name in data and data[name] is not None:
            text(data[name], name, 16000, empty=True)
    for name in ("confirm_graph", "acknowledge", "acknowledge_unknown"):
        if name in data and type(data[name]) is not bool:
            raise ValueError("invalid boolean")
    if "expected" in data:
        base = data["expected"]
        if type(base["generation"]) is not int or base["generation"] < 0:
            raise ValueError("invalid generation")
        for name in ("project", "source", "authority"):
            if not isinstance(base[name], str) or not re.fullmatch(r"[0-9a-f]{64}", base[name]):
                raise ValueError("invalid base identity")
        for name in ("working", "graph"):
            if base[name] is not None:
                text(base[name], name, 100)


def authoring_action(db_path, root, body, action, *, specs=None):
    from harness.dashboard.view import open_state

    validated = False
    try:
        data = decode(body)
        if action == "new":
            exact(data, {"request_id", "request", "host"})
            request_identity(data["request_id"])
            text(data["request"], "request")
            if data["host"] not in {"codex", "claude"}:
                raise ValueError("invalid host")
            validated = True
            with open_state(db_path) as controller:
                result = AuthoringService(controller, root, specs=specs).start(**data)
            return 202, result
        if action not in SCHEMAS:
            raise ValueError("unknown authoring action")
        keys = {"task", "request_id", "expected"} | SCHEMAS[action]
        if action == "reconcile-preview":
            keys = {"task", "revision"}
        if action == "reconcile" and "handling" in data:
            keys = keys | {"handling"}
        exact(data, keys)
        text(data["task"], "task", 100)
        if action != "reconcile-preview":
            request_identity(data["request_id"])
            exact(data["expected"], BASE_KEYS)
            if data["expected"]["task"] != data["task"]:
                raise ValueError("task/base mismatch")
        _scalars(data)
        if "actions" in data and (
            not isinstance(data["actions"], list) or len(data["actions"]) > 100
        ):
            raise ValueError("invalid reconciliation actions")
        if "operation" in data:
            from harness.authoring_actions_contract import validate_planning

            validate_planning(data["operation"])
        if not db_path.is_file():
            return 404, {"error": "task database does not exist"}
        with open_state(db_path) as controller:
            service = AuthoringService(controller, root, data["task"], specs=specs)
            args = {k: v for k, v in data.items() if k != "task"}
            method = {
                "save-spec": "save_spec",
                "reconnect-jira": "reconnect_jira",
                "jira-handoff": "jira_handoff",
                "investigation-review": "investigation_review",
                "accept-investigation": "accept_investigation",
                "revise-record": "revise_record",
                "regenerate": "request_regeneration",
                "reconcile-preview": "reconcile_preview",
                "cancel-job": "cancel_job",
                "retry-job": "retry_job",
            }.get(action, action)
            validated = True
            result = getattr(service, method)(**args)
        return (
            202
            if action in {"regenerate", "retry-job"} and result.get("origin") != "attached"
            else 200
        ), result
    except AcquisitionError as error:
        return 409, {"error": str(error), "code": "acquisition", **error.public()}
    except NonAnswer as error:
        return 409, {"error": str(error), "code": "non_answer"}
    except AuthoringContextTooLarge as error:
        return 409, {"error": str(error)}
    except __import__(
        "harness.planning_session_contract", fromlist=["SessionError"]
    ).SessionError as error:
        return 409, error.public()
    except AuthoringConflict as error:
        current = None
        try:
            current = read_authoring(db_path, root, data.get("task"), specs=specs).get("base")
        except (ValueError, LookupError, OSError):
            pass
        result = {"error": str(error), "base": current}
        if error.current_target is not None:
            result["current_target"] = error.current_target
        return 409, result
    except LookupError:
        return 404, {"error": "unknown task-owned authoring record"}
    except (ValueError, TypeError, KeyError):
        return (409 if validated else 400), {"error": "Invalid authoring request or action state"}
    except sqlite3.OperationalError as error:
        from harness.dashboard.bodies import busy_failure

        return busy_failure(error)
    except OSError:
        return 503, {"error": "Authoring storage unavailable"}
