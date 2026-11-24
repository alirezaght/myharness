"""Who consumes a module, file or endpoint, and the import/endpoint edges of code: a view over
AL's code facts (#34).

AL holds the only import analyser (`harness.knowledge.al_facts` queries it through `alctl run`).
This module keeps the knowledge graph's own rules on top: the repository must be opted in
(indexed, #12: an unindexed repository costs nothing), the answer is limited to the sources the
scope may see in its active snapshot, and a commit snapshot is answered at its commit. Without
AL the graph still serves its text and symbol index, and these views say
`al-facts-unavailable` rather than guessing edges.
"""

from harness.knowledge import al_facts
from harness.knowledge.policy import validate_scope

DEFAULT_LIMIT = 200
_RESOLVE = object()


def _snapshot(conn, scope, snapshot_id):
    from harness.knowledge.query import _partition

    project = validate_scope(conn, scope)
    snapshot = snapshot_id or project["active_snapshot_id"]
    if not snapshot:
        return None
    _partition(conn, scope, snapshot)
    return snapshot


def _visible(conn):
    """Locators of the scope's eligible sources in the partitioned snapshot."""
    return {
        r[0]
        for r in conn.execute(
            "SELECT s.locator FROM temp.k_versions k JOIN knowledge_source_versions v "
            "USING(version_id) JOIN knowledge_sources s USING(source_id)"
        )
    }


def _commit(conn, snapshot):
    row = conn.execute(
        "SELECT head,mode FROM knowledge_snapshot_cursor WHERE snapshot_id=?", (snapshot,)
    ).fetchone()
    return row[0] if row and row[1] == "commit" and row[0] else None


def _facts(scope, facts):
    return al_facts.resolve(scope.repository) if facts is _RESOLVE else facts


def _unavailable(base, reason):
    return {
        **base,
        "status": "unavailable",
        "gaps": [{"status": "unresolved", "reason": "al-facts-unavailable", "detail": reason}],
    }


def _query(facts, snapshot, conn, *arguments):
    commit = _commit(conn, snapshot)
    return facts.query(*arguments, *(["--commit", commit] if commit else []))


def consumers(conn, scope, target, *, snapshot_id=None, limit=DEFAULT_LIMIT, facts=_RESOLVE):
    """Static importers and runtime (HTTP endpoint) callers of `target` in the snapshot."""
    snapshot = _snapshot(conn, scope, snapshot_id)
    if not snapshot:
        return {"target": target, "status": "not-indexed", "consumers": [], "gaps": []}
    base = {
        "target": target,
        "snapshot_id": snapshot,
        "resolved": {"files": [], "endpoints": []},
        "consumers": [],
        "truncated": False,
    }
    facts = _facts(scope, facts)
    if facts is None:
        return _unavailable(base, "no AL run to query")
    visible = _visible(conn)
    try:
        answer = _query(facts, snapshot, conn, "consumers", target, "--limit", "1000")
    except al_facts.FactsUnavailable as error:
        return _unavailable(base, str(error))
    rows = [c for c in answer["consumers"] if c["locator"] in visible]
    hidden = len(answer["consumers"]) - len(rows)
    status = answer["status"]
    if status == "complete" and _coverage_status(conn, snapshot) != "complete":
        status = "partial"
    return {
        **base,
        "status": status,
        "source": "al-facts",
        "tree": answer.get("tree"),
        "resolved": {
            "files": [f for f in answer["resolved"]["files"] if f in visible],
            "endpoints": answer["resolved"]["endpoints"],
        },
        "consumers": rows[:limit],
        "truncated": len(rows) > limit or bool(answer.get("truncated")),
        "gaps": [g for g in answer["gaps"] if "locator" not in g or g["locator"] in visible]
        + ([{"status": "withheld", "reason": "outside-scope", "count": hidden}] if hidden else []),
    }


def edges(conn, scope, paths=(), *, snapshot_id=None, facts=_RESOLVE):
    """AL's import/endpoint edges of the visible code files under `paths` (all when empty)."""
    snapshot = _snapshot(conn, scope, snapshot_id)
    if not snapshot:
        return {"status": "not-indexed", "edges": [], "gaps": []}
    base = {"snapshot_id": snapshot, "edges": []}
    facts = _facts(scope, facts)
    if facts is None:
        return _unavailable(base, "no AL run to query")
    visible = _visible(conn)
    try:
        answer = _query(facts, snapshot, conn, "edges", *paths)
    except al_facts.FactsUnavailable as error:
        return _unavailable(base, str(error))
    return {
        **base,
        "status": answer["status"],
        "source": "al-facts",
        "tree": answer.get("tree"),
        "edges": [e for e in answer["edges"] if e["locator"] in visible],
        "gaps": [g for g in answer["gaps"] if g.get("locator") in visible],
    }


def _coverage_status(conn, snapshot):
    row = conn.execute(
        "SELECT unknown_count,names_complete FROM knowledge_snapshot_cursor WHERE snapshot_id=?",
        (snapshot,),
    ).fetchone()
    return "complete" if row and not row[0] and row[1] else "partial"


def coverage_summary(conn, scope):
    """One-line trust signal: how much of the eligible inventory the active snapshot covers."""
    from harness.knowledge.sources import CODE_SUFFIXES

    project = conn.execute(
        "SELECT active_snapshot_id FROM knowledge_projects WHERE project_id=?", (scope.project_id,)
    ).fetchone()
    if not project or not project[0]:
        return {"status": "not-indexed"}
    rows = conn.execute(
        "SELECT s.locator,m.membership_state FROM knowledge_snapshot_sources m "
        "JOIN knowledge_sources s USING(source_id) WHERE m.snapshot_id=? "
        "AND s.locator NOT LIKE 'harness:%'",
        (project[0],),
    ).fetchall()
    present = [r[0] for r in rows if r[1] == "present"]
    known = [r[0] for r in rows if r[1] in {"present", "unknown"}]
    languages = sorted(
        {
            "python" if n.endswith(".py") else "typescript/javascript"
            for n in present
            if any(n.endswith(suffix) for suffix in CODE_SUFFIXES)
        }
    )
    percent = round(100 * len(present) / len(known)) if known else 100
    status = _coverage_status(conn, project[0])
    return {
        "status": status,
        "snapshot_id": project[0],
        "indexed_files": len(present),
        "known_files": len(known),
        "percent": percent,
        "languages": languages,
        "line": f"knowledge graph: {status}, {percent}% of {len(known)} files, "
        + (", ".join(languages) or "no code")
        + " text/symbols; import/endpoint edges from AL code facts",
    }
