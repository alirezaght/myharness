"""Existing immutable Knowledge source observations verify provenance, never current stability."""

from pathlib import Path


def require_component(conn, repository, component):
    from harness.knowledge.models import projection_identity
    from harness.knowledge.retention import version_purged

    if repository is None:
        raise ValueError("component observation scope required")
    row = conn.execute(
        "SELECT v.version_id FROM knowledge_source_versions v JOIN "
        "knowledge_sources s USING(source_id) "
        "JOIN knowledge_projects p USING(project_id) JOIN knowledge_snapshot_sources m "
        "ON m.version_id=v.version_id AND m.source_id=s.source_id "
        "WHERE v.version_id=? AND m.snapshot_id=? AND s.locator=? "
        "AND p.repository_identity=? AND m.membership_state='present'",
        (
            component["source_version"],
            component["snapshot"],
            component["reference"],
            projection_identity(Path(repository)),
        ),
    ).fetchone()
    if row is None or version_purged(conn, component["source_version"]):
        raise ValueError("verified component observation unavailable")
