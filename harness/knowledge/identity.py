"""Exact scoped identity resolution; aliases require supporting evidence."""

from harness.knowledge.models import identity


def resolve(conn, project_id, reference):
    """Must run after query creates its permitted k_eligible partition."""
    rows = conn.execute(
        "SELECT DISTINCT n.scoped_key FROM knowledge_entities n "
        "JOIN knowledge_assertions a ON (a.subject_id=n.entity_id OR a.object_id=n.entity_id) "
        "JOIN temp.k_eligible e ON e.assertion_id=a.assertion_id "
        "WHERE n.project_id=? AND n.scoped_key=? UNION "
        "SELECT DISTINCT n.scoped_key FROM knowledge_aliases x "
        "JOIN temp.k_eligible e ON e.assertion_id=x.assertion_id "
        "JOIN knowledge_entities n ON n.entity_id=x.entity_id "
        "WHERE x.project_id=? AND x.alias=? ORDER BY 1 LIMIT 41",
        (project_id, reference, project_id, reference),
    ).fetchall()
    return tuple(r[0] for r in rows)


def record_alias(conn, project_id, alias, entity_id, assertion_id):
    support = conn.execute(
        "SELECT 1 FROM knowledge_assertions a JOIN knowledge_evidence e USING(assertion_id) "
        "WHERE a.assertion_id=? AND a.project_id=? AND (a.subject_id=? OR a.object_id=?)",
        (assertion_id, project_id, entity_id, entity_id),
    ).fetchone()
    if not support or not alias or len(alias.encode()) > 512:
        raise ValueError("alias requires scoped source evidence")
    conn.execute(
        "INSERT OR IGNORE INTO knowledge_aliases VALUES (?,?,?,?,?)",
        (
            identity(project_id, alias, entity_id, assertion_id),
            project_id,
            alias,
            entity_id,
            assertion_id,
        ),
    )
