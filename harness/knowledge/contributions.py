"""Supervisor ingestion of completed native outbox reports as attributed inference."""

import json
from pathlib import Path

from harness.db import utcnow
from harness.execution_authority import require_authored_report
from harness.execution_handoff import read_handoff
from harness.execution_store import get_execution
from harness.knowledge.contribution_contract import extract
from harness.knowledge.models import (
    EXTRACTOR_VERSION,
    Extraction,
    PreparedSnapshot,
    SourceInventory,
    SourceVersion,
    digest,
    identity,
)
from harness.knowledge.policy import current_operator_scope, validate_scope
from harness.knowledge.sources import read_artifact, read_source
from harness.knowledge.store import ensure_project, publish_snapshot


def report_metadata(directory):
    """Read protected outbox metadata only; do not inspect unrelated report contents."""
    directory = Path(directory)
    try:
        index = json.loads(read_source(directory, "index.json", 16 * 1024 * 1024))
    except FileNotFoundError:
        return {}
    return index.get("records", {})


def _kind(state, producer):
    role = producer["role"]
    if role in {"al-engineer", "al-builder"}:
        return "implementer-report"
    if role in {"al-designer", "al-designer-fallback"}:
        return "pseudocode"
    if role == "al-scout":
        return "scout-report"
    phase = None
    for event in state["integration"].get("outbox", []):
        if event["kind"] == "phase.changed":
            phase = event["payload"]["phase"]
        if event["kind"] == "agent.registered" and event["payload"]["id"] == producer["id"]:
            return "finding" if phase == "REVIEW" else "plan" if phase == "PLANNING" else "other"
    return "other"


def current_bytes(conn, scope, observation):
    execution = get_execution(conn, observation["execution_id"])
    envelope = read_handoff(execution["handoff_path"], execution["handoff_hash"])
    if identity(str(Path(envelope["repository"]).resolve())) != scope.project_id:
        raise PermissionError("contribution belongs to another project")
    root = Path(execution["handoff_path"]).parent / "operations/drafts"
    record = report_metadata(root).get(observation["artifact_id"])
    if (
        not record
        or record["source"] != "native-agent-output"
        or (
            record["sha256"].removeprefix("sha256:") != observation["artifact_sha256"]
            or record.get("owner") != observation["producer"]
        )
    ):
        raise ValueError("contribution artifact provenance differs")
    return read_artifact(root, record, maximum=262144)


def ingest(operation, controller, principal, task_id, resource):
    if principal["role"] != "orchestrator":
        raise PermissionError("only the supervisor may ingest completed reports")
    execution, state = operation.authority(controller, principal, operation="read")
    row = require_authored_report(execution, state, task_id, resource)
    producer = state["integration"]["agents"][row["owner"]]
    root = Path(execution["handoff_path"]).parent / "operations/drafts"
    record = report_metadata(root).get(row["artifact_id"])
    if not record:
        raise ValueError("completed native report artifact unavailable")
    raw = read_artifact(root, record, maximum=262144)
    repository = Path(operation.envelope["repository"])
    scope = current_operator_scope(controller.conn, repository, task_id=execution["task_id"])
    ensure_project(controller.conn, scope)
    project = validate_scope(controller.conn, scope)
    sha = digest(raw)
    existing = controller.conn.execute(
        "SELECT * FROM knowledge_contributions WHERE project_id=? AND artifact_sha256=?",
        (scope.project_id, sha),
    ).fetchone()
    if existing:
        if (existing["execution_id"], existing["producer_principal"]) != (
            execution["id"],
            row["owner"],
        ):
            raise ValueError("identical report content has distinct producer provenance")
        return {"status": "unchanged", "contribution_id": existing["contribution_id"]}
    visibility = "task:" + execution["task_id"]
    locator = "contribution:" + sha
    source_id = identity(scope.project_id, locator)
    observation = {
        "execution_id": execution["id"],
        "artifact_id": row["artifact_id"],
        "artifact_sha256": sha,
        "producer": row["owner"],
        "visibility": visibility,
    }
    source = SourceVersion(
        source_id,
        identity(source_id, sha, EXTRACTOR_VERSION, "contribution", visibility),
        locator,
        "contribution",
        sha,
        raw,
        observation,
        "contribution",
        visibility,
    )
    kind = _kind(state, producer)
    assertions = extract(raw, row["role"], kind)
    expected = project["active_snapshot_id"]
    parent = controller.conn.execute(
        "SELECT observation_json,enumeration_complete FROM knowledge_snapshots WHERE snapshot_id=?",
        (expected,),
    ).fetchone()
    inventory = SourceInventory(
        (source,),
        True,
        () if parent else ({"status": "repository-not-indexed"},),
        json.loads(parent[0]) if parent else {},
    )
    identifier = identity(scope.project_id, sha)
    metadata = (
        identifier,
        scope.project_id,
        source_id,
        source.version_id,
        execution["id"],
        task_id,
        execution["epoch_id"],
        row["owner"],
        row["role"],
        kind,
        sha,
        state["integration"]["graph_revision"],
        utcnow(),
    )
    snapshot = publish_snapshot(
        controller.conn,
        PreparedSnapshot(
            scope,
            inventory,
            (Extraction(tuple(assertions)),),
            scope.policy_generation,
            carry_forward=True,
            names_complete=bool(parent and parent["enumeration_complete"]),
            contribution=metadata,
        ),
        expected_active=expected,
    )
    return {"status": "published", "contribution_id": identifier, "snapshot_id": snapshot}
