"""Explicit bounded operator indexing; startup and queries never call this service."""

import json

from harness.db import transaction
from harness.execution_contract import canonical
from harness.reliability.catalog import IncidentConflict, project_operation, selected_values
from harness.reliability.query import scope


def pending(conn, selected):
    return conn.execute(
        "SELECT i.incident_id FROM reliability_incidents i JOIN reliability_observations o "
        "USING(observation_id) WHERE o.task_id=? AND o.execution_id=? AND o.epoch_id=? "
        "AND NOT EXISTS (SELECT 1 FROM incident_operation_sources s "
        "WHERE s.legacy_incident_id=i.incident_id) ORDER BY i.rowid LIMIT 101",
        selected_values(selected),
    ).fetchall()


def project_legacy_batch(conn, selected, *, expected_revision, request_id, limit=100):
    # The HTTP caller must establish configured local operator ownership first.
    from harness.reliability.investigation_contracts import identity

    identity(request_id)
    if (
        type(expected_revision) is not int
        or expected_revision < 0
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise ValueError("invalid projection request")
    values = selected_values(selected)
    request = canonical({"scope": selected, "expected_revision": expected_revision, "limit": limit})
    with transaction(conn):
        scope(conn, *values)
        prior = conn.execute(
            "SELECT * FROM incident_projection_requests WHERE task_id=? AND execution_id=? "
            "AND epoch_id=? AND request_id=?",
            (*values, request_id),
        ).fetchone()
        if prior:
            if prior["request"] != request:
                raise IncidentConflict("projection request already used")
            return json.loads(prior["receipt"])
        row = conn.execute(
            (
                "SELECT revision FROM incident_projection_checkpoints WHERE task_i"
                "d=? AND execution_id=? AND epoch_id=?"
            ),
            values,
        ).fetchone()
        revision = row[0] if row else 0
        if revision != expected_revision:
            raise IncidentConflict("projection checkpoint changed")
        rows = pending(conn, selected)[:limit]
        for row in rows:
            project_operation(conn, row[0])
        receipt = {
            "schema_version": 1,
            "projected_count": len(rows),
            "checkpoint_revision": revision + 1,
            "more_as_of_commit": bool(pending(conn, selected)),
            "coverage": "observed within this transaction; later inserts may be unindexed",
        }
        conn.execute(
            (
                "INSERT INTO incident_projection_checkpoints VALUES (?,?,?,?) ON C"
                "ONFLICT(task_id,execution_id,epoch_id) DO UPDATE SET revision=exc"
                "luded.revision"
            ),
            (*values, revision + 1),
        )
        conn.execute(
            "INSERT INTO incident_projection_requests VALUES (?,?,?,?,?,?)",
            (*values, request_id, request, canonical(receipt)),
        )
        return receipt
