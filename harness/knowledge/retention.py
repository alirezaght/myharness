"""Explicit local lifecycle operations; immutable identities outlive derived payloads."""

import json
from datetime import UTC, datetime, timedelta

from harness.db import transaction, utcnow
from harness.knowledge.models import canonical, digest, identity
from harness.knowledge.policy import validate_scope


def require_operator(conn, scope):
    validate_scope(conn, scope)
    if scope.role != "operator" or scope.purpose != "operator" or scope.execution_id:
        raise PermissionError("knowledge mutation requires a local operator scope")
    from harness.knowledge.store import ensure_project

    return ensure_project(conn, scope)


def version_purged(conn, version_id):
    return bool(
        conn.execute(
            "SELECT 1 FROM knowledge_purges WHERE target_kind='source-version' AND "
            "target_sha256=? UNION ALL "
            "SELECT 1 FROM knowledge_evidence e JOIN knowledge_assertion_state a "
            "USING(assertion_id) "
            "WHERE e.version_id=?",
            (version_id, version_id),
        ).fetchone()
    )


def invalidate(conn, bundles, reason, cause):
    now = utcnow()
    for bundle in bundles:
        conn.execute(
            "UPDATE knowledge_bundles SET payload=NULL,invalidated_at=coalesce(invalidated_at,?) "
            "WHERE bundle_hash=?",
            (now, bundle),
        )
        conn.execute(
            "INSERT INTO knowledge_bundle_state VALUES (?,?,?,?) ON CONFLICT(bundle_hash) "
            "DO UPDATE SET reason=excluded.reason,cause_ref=excluded.cause_ref,at=excluded.at",
            (bundle, reason, cause, now),
        )
        conn.execute("DELETE FROM knowledge_query_cache WHERE bundle_hash=?", (bundle,))


def invalidate_versions(conn, project, versions, reason, cause):
    bundles = set()
    for version in versions:
        bundles.update(
            r[0]
            for r in conn.execute(
                "SELECT bundle_hash FROM knowledge_bundle_sources WHERE version_id=?", (version,)
            )
        )
        conn.execute(
            "DELETE FROM knowledge_review_projection_sources WHERE version_id=?", (version,)
        )
    invalidate(conn, bundles, reason, cause)
    conn.execute("DELETE FROM knowledge_query_cache WHERE project_id=?", (project,))
    return bundles


def purge(conn, scope, version_ids):
    """Permanently withhold exact versions and descendants; never erase authoritative sources."""
    require_operator(conn, scope)
    versions = set(version_ids)
    if not versions or len(versions) > 500:
        raise ValueError("purge requires 1..500 explicit source versions")
    for version in versions:
        if not conn.execute(
            "SELECT 1 FROM knowledge_source_versions v JOIN knowledge_sources s USING(source_id) "
            "WHERE v.version_id=? AND s.project_id=?",
            (version, scope.project_id),
        ).fetchone():
            raise ValueError("purge version outside selected project")
    with transaction(conn):
        require_operator(conn, scope)
        new = {v for v in versions if not version_purged(conn, v)}
        if not new:
            return {"status": "unchanged", "versions": 0, "assertions": 0, "bundles": 0}
        assertions = set()
        for version in versions:
            assertions.update(
                r[0]
                for r in conn.execute(
                    "SELECT assertion_id FROM knowledge_evidence WHERE version_id=?", (version,)
                )
            )
        frontier = set(assertions)
        while frontier:
            children = set()
            for aid in frontier:
                children.update(
                    r[0]
                    for r in conn.execute(
                        "SELECT assertion_id FROM knowledge_derivations WHERE "
                        "parent_assertion_id=?",
                        (aid,),
                    )
                )
            frontier = children - assertions
            assertions.update(frontier)
        affected_versions = set(versions)
        for aid in assertions:
            affected_versions.update(
                r[0]
                for r in conn.execute(
                    "SELECT version_id FROM knowledge_evidence WHERE assertion_id=?", (aid,)
                )
            )
        purge_id = identity(scope.project_id, canonical(sorted(new)))
        bundles = invalidate_versions(conn, scope.project_id, affected_versions, "purged", purge_id)
        for version in sorted(new):
            conn.execute(
                "INSERT INTO knowledge_purges VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    identity(purge_id, version),
                    scope.project_id,
                    "source-version",
                    version,
                    "operator-request",
                    len(new),
                    len(assertions),
                    len(bundles),
                    scope.principal_id,
                    utcnow(),
                ),
            )
        first = identity(purge_id, sorted(new)[0])
        for aid in assertions:
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_assertion_state VALUES (?,'purged',?)",
                (aid, first),
            )
            conn.execute(
                "UPDATE knowledge_assertions SET text='',value_json='null',applicability_json='{}' "
                "WHERE assertion_id=?",
                (aid,),
            )
            conn.execute(
                "DELETE FROM knowledge_assertion_fts WHERE rowid=(SELECT rowid FROM "
                "knowledge_assertions WHERE assertion_id=?)",
                (aid,),
            )
        for version in affected_versions:
            conn.execute("DELETE FROM knowledge_extraction_cache WHERE version_id=?", (version,))
            conn.execute("DELETE FROM knowledge_extraction_members WHERE version_id=?", (version,))
        material_hashes = set()
        for version in versions:
            row = conn.execute(
                "SELECT content_sha256,observation_json FROM knowledge_source_versions "
                "WHERE version_id=?",
                (version,),
            ).fetchone()
            material_hashes.add(row[0])
            material_hashes.add(json.loads(row[1]).get("lesson_sha256"))
        for row in conn.execute(
            "SELECT * FROM knowledge_lessons WHERE project_id=? AND payload!=''",
            (scope.project_id,),
        ).fetchall():
            from harness.knowledge.lessons import parse_portable

            body, metadata = parse_portable(row["payload"].encode())
            if (
                row["content_sha256"] in material_hashes
                or digest(row["payload"].encode()) in material_hashes
                or any(e["assertion_id"] in assertions for e in metadata["evidence"])
            ):
                conn.execute(
                    "UPDATE knowledge_lessons SET payload='' WHERE lesson_id=? AND version=?",
                    (row["lesson_id"], row["version"]),
                )
        conn.execute(
            "UPDATE knowledge_projects SET policy_generation=policy_generation+1 "
            "WHERE project_id=?",
            (scope.project_id,),
        )
        return {
            "status": "purged",
            "versions": len(new),
            "assertions": len(assertions),
            "bundles": len(bundles),
        }


def retain(conn, scope, *, now=None, limit=500):
    require_operator(conn, scope)
    if not 1 <= limit <= 500:
        raise ValueError("retention batch must be 1..500")
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("retention requires timezone-aware time")
    cutoff = (now - timedelta(days=30)).isoformat()
    with transaction(conn):
        require_operator(conn, scope)
        bundles = [
            r[0]
            for r in conn.execute(
                "SELECT b.bundle_hash FROM knowledge_bundles b WHERE b.project_id=? "
                "AND b.payload IS NOT NULL "
                "AND julianday(b.created_at)<julianday(?) AND NOT EXISTS "
                "(SELECT 1 FROM knowledge_bundle_pins p WHERE p.bundle_hash=b.bundle_hash) "
                "ORDER BY b.created_at,b.bundle_hash LIMIT ?",
                (scope.project_id, cutoff, limit),
            )
        ]
        invalidate(conn, bundles, "expired", "30-day-retention")
    return {"status": "expired", "bundles": len(bundles), "cutoff": cutoff}
