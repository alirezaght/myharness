"""Transactional immutable graph publication in the existing Harness database."""

import secrets

from harness.db import transaction, utcnow
from harness.knowledge.models import (
    EXTRACTOR_VERSION,
    MAX_REFRESH_BYTES,
    MAX_SOURCE_BYTES,
    PreparedSnapshot,
    canonical,
    digest,
    identity,
)


class SnapshotConflict(ValueError):
    pass


def record_refresh_attempt(conn, project_id, status, coverage):
    """Caller holds the publication or failed-attempt transaction."""
    conn.execute(
        "INSERT INTO knowledge_refresh_attempts VALUES (?,?,?,?,?)",
        (secrets.token_hex(16), project_id, status, canonical(coverage), utcnow()),
    )


def ensure_project(conn, scope):
    root = str(scope.repository.resolve())
    conn.execute(
        "INSERT OR IGNORE INTO knowledge_projects(project_id,repository_identity) VALUES (?,?)",
        (scope.project_id, root),
    )
    row = conn.execute(
        "SELECT * FROM knowledge_projects WHERE project_id=?", (scope.project_id,)
    ).fetchone()
    if not row or row["repository_identity"] != root:
        raise ValueError("knowledge project repository mismatch")
    return row


def _entity(conn, project, key):
    identifier = identity(project, key)
    conn.execute(
        "INSERT OR IGNORE INTO knowledge_entities VALUES (?,?,?,?)",
        (identifier, project, key.split(":", 1)[0], key),
    )
    return identifier


def publish_snapshot(conn, prepared: PreparedSnapshot, *, expected_active: str | None) -> str:
    scope, inventory = prepared.scope, prepared.inventory
    if not inventory.complete:
        raise ValueError("incomplete inventory cannot replace active snapshot")
    if len(inventory.sources) != len(prepared.extractions):
        raise ValueError("source extraction cardinality mismatch")
    if any(len(source.raw) > MAX_SOURCE_BYTES for source in inventory.sources) or (
        sum(len(source.raw) for source in inventory.sources) > MAX_REFRESH_BYTES
    ):
        raise ValueError("prepared source bytes exceed supported publication limits")
    snapshot = secrets.token_hex(16)
    with transaction(conn):
        project = ensure_project(conn, scope)
        if project["active_snapshot_id"] != expected_active or project["policy_generation"] != (
            prepared.expected_policy_generation
        ):
            raise SnapshotConflict("active snapshot or policy changed during extraction")
        coverage = [
            *inventory.coverage,
            *(gap for extraction in prepared.extractions for gap in extraction.coverage),
        ]
        conn.execute(
            "INSERT INTO knowledge_snapshots VALUES (?,?,?,?,?,?,?)",
            (
                snapshot,
                scope.project_id,
                expected_active,
                canonical(inventory.observation),
                1,
                canonical(coverage),
                utcnow(),
            ),
        )
        present = set()
        for source, extraction in zip(inventory.sources, prepared.extractions, strict=True):
            if source.source_id != identity(scope.project_id, source.locator) or (
                source.version_id
                != identity(source.source_id, source.content_sha256, EXTRACTOR_VERSION)
            ):
                raise ValueError("source identity is not scoped to project/extractor")
            if digest(source.raw) != source.content_sha256:
                raise ValueError("source content hash mismatch")
            present.add(source.source_id)
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_sources VALUES (?,?,?,?,?)",
                (source.source_id, scope.project_id, source.locator, source.kind, "project"),
            )
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_source_versions VALUES (?,?,?,?,?,?,?)",
                (
                    source.version_id,
                    source.source_id,
                    source.content_sha256,
                    "deterministic",
                    EXTRACTOR_VERSION,
                    canonical(source.observation),
                    "complete",
                ),
            )
            conn.execute(
                "INSERT INTO knowledge_snapshot_sources VALUES (?,?,?,?)",
                (snapshot, source.source_id, source.version_id, "present"),
            )
            for assertion in extraction.assertions:
                if not (0 <= assertion.start_byte <= assertion.end_byte <= len(source.raw)):
                    raise ValueError("invalid evidence span")
                if assertion.origin not in {
                    "structured",
                    "static-analysis",
                    "attributed-inference",
                } or (
                    assertion.verification not in {"source-backed", "unverified", "broken-evidence"}
                ):
                    raise ValueError("invalid assertion trust state")
                if (
                    assertion.origin == "attributed-inference"
                    and assertion.verification != "unverified"
                ):
                    raise ValueError("inference cannot create verification")
                data = [
                    assertion.subject,
                    assertion.predicate,
                    assertion.object,
                    assertion.value,
                    assertion.text,
                    assertion.start_byte,
                    assertion.end_byte,
                    assertion.origin,
                    assertion.verification,
                    assertion.applicability,
                ]
                aid = identity(source.version_id, canonical(data))
                subject = _entity(conn, scope.project_id, assertion.subject)
                obj = (
                    _entity(conn, scope.project_id, assertion.object) if assertion.object else None
                )
                conn.execute(
                    "INSERT OR IGNORE INTO knowledge_assertions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        aid,
                        scope.project_id,
                        subject,
                        assertion.predicate,
                        obj,
                        canonical(assertion.value),
                        canonical(assertion.applicability),
                        assertion.origin,
                        assertion.verification,
                        assertion.confidence,
                        assertion.text,
                    ),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO knowledge_evidence VALUES (?,?,?,?,?)",
                    (
                        aid,
                        source.version_id,
                        assertion.start_byte,
                        assertion.end_byte,
                        digest(source.raw[assertion.start_byte : assertion.end_byte]),
                    ),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO knowledge_snapshot_assertions VALUES (?,?)",
                    (snapshot, aid),
                )
        if expected_active:
            old = conn.execute(
                "SELECT * FROM knowledge_snapshot_sources WHERE snapshot_id=?", (expected_active,)
            ).fetchall()
            for row in old:
                if row["source_id"] not in present:
                    conn.execute(
                        "INSERT INTO knowledge_snapshot_sources VALUES (?,?,?,?)",
                        (snapshot, row["source_id"], row["version_id"], "deleted"),
                    )
        conn.execute(
            "UPDATE knowledge_projects SET active_snapshot_id=? WHERE project_id=?",
            (snapshot, scope.project_id),
        )
        record_refresh_attempt(conn, scope.project_id, "published", coverage)
    return snapshot
