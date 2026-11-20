"""Short current-epoch publications; no replay or historical append writer."""

import secrets
import sqlite3
from contextlib import closing, contextmanager

from harness.db import connect, transaction, utcnow
from harness.execution_contract import canonical
from harness.execution_epoch_lock import epoch_action_lock
from harness.execution_epochs import require_active_epoch
from harness.execution_store import get_execution
from harness.reliability.contracts import TERMINALS, TOOLS
from harness.reliability.identity import verified_principal


def _current(conn, operation):
    return require_active_epoch(
        conn,
        operation.execution_id,
        expected_epoch_id=operation.epoch_id,
        expected_authority_generation=operation.authority_generation,
        mutation=True,
    )


@contextmanager
def _publication(operation):
    try:
        with closing(connect(operation.database)) as conn:
            execution = get_execution(conn, operation.execution_id)
            with epoch_action_lock(execution), transaction(conn):
                yield conn, _current(conn, operation)
    except (sqlite3.Error, OSError):
        raise ValueError("Reliability recording unavailable; outcome may be unknown") from None


def begin(operation, principal, tool):
    verified = verified_principal(operation, principal)
    if verified is None:
        return None
    source, observation, now = secrets.token_hex(16), secrets.token_hex(16), utcnow()
    with _publication(operation) as (conn, execution):
        if principal["binding"] != execution["binding"]:
            return None
        from harness.reliability.timeline_store import ensure_trace

        ensure_trace(conn, execution["id"])
        binding = execution["binding"]
        tool = tool if isinstance(tool, str) and tool in TOOLS else "unknown"
        row = {
            "observation_id": observation,
            "schema_version": 1,
            "source_kind": "operation-dispatch",
            "source_id": source,
            "execution_id": execution["id"],
            "task_id": execution["task_id"],
            "epoch_id": operation.epoch_id,
            "authority_generation": operation.authority_generation,
            **verified,
            "tool": tool,
            "policy_digest": operation.registry.policy,
            "spec_version": binding["spec_version"],
            "approval_id": execution["approval_id"],
            "started_at": now,
            "finished_at": None,
            "state": "pending",
            "code": None,
            "provenance": "runtime_observed",
        }
        conn.execute(
            "INSERT INTO operation_audit VALUES (?,?,?,?,?,?)",
            (
                source,
                execution["id"],
                canonical(
                    {
                        "principal": verified["principal_id"],
                        "tool": tool,
                        "observation_id": observation,
                    }
                ),
                "guarded",
                None,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO reliability_observations ("
            + ",".join(row)
            + ") VALUES ("
            + ",".join("?" for _ in row)
            + ")",
            tuple(row.values()),
        )
    return observation


def _outcome(state, code, result):
    outcome = {"status": "returned" if state == "succeeded" else "failed", "code": code}
    if state != "succeeded":
        outcome["error"] = code
    elif isinstance(result, dict):
        status = result.get("status")
        if isinstance(status, str) and status in {"SetupPending", "SetupUnavailable"}:
            outcome["status"] = status
    return canonical(outcome)


def finish(operation, observation_id, code, result=None, *, rejection=None):
    if observation_id is None:
        return
    state = TERMINALS[code]
    outcome = _outcome(state, code, result)
    with _publication(operation) as (conn, execution):
        row = conn.execute(
            "SELECT * FROM reliability_observations WHERE observation_id=? AND execution_id=? "
            "AND epoch_id=? AND authority_generation=?",
            (
                observation_id,
                operation.execution_id,
                operation.epoch_id,
                operation.authority_generation,
            ),
        ).fetchone()
        if row is None:
            raise ValueError("observation not found for current authority")
        if row["state"] != "pending":
            audit = conn.execute(
                "SELECT outcome FROM operation_audit WHERE id=?", (row["source_id"],)
            ).fetchone()
            if (row["state"], row["code"]) != (state, code) or audit["outcome"] != outcome:
                raise ValueError("conflicting terminal observation")
            if rejection is not None:
                from harness.reliability.rejection_sources import capture

                capture(
                    conn,
                    execution,
                    row["principal_id"],
                    rejection,
                    boundary="dispatch",
                    observation_id=observation_id,
                )
            return
        now = utcnow()
        conn.execute(
            "UPDATE reliability_observations SET state=?,code=?,finished_at=? "
            "WHERE observation_id=?",
            (state, code, now, observation_id),
        )
        conn.execute(
            "UPDATE operation_audit SET outcome=? WHERE id=?",
            (outcome, row["source_id"]),
        )
        if state in {"denied", "failed"}:
            incident_id = secrets.token_hex(16)
            conn.execute(
                "INSERT INTO reliability_incidents VALUES (?,?,1,?,'open',?,?,1)",
                (
                    incident_id,
                    observation_id,
                    "operation_denied" if state == "denied" else "operation_failed",
                    now,
                    now,
                ),
            )

            from harness.reliability.catalog import project_operation

            project_operation(conn, incident_id)
        if rejection is not None:
            from harness.reliability.rejection_sources import capture

            capture(
                conn,
                execution,
                row["principal_id"],
                rejection,
                boundary="dispatch",
                observation_id=observation_id,
            )


def reject_assignment(operation, principal, error):
    verified = verified_principal(operation, principal)
    if verified is None:
        raise ValueError("typed rejection producer unavailable")
    with _publication(operation) as (conn, execution):
        if principal["binding"] != execution["binding"]:
            raise ValueError("typed rejection producer binding differs")
        from harness.reliability.rejection_sources import capture

        return capture(conn, execution, verified["principal_id"], error, boundary="assignment_wait")
