from dataclasses import replace

import pytest

from harness.knowledge.models import ContextRequest
from harness.knowledge.query import explain, query
from harness.knowledge.refresh import refresh
from tests.test_knowledge_snapshots import graph  # noqa: F401


def test_packet_partition_excludes_forbidden_candidates_counts_and_cache(graph):  # noqa: F811
    conn, scope = graph
    refresh(conn, scope)
    allowed = frozenset(
        r[0]
        for r in conn.execute(
            "SELECT v.version_id FROM knowledge_source_versions v JOIN knowledge_sources s "
            "ON s.source_id=v.source_id WHERE s.locator='service.py'"
        )
    )
    reviewer = replace(
        scope,
        purpose="fresh-review",
        role="reviewer",
        packet_hash="frozen-packet",
        permitted_source_versions=allowed,
    )
    bundle = query(conn, reviewer, ContextRequest("consumer"))
    assert bundle.payload["assertions"] == []
    assert bundle.payload["budget"]["examined_edges"] == 0
    general = query(conn, scope, ContextRequest("consumer"))
    with pytest.raises(PermissionError):
        explain(
            conn, reviewer, general.bundle_hash, general.payload["assertions"][0]["assertion_id"]
        )
    with pytest.raises(PermissionError):
        query(conn, replace(scope, purpose="fresh-review"), ContextRequest("service"))


def test_revocation_blocks_previously_issued_bundle(graph):  # noqa: F811
    conn, scope = graph
    refresh(conn, scope)
    bundle = query(conn, scope, ContextRequest("service"))
    aid = bundle.payload["assertions"][0]["assertion_id"]
    conn.execute("UPDATE knowledge_projects SET policy_generation=policy_generation+1")
    with pytest.raises(PermissionError):
        explain(conn, scope, bundle.bundle_hash, aid)
