"Scoped read-only safe policy facts, without authorization resources or request payloads."

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs

from harness.execution_store import get_execution
from harness.policy.composition import select
from harness.reliability.query import scope

LIMITATIONS = {
    "mutation_unit": "admitted_invocation",
    "accounting": "same_project_database",
    "tokens": None,
    "cost": None,
    "native_children": None,
    "network_restrictions": "unsupported",
    "database_restrictions": "unsupported",
    "native_delegation_prevention": "unsupported",
    "unknown_liability": "retained_without_replay",
    "coordination": "same_database_enrolled_domains_only",
    "legacy_process_closure": "unqualified",
}


def view(conn, selected, *, cursor=None, attempt=None, cap_after=None):
    execution = get_execution(conn, selected["execution"])
    from harness.native_tool_policy import ROLES

    policies = {}
    pools = set()
    for role in ("orchestrator", *ROLES):
        docs, _ = select(conn, execution, {"role": role})
        for doc in docs:
            policies[(doc["policy_id"], doc["version"])] = {
                "policy_id": doc["policy_id"],
                "version": doc["version"],
                "scope_kind": doc["scope_kind"],
                "scope_ref": doc["scope_ref"],
            }
            pools.update(item["pool_id"] for item in doc["limits"])
    pool_rows = []
    for identifier in sorted(pools):
        row = dict(
            conn.execute(
                (
                    "SELECT id,dimension,domain,maximum,consumed,reserved,revision,state FROM"
                    " policy_pools WHERE id=?"
                ),
                (identifier,),
            ).fetchone()
        )
        row["measured"] = (
            row["consumed"]
            if row["dimension"] in {"mutating_operations", "retry_attempts"}
            else None
        )
        row["unknown"] = None
        row["measurement_status"] = (
            "discrete_start_commits" if row["measured"] is not None else "unavailable"
        )
        pool_rows.append(row)
    args = [selected["execution"], selected["epoch"]]
    where = "execution_id=? AND epoch_id=?"
    if cursor:
        where += " AND rowid<?"
        args.append(cursor)
    rows = [
        dict(row)
        for row in conn.execute(
            (
                "SELECT rowid AS cursor,id,attempt_id,verdict,code,created_at FROM policy"
                "_decisions WHERE "
            )
            + where
            + " ORDER BY rowid DESC LIMIT 26",
            args,
        )
    ]
    cap_args = [selected["execution"], selected["epoch"]]
    cap_where = "c.execution_id=? AND c.epoch_id=?"
    if cap_after is not None:
        cap_where += " AND c.id>?"
        cap_args.append(cap_after)
    capabilities = [
        dict(row)
        for row in conn.execute(
            "SELECT c.id,c.uses,c.units,c.state,c.revocation_ge"
            "neration,c.decision_ref AS issuance_decision_ref,"
            "a.action_id AS revocation_action_ref,a.decision_ref AS revocation_decision_ref "
            "FROM policy_capabilities c LEFT JOIN policy_capability_actions a ON "
            "a.capability_id=c.id AND a.action='revoked' AND a."
            "revocation_generation=c.revocation_generation "
            "WHERE " + cap_where + " ORDER BY c.id LIMIT 26",
            cap_args,
        )
    ]
    cap_more = len(capabilities) > 25
    result = {
        "schema_version": 1,
        "scope": selected,
        "policies": list(policies.values()),
        "pools": pool_rows,
        "limitations": LIMITATIONS,
        "decisions": rows[:25],
        "next_cursor": str(rows[24]["cursor"]) if len(rows) > 25 else None,
        "capabilities": capabilities[:25],
        "capabilities_next_cursor": capabilities[24]["id"] if cap_more else None,
        "capabilities_has_more": cap_more,
        "capabilities_coverage": {
            "status": "partial" if cap_after is not None or cap_more else "complete",
            "returned": min(len(capabilities), 25),
            "limit": 25,
        },
    }

    if attempt:
        row = conn.execute(
            (
                "SELECT id,legacy,principal_id,role,operation,state,admitted_at,started_at,settl"
                "ed_at,outcome,deadline_wall,versions,retry_reference,policy_digest"
                ",boundary,remote_liability FROM policy_attempts WH"
                "ERE id=? AND executio"
                "n_id=? AND epoch_id=?"
            ),
            (attempt, selected["execution"], selected["epoch"]),
        ).fetchone()
        if row is None:
            raise LookupError()
        import json

        result["attempt"] = dict(row)
        result["attempt"]["coordination_coverage"] = (
            "uncoordinated_legacy" if result["attempt"].pop("legacy") else "enrolled"
        )
        result["attempt"]["policy_version_refs"] = json.loads(result["attempt"].pop("versions"))
        retry = result["attempt"].pop("retry_reference")
        result["attempt"]["retry_reference"] = (
            dict(
                zip(
                    ("al_run_id", "al_session_id", "target_role", "failure_checkpoint_ref"),
                    json.loads(retry),
                    strict=True,
                )
            )
            if retry
            else None
        )
        result["attempt"]["failed_native_invocation_id"] = None
        result["attempt"]["retry_provenance"] = "AL_role_checkpoint" if retry else None
        result["reservations"] = [
            dict(r)
            for r in conn.execute(
                (
                    "SELECT pool_id,units,charged,observed_units FROM policy_reservations WHE"
                    "RE attempt_id=?"
                ),
                (attempt,),
            )
        ]
        result["processes"] = [
            dict(r)
            for r in conn.execute(
                (
                    "SELECT id,span_id,observed_attempt_id,launch_state"
                    ",closure,parent_reaped,owned_group_absent,boundary"
                    "_closed FROM policy_p"
                    "rocesses WHERE attempt_id=?"
                ),
                (attempt,),
            )
        ]
    return result


def read_policy(database, raw_query):
    try:
        values = parse_qs(raw_query, keep_blank_values=True, strict_parsing=True)
        if (
            not {"task", "execution", "epoch"} <= values.keys()
            or not values.keys() <= {"task", "execution", "epoch", "before", "attempt", "cap_after"}
            or any(len(v) != 1 for v in values.values())
        ):
            raise ValueError()
        data = {k: v[0] for k, v in values.items()}
        if any(not v or len(v) > 128 for v in data.values()):
            raise ValueError()
        if "before" in data and not re.fullmatch("[1-9][0-9]{0,17}", data["before"]):
            raise ValueError()
        if "cap_after" in data and not re.fullmatch("[A-Za-z0-9_-]{1,128}", data["cap_after"]):
            raise ValueError()
        if "attempt" in data and not re.fullmatch("[0-9a-f]{32}", data["attempt"]):
            raise ValueError()
    except (ValueError, TypeError):
        return 400, {"error": "Invalid policy query"}
    try:
        with closing(
            sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)
        ) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            selected = scope(conn, data["task"], data["execution"], data["epoch"])
            return 200, view(
                conn,
                selected,
                cursor=int(data["before"]) if "before" in data else None,
                attempt=data.get("attempt"),
                cap_after=data.get("cap_after"),
            )
    except (ValueError, LookupError, KeyError, TypeError):
        return 404, {"error": "Policy scope unavailable"}
    except (sqlite3.Error, OSError):
        return 503, {"error": "Policy storage unavailable"}
