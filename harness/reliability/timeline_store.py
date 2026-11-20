"""Safe references written only inside existing epoch-fenced producer transactions."""

import re
import secrets

from harness.db import utcnow
from harness.reliability.timeline_contracts import SEMANTIC

_SEMANTIC_REFERENCE_FIELDS = {
    "run.started": None,
    "run.ended": None,
    "agent.registered": "id",
    "agent.updated": "id",
    "task.registered": "id",
    "task.updated": "id",
    "test.started": "id",
    "test.result": "id",
    "decision.acknowledged": "delivery_id",
    "output.authored": "task_id",
    "output.invalidated": "task_id",
}


def ensure_trace(conn, execution_id, now=None):
    conn.execute(
        "INSERT OR IGNORE INTO reliability_traces VALUES (?,?,?)",
        (secrets.token_hex(16), execution_id, now or utcnow()),
    )
    return conn.execute(
        "SELECT trace_id FROM reliability_traces WHERE execution_id=?", (execution_id,)
    ).fetchone()[0]


def link(
    conn,
    execution,
    *,
    source_kind,
    source_id,
    event_kind,
    provenance,
    principal_id=None,
    parent_observation_id=None,
    parent_span_id=None,
    reference_id=None,
    bundle_hash=None,
    snapshot_id=None,
    receipt_id=None,
    at=None,
):
    trace = ensure_trace(conn, execution["id"])
    row = {
        "trace_id": trace,
        "execution_id": execution["id"],
        "epoch_id": execution["epoch_id"],
        "source_kind": source_kind,
        "source_id": source_id,
        "event_kind": event_kind,
        "provenance": provenance,
        "principal_id": principal_id,
        "parent_observation_id": parent_observation_id,
        "parent_span_id": parent_span_id,
        "reference_id": reference_id,
        "bundle_hash": bundle_hash,
        "snapshot_id": snapshot_id,
        "receipt_id": receipt_id,
    }
    old = conn.execute(
        "SELECT * FROM reliability_timeline_links WHERE execution_id=? AND epoch_id=? "
        "AND source_kind=? AND source_id=? AND event_kind=?",
        (execution["id"], execution["epoch_id"], source_kind, source_id, event_kind),
    ).fetchone()
    if old:
        if any(old[key] != value for key, value in row.items()):
            raise ValueError("conflicting timeline reference")
        return old["link_id"]
    lid = secrets.token_hex(16)
    row = {"link_id": lid, **row, "created_at": at or utcnow()}
    conn.execute(
        "INSERT INTO reliability_timeline_links ("
        + ",".join(row)
        + ") VALUES ("
        + ",".join("?" for _ in row)
        + ")",
        tuple(row.values()),
    )
    return lid


def semantic_link(conn, execution, event):
    kind = event["kind"]
    if kind not in _SEMANTIC_REFERENCE_FIELDS:
        return
    family = kind.split(".")[0]
    # Only this exact kind's validated reference field belongs in the safe projection.
    field = _SEMANTIC_REFERENCE_FIELDS[kind]
    reference = event["payload"][field] if field is not None else None
    link(
        conn,
        execution,
        source_kind="semantic",
        source_id=event["event_id"],
        event_kind=SEMANTIC[family],
        provenance="validated_semantic_report",
        reference_id=reference,
    )


def retrieval_link(operation, observation_id, returned):
    if observation_id is None or not isinstance(returned, dict):
        return
    bundle = returned.get("bundle_hash")
    if not isinstance(bundle, str) or not re.fullmatch(r"[0-9a-f]{64}", bundle):
        return
    from harness.reliability.store import _publication

    with _publication(operation) as (conn, execution):
        parent = conn.execute(
            "SELECT principal_id FROM reliability_observations WHERE observation_id=? "
            "AND execution_id=? AND epoch_id=? AND authority_generation=?",
            (
                observation_id,
                execution["id"],
                execution["epoch_id"],
                execution["authority_generation"],
            ),
        ).fetchone()
        if parent is None:
            raise ValueError("retrieval observation scope differs")
        # Only references actually issued by the authorized query are eligible.
        receipt = conn.execute(
            "SELECT receipt_id,snapshot_id FROM knowledge_receipts WHERE bundle_hash=? "
            "ORDER BY rowid DESC LIMIT 1",
            (bundle,),
        ).fetchone()
        if receipt is None:
            return
        link(
            conn,
            execution,
            source_kind="retrieval",
            source_id=observation_id,
            event_kind="retrieval.bundle",
            provenance="runtime_observed",
            principal_id=parent["principal_id"],
            parent_observation_id=observation_id,
            bundle_hash=bundle,
            snapshot_id=receipt["snapshot_id"],
            receipt_id=receipt["receipt_id"],
        )


def assignment_link(operation, principal, returned):
    from harness.reliability.identity import verified_principal
    from harness.reliability.store import _publication

    verified = verified_principal(operation, principal)
    if verified is None or returned.get("status") != "available":
        return
    with _publication(operation) as (conn, execution):
        receipt = conn.execute(
            "SELECT receipt_id,snapshot_id,bundle_hash FROM knowledge_receipts WHERE receipt_id=? "
            "AND bundle_hash=?",
            (returned["receipt_id"], returned["bundle_hash"]),
        ).fetchone()
        if receipt is None:
            return
        link(
            conn,
            execution,
            source_kind="retrieval",
            source_id=receipt["receipt_id"],
            event_kind="retrieval.bundle",
            provenance="runtime_observed",
            principal_id=verified["principal_id"],
            bundle_hash=receipt["bundle_hash"],
            snapshot_id=receipt["snapshot_id"],
            receipt_id=receipt["receipt_id"],
        )
