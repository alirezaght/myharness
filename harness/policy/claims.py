"""Private durable resource ownership; optional budgets never release effect obligations."""

import json

from harness.execution_contract import canonical
from harness.policy.contracts import require


def overlaps(a, b):
    return a == "." or b == "." or a == b or a.startswith(b + "/") or b.startswith(a + "/")


def claim(
    repository, resource, access, *, execution, task=None, revision=None, principals=(), writers=()
):
    # Resolve aliases before the short admission transaction, never in a database callback.
    return {
        "repository": repository,
        "resource": resource,
        "access": access,
        "producer_execution": execution,
        "producer_task": task,
        "producer_revision": str(revision) if revision is not None else None,
        "principals": tuple(sorted(principals)),
        "writers": tuple(sorted(writers)),
    }


def require_clear(conn, claims, *, own_attempt_id=None, unqualified_launch=False):
    rows = open_claims(conn)
    for existing in rows:
        if existing["attempt_id"] == own_attempt_id:
            continue
        old = json.loads(existing["claim"])
        for new in claims:
            same = old["repository"] == new["repository"]
            unknown = old["access"] == "unknown" or new["access"] == "unknown"
            if unknown or same and overlaps(old["resource"], new["resource"]):
                require(
                    not unknown
                    and old["access"] == new["access"] == "read_dependency"
                    and not (unqualified_launch and old.get("purpose") == "mutable_dependency"),
                    "ATTEMPT_NOT_EXECUTABLE",
                )


def reserve(conn, attempt_id, claims):
    require_clear(conn, claims, own_attempt_id=attempt_id)
    for position, value in enumerate(claims):
        conn.execute(
            "INSERT INTO policy_resource_claims(attempt_id,position,claim,state) "
            "VALUES (?,?,?,'reserved')",
            (attempt_id, position, canonical(value)),
        )


def revalidate(conn, attempt_id, claims):
    stored = [
        row["claim"]
        for row in conn.execute(
            "SELECT claim FROM policy_resource_claims WHERE attempt_id=? ORDER BY position",
            (attempt_id,),
        )
    ]
    require(stored == [canonical(value) for value in claims], "AUTHORITY_STALE")
    require_clear(conn, claims, own_attempt_id=attempt_id)


def transition(conn, attempt_id, state):
    require(state in {"active", "unresolved", "closed"})
    conn.execute(
        "UPDATE policy_resource_claims SET state=? WHERE attempt_id=?", (state, attempt_id)
    )


def open_claims(conn):
    return conn.execute(
        "SELECT c.*,a.state AS attempt_state,a.launcher_permit,a.remote_liability "
        "FROM policy_resource_claims c JOIN policy_attempts a ON a.id=c.attempt_id "
        "WHERE c.state!='closed' OR a.state IN ('reserved','started','unresolved') "
        "OR (a.state!='released_proven_not_started' AND (a.remote_liability='unresolved' "
        "OR EXISTS(SELECT 1 FROM policy_processes p WHERE p.attempt_id=a.id "
        "AND p.boundary_closed=0)))"
    ).fetchall()
