"""Attributed local-operator rejection of an actual published deliverable; grants no approval."""

from harness.db import transaction, utcnow
from harness.execution_artifacts import ArtifactStore, recorded_output_directory
from harness.execution_contract import canonical
from harness.execution_store import get_execution
from harness.reliability import catalog
from harness.reliability.investigation_contracts import identity, object_keys, rationale, references


def reject(conn, selected, *, request_id, target, reason, evidence):
    identity(request_id)
    catalog.selected_values(selected)
    object_keys(target, {"artifact_id", "sha256"})
    request = canonical(
        {"scope": selected, "target": target, "reason": reason, "evidence": evidence}
    )
    execution = get_execution(conn, selected["execution"])
    if execution["task_id"] != selected["task"]:
        raise LookupError("incident scope not found")
    # Snapshot-only identity validation outside SQLite: no mutable source stability claim.
    old = conn.execute(
        "SELECT * FROM incident_operator_sources WHERE task_id=? AND operator_request_id=?",
        (selected["task"], request_id),
    ).fetchone()
    if old:
        if old["document"] != request:
            raise catalog.IncidentConflict("operator request already used")
        return {"incident_id": old["incident_id"], "verdict_revision": old["verdict_revision"]}
    rationale(conn, selected, reason)
    references(conn, selected, evidence)
    store = ArtifactStore(
        recorded_output_directory(conn, execution["id"], selected["epoch"]), create=False
    )
    with store.locked(read_only=True) as index:
        record = index["records"].get(target["artifact_id"])
        if (
            not record
            or record["source"] != "native-agent-output"
            or record["sha256"] != target["sha256"]
        ):
            raise ValueError("published deliverable unavailable")
    store.buffer(target["artifact_id"], target["sha256"])
    with transaction(conn):
        # Race-safe request replay, before any new publication.
        prior = conn.execute(
            "SELECT * FROM incident_operator_sources WHERE task_id=? AND operator_request_id=?",
            (selected["task"], request_id),
        ).fetchone()
        if prior:
            if prior["document"] != request:
                raise catalog.IncidentConflict("operator request already used")
            return {
                "incident_id": prior["incident_id"],
                "verdict_revision": prior["verdict_revision"],
            }
        catalog.scope_filter(conn, selected)
        incident = catalog._insert(
            conn,
            "operator_verdict",
            execution["id"],
            selected["epoch"],
            "human_rejected_deliverable",
            "OPERATOR_REJECTED",
            "operator_reported",
            utcnow(),
        )
        conn.execute(
            "INSERT INTO incident_operator_sources VALUES (?,'operator_verdict',?,?,1,?)",
            (incident, selected["task"], request_id, request),
        )
        return {"incident_id": incident, "verdict_revision": 1}
