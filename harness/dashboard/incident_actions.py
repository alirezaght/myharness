"""Local dashboard operator actions, never native-tool or epoch-writer authority."""

import sqlite3
from contextlib import closing

from harness.dashboard.incident_scope import repository_scope, selected_scope
from harness.db import connect
from harness.execution_contract import canonical, loads
from harness.reliability import (
    catalog,
    grouping,
    investigations,
    legacy_projection,
    operator_verdicts,
)
from harness.reliability.investigation_contracts import object_keys


def incident_action(database, root, body, action):
    try:
        data = loads(body, max_bytes=32768)
        fields = {
            "investigate": {"incident", "expected_revision", "request_id", "changes"},
            "suggest": {"limit", "cursor"},
            "group-decision": {"group", "expected_revision", "request_id", "decision"},
            "project": {"expected_revision", "request_id", "limit"},
            "verdict": {"request_id", "target", "reason", "evidence"},
        }[action]
        object_keys(data, {"schema_version", "scope"} | fields)
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError()
        selected = selected_scope(data["scope"])
        with closing(connect(database)) as conn:
            repository_scope(conn, selected, root)

            # Source facts/membership are immutable; validate only this bounded request/window.
            # No repository file or external probe is performed inside the append transaction.
            def check_evidence(value, depth=0):
                if depth > 12:
                    raise ValueError("incident request nesting exceeds limit")
                if isinstance(value, dict):
                    for key, nested in value.items():
                        if key in {"supporting", "contradicting", "evidence"} and isinstance(
                            nested, list
                        ):
                            for ref in nested:
                                item = catalog.detail(conn, selected, ref)
                                repository_scope(
                                    conn, selected, root, execution=item["execution_id"]
                                )
                        else:
                            check_evidence(nested, depth + 1)
                elif isinstance(value, list):
                    for nested in value:
                        check_evidence(nested, depth + 1)

            check_evidence(data.get("changes", data.get("decision", {})))
            if action == "investigate":
                item = catalog.detail(conn, selected, data["incident"])
                repository_scope(conn, selected, root, execution=item["execution_id"])
                result = investigations.revise(
                    conn,
                    selected,
                    data["incident"],
                    expected_revision=data["expected_revision"],
                    request_id=data["request_id"],
                    changes=data["changes"],
                    repository=root,
                )
            elif action == "suggest":
                window = catalog.list_incidents(
                    conn, selected, limit=data["limit"], cursor=data["cursor"]
                )
                for item in window["items"]:
                    repository_scope(conn, selected, root, execution=item["execution_id"])
                result = grouping.suggest(
                    conn,
                    selected,
                    limit=data["limit"],
                    cursor=data["cursor"],
                    expected_window=canonical(window),
                )
            elif action == "group-decision":
                group = grouping.get(conn, selected, data["group"])
                for item in group["member_metadata"].values():
                    repository_scope(conn, selected, root, execution=item["execution_id"])
                if isinstance(data["decision"], dict) and "target_group" in data["decision"]:
                    target = grouping.get(conn, selected, data["decision"]["target_group"])
                    for item in target["member_metadata"].values():
                        repository_scope(conn, selected, root, execution=item["execution_id"])
                result = grouping.decide(
                    conn,
                    selected,
                    data["group"],
                    expected_revision=data["expected_revision"],
                    request_id=data["request_id"],
                    decision=data["decision"],
                )
            elif action == "project":
                result = legacy_projection.project_legacy_batch(
                    conn,
                    selected,
                    expected_revision=data["expected_revision"],
                    request_id=data["request_id"],
                    limit=data["limit"],
                )
            else:
                result = operator_verdicts.reject(
                    conn,
                    selected,
                    request_id=data["request_id"],
                    target=data["target"],
                    reason=data["reason"],
                    evidence=data["evidence"],
                )
            return 200, {
                "schema_version": 1,
                "scope": selected,
                "result": result,
                "authority": "local_dashboard_operator; no execution or approval grant",
            }
    except catalog.IncidentConflict:
        return 409, {"error": "Incident revision or request conflict; reload before editing"}
    except LookupError:
        return 404, {"error": "Incident scope or record not found"}
    except (ValueError, KeyError, TypeError, RecursionError):
        return 409, {"error": "Invalid incident action or unavailable supporting evidence"}
    except (OSError, sqlite3.Error):
        return 503, {"error": "Incident storage unavailable"}
