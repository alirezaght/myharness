"""Review context starts from validated issued packet bindings, never a general corpus."""

import json
from pathlib import Path

from harness.db import transaction, utcnow
from harness.execution_hosts import al_source
from harness.knowledge.contributions import report_metadata
from harness.knowledge.models import canonical, digest
from harness.knowledge.sources import read_artifact


def issued(operation, execution, state, stage):
    review = state.get("review")
    expected_stage = "fresh" if stage == "fresh" else "comparison"
    if state["phase"] != "REVIEW" or not review or review.get("stage") != expected_stage:
        raise PermissionError("knowledge:review-stage-closed")
    reference = review.get("packets", {}).get(stage)
    if not reference:
        raise PermissionError("knowledge:review-packet-not-issued")
    root = (
        Path(al_source(execution, operation.envelope)).parent
        / execution["al_session_id"]
        / "artifacts"
    )
    packet = json.loads(read_artifact(root, reference, maximum=32 * 1024 * 1024))
    attempt_ref = review.get("attempts", {}).get(stage)
    if not attempt_ref:
        raise PermissionError("knowledge:review-attempt-not-issued")
    attempt = json.loads(read_artifact(root, attempt_ref))
    if (
        packet.get("stage") != stage
        or packet.get("review_id") != review["id"]
        or packet.get("candidate_hash") != review["candidate"]["fingerprint"]
        or packet.get("candidate") != review["candidate"]
        or packet.get("task_hash") != review["task_hash"]
        or packet.get("attempt") != attempt
        or attempt.get("stage") != stage
        or attempt.get("role") != "al-architect"
    ):
        raise PermissionError("knowledge:review-packet-mismatch")
    if packet.get("task_package") != json.loads(
        read_artifact(root, state["task_package"], maximum=32 * 1024 * 1024)
    ):
        raise PermissionError("knowledge:review-task-package-mismatch")
    return packet, reference, root


def resolve(operation, controller, execution, state, scope, stage):
    packet, reference, root = issued(operation, execution, state, stage)
    project, conn = scope.project_id, controller.conn
    row = conn.execute(
        "SELECT * FROM knowledge_review_projections WHERE project_id=? AND packet_hash=?",
        (project, reference["sha256"]),
    ).fetchone()
    if row and (
        row["execution_id"] != execution["id"] or row["review_id"] != state["review"]["id"]
    ):
        raise PermissionError("knowledge:review-packet-mismatch")
    snapshot = (
        row["snapshot_id"]
        if row
        else conn.execute(
            "SELECT active_snapshot_id FROM knowledge_projects WHERE project_id=?", (project,)
        ).fetchone()[0]
    )
    if not snapshot:
        raise PermissionError("knowledge:not-indexed")
    # Explicit candidate bindings are the starting set, including their exact retained hashes.
    bindings = [
        (r["path"], r["worktree"]["sha256"])
        for r in packet["candidate"]["files"]
        if r.get("worktree", {}).get("type") == "file" and r["worktree"].get("sha256")
    ]
    forbidden = {
        r["sha256"].removeprefix("sha256:")
        for r in report_metadata(
            Path(execution["handoff_path"]).parent / "operations/drafts"
        ).values()
        if r.get("source") == "native-agent-output"
    }
    forbidden.update(
        r[0]
        for r in conn.execute(
            "SELECT artifact_sha256 FROM knowledge_contributions WHERE project_id=?", (project,)
        )
    )
    permitted = set()
    for locator, sha in bindings:
        if stage == "fresh" and sha in forbidden:
            continue
        permitted.update(
            r[0]
            for r in conn.execute(
                "SELECT m.version_id FROM knowledge_snapshot_sources m JOIN "
                "knowledge_sources s USING(source_id) "
                "JOIN knowledge_source_versions v USING(version_id) JOIN "
                "knowledge_source_class c USING(source_id) "
                "WHERE m.snapshot_id=? AND m.membership_state='present' AND s.project_id=? "
                "AND s.locator=? AND v.content_sha256=? AND c.material_class='repository'",
                (snapshot, project, locator, sha),
            )
        )
    if stage == "compare":
        refs = packet.get("approved_inputs", {}).get("references", {})
        if refs != state["review"].get("comparison_inputs"):
            raise PermissionError("knowledge:comparison-inputs-mismatch")
        allowed = set()
        for name in ("plan", "pseudocode"):
            ref = refs.get(name)
            if ref:
                raw = read_artifact(root, ref)
                if packet["approved_inputs"]["contents"].get(name) != raw.decode():
                    raise PermissionError("knowledge:comparison-inputs-mismatch")
                allowed.add(ref["sha256"])
        comparisons = packet.get("comparison_artifacts", [])
        if comparisons != state.get("comparison_artifacts", []):
            raise PermissionError("knowledge:comparison-artifacts-mismatch")
        for ref in comparisons:
            read_artifact(root, ref)
            allowed.add(ref["sha256"])
        fresh = state["review"].get("fresh")
        if fresh:
            if json.loads(read_artifact(root, fresh)) != packet.get("fresh"):
                raise PermissionError("knowledge:comparison-fresh-report-mismatch")
            allowed.add(fresh["sha256"])
        # Only comparison-explicit artifact hashes admit contributions, never role-wide grants.
        for sha in sorted(allowed):
            permitted.update(
                r[0]
                for r in conn.execute(
                    "SELECT m.version_id FROM knowledge_snapshot_sources m JOIN "
                    "knowledge_contributions c USING(version_id) "
                    "WHERE m.snapshot_id=? AND m.membership_state='present' AND c.project_id=? "
                    "AND c.execution_id=? AND c.artifact_sha256=?",
                    (snapshot, project, execution["id"], sha),
                )
            )
    if row:
        frozen = {
            r[0]
            for r in conn.execute(
                "SELECT version_id FROM knowledge_review_projection_sources WHERE "
                "project_id=? AND packet_hash=?",
                (project, reference["sha256"]),
            )
        }
        permitted &= frozen
    sha = digest(canonical(sorted(permitted)).encode())
    with transaction(conn):
        conn.execute(
            "INSERT INTO knowledge_review_projections VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(project_id,packet_hash) DO UPDATE SET "
            "policy_generation=excluded.policy_generation,"
            "permitted_digest=excluded.permitted_digest",
            (
                project,
                reference["sha256"],
                execution["id"],
                state["review"]["id"],
                stage,
                snapshot,
                scope.policy_generation,
                sha,
                utcnow(),
            ),
        )
        conn.execute(
            "DELETE FROM knowledge_review_projection_sources WHERE project_id=? AND packet_hash=?",
            (project, reference["sha256"]),
        )
        conn.executemany(
            "INSERT INTO knowledge_review_projection_sources VALUES (?,?,?)",
            [(project, reference["sha256"], v) for v in sorted(permitted)],
        )
    return reference["sha256"], state["review"]["id"], snapshot, frozenset(permitted), sha
