"""Local operator invokes exact acknowledged developer resources, never an approved flag."""

import sqlite3
from contextlib import closing

from harness.controller import HarnessController
from harness.db import connect
from harness.execution_contract import loads
from harness.execution_store import get_execution
from harness.policy import capabilities, configuration
from harness.policy.contracts import PolicyError, number, require


def policy_action(database, body, action):
    try:
        data = loads(body, max_bytes=4096)
        require(
            isinstance(data, dict)
            and set(data) == {"task", "execution", "epoch", "generation", "decision", "revision"}
        )
        require(
            all(
                isinstance(data[k], str) and 0 < len(data[k]) <= 128
                for k in ("task", "execution", "epoch", "decision")
            )
        )
        number(data["generation"])
        number(data["revision"])
        with closing(connect(database)) as conn:
            execution = get_execution(conn, data["execution"])
            require(execution["task_id"] == data["task"], "AUTHORITY_STALE")
            controller = HarnessController(conn)
            if action == "install":
                result = configuration.install(
                    controller,
                    data["execution"],
                    data["decision"],
                    data["revision"],
                    data["epoch"],
                    data["generation"],
                )
            elif action == "capability":
                result = capabilities.issue(
                    controller,
                    data["execution"],
                    data["decision"],
                    data["epoch"],
                    data["generation"],
                )
            elif action == "workflow":
                result = configuration.register_workflow(
                    controller,
                    data["execution"],
                    data["decision"],
                    data["epoch"],
                    data["generation"],
                )
            elif action == "revoke":
                result = capabilities.revoke(
                    controller,
                    data["execution"],
                    data["decision"],
                    data["revision"],
                    data["epoch"],
                    data["generation"],
                )
            else:
                raise ValueError()
            return 200, {"schema_version": 1, "result": result}
    except PolicyError as error:
        return 409, {"error": error.code}
    except (ValueError, TypeError, LookupError, KeyError):
        return 409, {"error": "Policy action refused"}
    except (sqlite3.Error, OSError):
        return 503, {"error": "Policy storage unavailable"}
