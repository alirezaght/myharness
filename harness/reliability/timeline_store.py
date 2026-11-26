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


def retrieval_link(operation, observation_id, returned, *, scope=None):
    if observation_id is None or scope is None or not isinstance(returned, dict):
        return
    bundle = returned.get("bundle_hash")
    receipt_id = returned.get("receipt_id")
    if not isinstance(bundle, str) or not re.fullmatch(r"[0-9a-f]{64}", bundle):
        return
    if not isinstance(receipt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", receipt_id):
        return
    from harness.knowledge.policy import validate_scope
    from harness.reliability.store import _publication

    with _publication(operation) as (conn, execution):
        validate_scope(conn, scope)
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
        if parent is None or (
            scope.principal_id != parent["principal_id"]
            or scope.execution_id != execution["id"]
            or scope.epoch_id != execution["epoch_id"]
            or scope.authority_generation != execution["authority_generation"]
        ):
            raise ValueError("retrieval observation scope differs")
        # Resolve the issued pair within the same validated delivery scope, never a later row.
        receipt = _receipt(conn, scope, bundle, receipt_id)
        if receipt is not None and scope.snapshot_id not in {None, receipt["snapshot_id"]}:
            return
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


def _receipt(conn, scope, bundle, receipt_id):
    from harness.knowledge.policy import policy_digest

    return conn.execute(
        "SELECT r.*,b.payload FROM knowledge_receipts r "
        "JOIN knowledge_bundles b ON b.bundle_hash=r.bundle_hash "
        "JOIN knowledge_snapshots s ON s.snapshot_id=r.snapshot_id "
        "AND s.project_id=r.project_id "
        "JOIN knowledge_bundle_scope k ON k.bundle_hash=b.bundle_hash "
        "WHERE r.receipt_id=? AND r.bundle_hash=? AND r.project_id=? "
        "AND b.project_id=r.project_id AND b.snapshot_id=r.snapshot_id "
        "AND r.policy_digest=? AND b.policy_digest=r.policy_digest "
        "AND r.purpose=? AND b.purpose=r.purpose AND k.purpose=r.purpose "
        "AND k.principal_id=? AND k.execution_id=? AND k.policy_generation=? "
        "AND k.packet_hash IS ? AND b.invalidated_at IS NULL AND b.payload!='' "
        "AND NOT EXISTS (SELECT 1 FROM knowledge_bundle_state x "
        "WHERE x.bundle_hash=b.bundle_hash)",
        (
            receipt_id,
            bundle,
            scope.project_id,
            policy_digest(scope),
            scope.purpose,
            scope.principal_id,
            scope.execution_id,
            scope.policy_generation,
            scope.packet_hash,
        ),
    ).fetchone()


def assignment_link(operation, principal, returned):
    """Final source reads need their own query policy Attempt after retrieval settles."""
    from contextlib import closing
    from pathlib import Path

    from harness.controller import HarnessController
    from harness.db import connect
    from harness.knowledge.delivery import binding
    from harness.knowledge.policy import derive_scope
    from harness.policy.context import scope as policy_scope
    from harness.policy.context import start_effect
    from harness.policy.resources import prepare

    with operation.lock, closing(connect(operation.database)) as conn:
        controller = HarnessController(conn)
        execution, state = operation.authority(controller, principal, operation="read")
        scope = derive_scope(
            controller,
            execution,
            principal,
            state,
            Path(operation.envelope["repository"]),
            operation=operation,
        )
        expected = binding(operation, controller, principal)
        attempt = prepare(operation, controller, principal, "knowledge_query", {}, state)
        with policy_scope(attempt):
            try:
                start_effect()
                _assignment_link(operation, principal, returned, scope, expected)
            except BaseException:
                if attempt:
                    attempt.owner_finished(outcome="failed")
                raise
            else:
                if attempt:
                    attempt.owner_finished()


def _assignment_link(operation, principal, returned, scope, expected):
    """Record exact prepared server-return bytes, never transport receipt or model use."""
    from harness.controller import HarnessController
    from harness.knowledge.delivery import binding, size_estimate, validate_return
    from harness.knowledge.models import canonical, digest
    from harness.knowledge.policy import validate_scope
    from harness.reliability.identity import verified_principal
    from harness.reliability.store import _publication

    verified = verified_principal(operation, principal)
    if verified is None or returned.get("status") != "available":
        raise ValueError("knowledge delivery requires a verified principal")
    with _publication(operation) as (conn, execution):
        controller = HarnessController(conn)
        structural = binding(operation, controller, principal, include_handoff=False)
        if structural != {k: v for k, v in expected.items() if k != "handoff"}:
            raise ValueError("knowledge assignment changed before publication")
        project = validate_scope(conn, scope)
        receipt = _receipt(conn, scope, returned["bundle_hash"], returned["receipt_id"])
        if receipt is None or project["active_snapshot_id"] != receipt["snapshot_id"]:
            raise ValueError("knowledge delivery receipt is stale or foreign")
        validate_return(conn, scope, returned["context"], receipt)
        raw = canonical(returned["context"]).encode()
        meta = returned["presentation"]
        import json

        if (
            meta["sha256"] != digest(raw)
            or meta["size"] != len(raw)
            or meta["size_estimate"] != size_estimate(json.loads(receipt["payload"]))
        ):
            raise ValueError("knowledge presentation identity differs")
        link(
            conn,
            execution,
            source_kind="retrieval",
            source_id=receipt["receipt_id"],
            event_kind="retrieval.bundle",
            provenance="runtime_observed",
            principal_id=verified["principal_id"],
            bundle_hash=returned["bundle_hash"],
            snapshot_id=receipt["snapshot_id"],
            receipt_id=receipt["receipt_id"],
            reference_id=f"await_assignment-return-prepared:sha256:{meta['sha256']}:bytes:{len(raw)}",
        )
