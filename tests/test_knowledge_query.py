import json
import subprocess

import pytest

from harness.cli_knowledge import evidence_knowledge
from harness.controller import HarnessController
from harness.knowledge.models import ContextRequest, QueryLimits, canonical, digest
from harness.knowledge.query import explain, query
from harness.knowledge.refresh import refresh
from tests.test_knowledge_snapshots import graph as graph


def test_sourced_impact_paths_survive_reconnect_and_observe_staleness(graph):
    conn, scope = graph
    refresh(conn, scope)
    bundle = query(conn, scope, ContextRequest(entity_refs=("module:service",)))
    assert any(
        a["subject"] == "module:consumer" and a["predicate"] == "imports"
        for a in bundle.payload["assertions"]
    )
    assert bundle.payload["citations"]
    aid = bundle.payload["assertions"][0]["assertion_id"]
    assert explain(conn, scope, bundle.bundle_hash, aid)["citations"]
    (scope.repository / "consumer.py").write_text("import changed\n")
    updated = query(conn, scope, ContextRequest(entity_refs=("module:service",)))
    assert updated.payload["staleness"]
    assert not any(a["subject"] == "module:consumer" for a in updated.payload["assertions"])


def test_budget_includes_all_citations_paths_and_metadata(graph):
    conn, scope = graph
    refresh(conn, scope)
    bundle = query(conn, scope, ContextRequest("service", limits=QueryLimits(bytes=2048)))
    encoded = canonical(bundle.to_dict()).encode()
    assert len(encoded) <= 2048
    assert len(encoded) == bundle.payload["budget"]["serialized_bytes"]
    assert {a["assertion_id"] for a in bundle.payload["assertions"]} == {
        c["assertion_id"] for c in bundle.payload["citations"]
    }
    assert json.loads(encoded)["snapshot_id"]


def test_large_source_citations_bind_whole_content_and_return_bounded_evidence(graph):
    conn, scope = graph
    raw = json.dumps({"description": "large-contract", "payload": "x" * 519000}).encode()
    path = scope.repository / "large-contract.json"
    path.write_bytes(raw)
    subprocess.run(["git", "-C", str(scope.repository), "add", "."], check=True)
    assert refresh(conn, scope).status == "published"
    bundle = query(conn, scope, ContextRequest("large-contract"))
    assertion = next(
        a for a in bundle.payload["assertions"] if a["subject"] == "file:large-contract.json"
    )
    aid = assertion["assertion_id"]
    refs = explain(conn, scope, bundle.bundle_hash, aid)["citations"]
    assert refs[0]["content_sha256"] == digest(raw)
    evidence = evidence_knowledge(
        HarnessController(conn), scope.repository, bundle.bundle_hash, aid
    )
    assert len(evidence["citations"][0]["text"].encode()) <= 4096
    assert evidence["citations"][0]["truncated"]
    path.write_bytes(raw[:-3] + b"y" + raw[-2:])
    # The edit is beyond the cited prefix; whole-source integrity still invalidates it.
    assert refs[0]["end_byte"] < len(raw) - 3
    with pytest.raises(ValueError, match="stale"):
        explain(conn, scope, bundle.bundle_hash, aid)
    current = query(conn, scope, ContextRequest("large-contract"))
    assert all(a["assertion_id"] != aid for a in current.payload["assertions"])
