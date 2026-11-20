"""Typed rejection receipt capture, only inside an authenticated current publication owner."""

import secrets

from harness.agent_contracts import FAMILIES, FIELDS, ContractError
from harness.db import utcnow
from harness.reliability.catalog import _insert, atomic_source


def capture(conn, execution, principal_id, error, *, boundary, observation_id=None):
    if not isinstance(error, ContractError) or boundary not in {"dispatch", "assignment_wait"}:
        raise ValueError("typed rejection source unavailable")
    feedback = error.feedback
    family = feedback.get("contract_kind")
    family = family if isinstance(family, str) and family in FAMILIES else None
    # Only schema-owned roots are durable, never an arbitrary rejected key/index/value.
    pointer = feedback.get("field_pointer")
    root = (
        pointer.split("/")[1]
        if isinstance(pointer, str) and pointer.startswith("/") and pointer != "/"
        else None
    )
    pointer = "/" + root if root in FIELDS else "/"
    with atomic_source(conn):
        if observation_id:
            row = conn.execute(
                "SELECT r.rejection_id,s.incident_id FROM contract_rejection_receipts r "
                "JOIN incident_contract_sources s USING(rejection_id) WHERE r.observation_id=?",
                (observation_id,),
            ).fetchone()
            if row:
                return row["incident_id"]
        rid, now = secrets.token_hex(16), utcnow()
        conn.execute(
            "INSERT INTO contract_rejection_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                execution["id"],
                execution["epoch_id"],
                execution["authority_generation"],
                principal_id,
                boundary,
                family,
                "INVALID_AGENT_CONTRACT",
                pointer,
                observation_id,
                now,
            ),
        )
        incident = _insert(
            conn,
            "contract_rejection",
            execution["id"],
            execution["epoch_id"],
            "invalid_handoff",
            "CONTRACT_REJECTED",
            "runtime_observed",
            now,
        )
        conn.execute(
            "INSERT INTO incident_contract_sources(incident_id,rejection_id) VALUES (?,?)",
            (incident, rid),
        )
    return incident
