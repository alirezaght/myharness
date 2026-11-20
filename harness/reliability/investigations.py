"""Append-only local-operator interpretations; source facts and execution authority stay intact."""

import json
import secrets

from harness.db import transaction, utcnow
from harness.execution_contract import canonical
from harness.reliability import catalog
from harness.reliability.investigation_contracts import identity, validate_changes


def _initial(item):
    controls = []
    kind = {
        "AUTHORITY_REJECTED": "authority",
        "CONTRACT_REJECTED": "contract",
        "BUDGET_EXHAUSTED": "budget",
    }.get(item["symptom_code"])
    if kind:
        controls = [{"kind": kind, "state": "held", "evidence": [item["incident_id"]]}]
    elif item["symptom_code"] == "DISPATCH_REJECTED":
        controls = [{"kind": "dispatch", "state": "unknown", "evidence": [item["incident_id"]]}]
    return {
        "schema_version": 1,
        "incident_id": item["incident_id"],
        "facts": [item["incident_id"]],
        "classification": {
            k: item[k] for k in ("category", "classification_version", "provenance")
        },
        "hypotheses": [],
        "affected_components": [],
        "controls": controls,
        "corrective_actions": [],
        "corrections": [],
        "status": "open",
        "rationale": None,
    }


def history(conn, selected, incident_id, *, before_revision=None, limit=25):
    item = catalog.detail(conn, selected, incident_id)
    if (
        type(limit) is not int
        or not 1 <= limit <= 100
        or (
            before_revision is not None
            and (type(before_revision) is not int or before_revision < 1)
        )
    ):
        raise ValueError("invalid investigation page")
    rows = conn.execute(
        (
            "SELECT * FROM incident_investigation_revisions WHERE incident_id="
            "? AND (? IS NULL OR revision<?) ORDER BY revision DESC LIMIT ?"
        ),
        (incident_id, before_revision, before_revision, limit + 1),
    ).fetchall()
    values = [_decode(row) for row in rows[:limit]]
    return {
        "schema_version": 1,
        "incident": item,
        "items": values,
        "next_revision": values[-1]["revision"] if len(rows) > limit else None,
    }


def _decode(row):
    return {
        k: row[k]
        for k in (
            "revision_id",
            "revision",
            "parent_revision_id",
            "actor_kind",
            "request_id",
            "created_at",
        )
    } | {"document": json.loads(row["document"])}


def current(conn, selected, incident_id):
    item = catalog.detail(conn, selected, incident_id)
    row = conn.execute(
        (
            "SELECT r.* FROM incident_investigation_heads h JOIN incident_inve"
            "stigation_revisions r USING(revision_id) WHERE h.incident_id=?"
        ),
        (incident_id,),
    ).fetchone()
    return _decode(row) if row else {"revision": 0, "revision_id": None, "document": _initial(item)}


def revise(conn, selected, incident_id, *, expected_revision, request_id, changes, repository=None):
    """Caller establishes operator boundary; historical context grants no runtime permission."""
    identity(request_id)
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("invalid investigation revision")
    request = canonical(
        {"scope": selected, "expected_revision": expected_revision, "changes": changes}
    )
    if len(request.encode()) > 32768:
        raise ValueError("investigation request too large")
    with transaction(conn):
        item = catalog.detail(conn, selected, incident_id)
        prior = conn.execute(
            "SELECT * FROM incident_investigation_revisions WHERE incident_id=? AND request_id=?",
            (incident_id, request_id),
        ).fetchone()
        if prior:
            if prior["request"] != request:
                raise catalog.IncidentConflict("investigation request already used")
            return _decode(prior)
        if item["indexing"] != "indexed":
            raise ValueError("explicit legacy projection required")
        validate_changes(conn, selected, changes, repository=repository)
        parent = current(conn, selected, incident_id)
        if parent["revision"] != expected_revision:
            raise catalog.IncidentConflict("investigation revision changed")
        document = parent["document"] | changes
        # Human corrections are cumulative; old revisions and observed facts never change.
        document["corrections"] = parent["document"]["corrections"] + changes.get("corrections", [])
        if len(document["corrections"]) > 200:
            raise ValueError("investigation correction history requires a new investigation")
        revision, rid = expected_revision + 1, secrets.token_hex(16)
        conn.execute(
            (
                "INSERT INTO incident_investigation_revisions VALUES (?,?,?,?,1,'l"
                "ocal_dashboard_operator',?,?,?,?)"
            ),
            (
                rid,
                incident_id,
                revision,
                parent["revision_id"],
                request_id,
                request,
                utcnow(),
                canonical(document),
            ),
        )
        conn.execute(
            (
                "INSERT INTO incident_investigation_heads VALUES (?,?,?) ON CONFLI"
                "CT(incident_id) DO UPDATE SET revision=excluded.revision,revision"
                "_id=excluded.revision_id"
            ),
            (incident_id, revision, rid),
        )
        return _decode(
            conn.execute(
                "SELECT * FROM incident_investigation_revisions WHERE revision_id=?", (rid,)
            ).fetchone()
        )
