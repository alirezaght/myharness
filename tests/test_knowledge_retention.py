"""Purge is permanent per version; retention alone respects pins and immutable hashes."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from harness.knowledge.extract import extract_source
from harness.knowledge.models import ContextRequest, PreparedSnapshot, RefreshRequest
from harness.knowledge.policy import current_operator_scope
from harness.knowledge.query import explain, query
from harness.knowledge.refresh import refresh
from harness.knowledge.retention import purge, retain
from harness.knowledge.sources import enumerate_sources
from harness.knowledge.store import SnapshotConflict, prepare_rows, publish_snapshot
from tests.test_knowledge_lifecycle import graph as graph


def test_purge_preserves_metadata_prevents_same_version_and_racing_publish(graph):
    conn, scope = graph
    first = refresh(conn, scope)
    bundle = query(conn, scope, ContextRequest("alpha"))
    aid = bundle.payload["assertions"][0]["assertion_id"]
    version = bundle.payload["citations"][0]["version_id"]
    conn.execute(
        "INSERT INTO knowledge_bundle_pins VALUES (?,?)", (bundle.bundle_hash, "task:active")
    )
    inventory = enumerate_sources(scope, RefreshRequest(), code_facts=True)  # as refresh did
    prepared = PreparedSnapshot(
        scope, inventory, tuple(extract_source(s) for s in inventory.sources), 0
    )
    prepared = replace(prepared, rows=prepare_rows(prepared))
    metadata = conn.execute("SELECT * FROM knowledge_evidence").fetchall()
    result = purge(conn, scope, [version])
    assert result["assertions"] and result["bundles"]
    assert conn.execute(
        "SELECT text,value_json FROM knowledge_assertions WHERE assertion_id=?", (aid,)
    ).fetchone()[:] == ("", "null")
    assert [tuple(r) for r in conn.execute("SELECT * FROM knowledge_evidence")] == [
        tuple(r) for r in metadata
    ]
    assert (
        conn.execute(
            "SELECT payload FROM knowledge_bundles WHERE bundle_hash=?", (bundle.bundle_hash,)
        ).fetchone()[0]
        is None
    )
    with pytest.raises(SnapshotConflict):
        publish_snapshot(conn, prepared, expected_active=first.snapshot_id)
    # Even a caller with current generation cannot republish prepared purged identities.
    scope = current_operator_scope(conn, scope.repository)
    with pytest.raises(SnapshotConflict, match="purged"):
        publish_snapshot(
            conn,
            replace(prepared, scope=scope, expected_policy_generation=scope.policy_generation),
            expected_active=first.snapshot_id,
        )
    assert purge(conn, scope, [version])["status"] == "unchanged"
    assert not query(
        conn, scope, ContextRequest("alpha", as_of_snapshot=first.snapshot_id)
    ).payload["assertions"]
    refresh(conn, scope)
    assert not query(conn, scope, ContextRequest("alpha")).payload["assertions"]
    (scope.repository / "a.py").write_text("import changed\n")
    refresh(conn, scope)
    assert query(conn, scope, ContextRequest("changed")).payload["assertions"]
    with pytest.raises(PermissionError, match="bundle-purged"):
        explain(conn, scope, bundle.bundle_hash, aid)
    assert (scope.repository / "a.py").read_text() == "import changed\n"


def test_retention_cutoff_pins_batches_and_hashes(graph):
    conn, scope = graph
    refresh(conn, scope)
    bundles = [query(conn, scope, ContextRequest(text)) for text in ("alpha", "beta", "import")]
    for bundle in bundles:
        conn.execute(
            "UPDATE knowledge_bundles SET created_at='2026-10-01T00:00:00+00:00' "
            "WHERE bundle_hash=?",
            (bundle.bundle_hash,),
        )
    conn.execute(
        "INSERT INTO knowledge_bundle_pins VALUES (?,?)",
        (bundles[0].bundle_hash, "execution:active"),
    )
    now = datetime(2026, 10, 31, tzinfo=UTC)
    assert retain(conn, scope, now=now)["bundles"] == 0
    now = datetime(2026, 11, 1, tzinfo=UTC)
    assert retain(conn, scope, now=now, limit=1)["bundles"] == 1
    assert retain(conn, scope, now=now, limit=1)["bundles"] == 1
    assert retain(conn, scope, now=now)["bundles"] == 0
    aid = bundles[0].payload["assertions"][0]["assertion_id"]
    assert explain(conn, scope, bundles[0].bundle_hash, aid)
    with pytest.raises(PermissionError, match="bundle-expired"):
        explain(
            conn, scope, bundles[1].bundle_hash, bundles[1].payload["assertions"][0]["assertion_id"]
        )
    assert set(r[0] for r in conn.execute("SELECT bundle_hash FROM knowledge_bundles")) == {
        b.bundle_hash for b in bundles
    }
    assert not conn.execute("SELECT * FROM knowledge_purges").fetchall()


def test_purge_descendants_erases_pinned_payload_and_keeps_authoritative_tables(graph):
    from harness.knowledge.models import (
        EXTRACTOR_VERSION,
        Assertion,
        Extraction,
        SourceInventory,
        SourceVersion,
        digest,
        identity,
    )

    conn, scope = graph
    first = refresh(conn, scope)
    parent_bundle = query(conn, scope, ContextRequest("alpha"))
    parent = parent_bundle.payload["assertions"][0]["assertion_id"]
    version = next(
        c["version_id"] for c in parent_bundle.payload["citations"] if c["assertion_id"] == parent
    )
    raw = b"descendant private payload"
    (scope.repository / "derived.md").write_bytes(raw)
    sid = identity(scope.project_id, "derived.md")
    source = SourceVersion(
        sid,
        identity(sid, digest(raw), EXTRACTOR_VERSION, "text"),
        "derived.md",
        "text",
        digest(raw),
        raw,
        {},
    )
    extraction = Extraction(
        (
            Assertion(
                "derived:test",
                "summarizes",
                None,
                "private value",
                "descendant payload",
                0,
                len(raw),
                derivation_parents=(parent,),
            ),
        )
    )
    publish_snapshot(
        conn,
        PreparedSnapshot(
            scope, SourceInventory((source,), True, (), {}), (extraction,), 0, carry_forward=True
        ),
        expected_active=first.snapshot_id,
    )
    descendant = query(conn, scope, ContextRequest("descendant"))
    conn.execute(
        "INSERT INTO knowledge_bundle_pins VALUES (?,?)", (descendant.bundle_hash, "review:pin")
    )
    before = [line for line in conn.iterdump() if "knowledge_" not in line]
    purge(conn, scope, [version])
    assert [line for line in conn.iterdump() if "knowledge_" not in line] == before
    assert (
        conn.execute(
            "SELECT payload FROM knowledge_bundles WHERE bundle_hash=?", (descendant.bundle_hash,)
        ).fetchone()[0]
        is None
    )
    assert not conn.execute(
        "SELECT * FROM knowledge_assertion_fts WHERE knowledge_assertion_fts MATCH 'descendant'"
    ).fetchall()
    assert not conn.execute(
        "SELECT * FROM knowledge_extraction_cache WHERE version_id=?", (source.version_id,)
    ).fetchall()
    assert not conn.execute(
        "SELECT * FROM knowledge_extraction_members WHERE version_id=?", (source.version_id,)
    ).fetchall()


def test_expired_bundle_is_never_restored_when_queried_again(graph):
    conn, scope = graph
    refresh(conn, scope)
    old = query(conn, scope, ContextRequest("alpha"))
    conn.execute("UPDATE knowledge_bundles SET created_at='2020-01-01T00:00:00+00:00'")
    retain(conn, scope)
    fresh = query(conn, scope, ContextRequest("alpha"))
    assert fresh.bundle_hash != old.bundle_hash
    assert (
        conn.execute(
            "SELECT payload FROM knowledge_bundles WHERE bundle_hash=?", (old.bundle_hash,)
        ).fetchone()[0]
        is None
    )
