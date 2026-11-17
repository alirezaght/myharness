"""Source evolution and publication failures must preserve a complete historical graph."""

import json
import subprocess
from dataclasses import replace
from importlib import import_module

import pytest

from harness.db import connect
from harness.knowledge.extract import extract_source
from harness.knowledge.models import (
    MAX_REFRESH_BYTES,
    MAX_SOURCE_BYTES,
    PreparedSnapshot,
    RefreshRequest,
)
from harness.knowledge.policy import operator_scope
from harness.knowledge.refresh import refresh
from harness.knowledge.sources import enumerate_sources
from harness.knowledge.store import SnapshotConflict, publish_snapshot


@pytest.fixture
def graph(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "consumer.py").write_text("import service\n")
    (root / "service.py").write_text("def serve():\n    return 1\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    conn = connect(tmp_path / "state.db")
    scope = operator_scope(root)
    yield conn, scope
    conn.close()


def test_additive_migration_and_restart(tmp_path):
    path = tmp_path / "state.db"
    conn = connect(path)
    conn.execute(
        "INSERT INTO tasks VALUES ('t-test','keep','small','developer','discovery','now','now')"
    )
    assert conn.execute("SELECT name FROM schema_migrations WHERE version=15").fetchone()[0] == (
        "0015_knowledge.sql"
    )
    assert conn.execute("SELECT count(*) FROM knowledge_snapshots").fetchone()[0] == 0
    conn.close()
    conn = connect(path)
    assert conn.execute("SELECT request FROM tasks WHERE id='t-test'").fetchone()[0] == "keep"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_cas_and_invalid_evidence_roll_back(graph):
    conn, scope = graph
    first = refresh(conn, scope).snapshot_id
    inventory = enumerate_sources(scope, RefreshRequest())
    prepared = PreparedSnapshot(
        scope, inventory, tuple(extract_source(s) for s in inventory.sources), 0
    )
    second = publish_snapshot(conn, prepared, expected_active=first)
    with pytest.raises(SnapshotConflict):
        publish_snapshot(conn, prepared, expected_active=first)
    assert conn.execute("SELECT active_snapshot_id FROM knowledge_projects").fetchone()[0] == second
    assert conn.execute("SELECT count(*) FROM knowledge_snapshots").fetchone()[0] == 2
    extraction = prepared.extractions[0]
    broken = replace(extraction.assertions[0], end_byte=1000000)
    invalid = replace(
        prepared, extractions=(replace(extraction, assertions=(broken,)), *prepared.extractions[1:])
    )
    with pytest.raises(ValueError, match="invalid evidence span"):
        publish_snapshot(conn, invalid, expected_active=second)
    assert conn.execute("SELECT count(*) FROM knowledge_snapshots").fetchone()[0] == 2
    assert conn.execute("SELECT active_snapshot_id FROM knowledge_projects").fetchone()[0] == second


def test_incomplete_read_never_deletes_and_complete_deletion_is_historical(graph):
    conn, scope = graph
    first = refresh(conn, scope).snapshot_id
    (scope.repository / "consumer.py").write_text("x" * 300000)
    result = refresh(conn, scope, RefreshRequest(max_file_bytes=262144))
    assert result.status == "incomplete" and result.snapshot_id == first
    (scope.repository / "consumer.py").unlink()
    subprocess.run(
        ["git", "-C", str(scope.repository), "rm", "--cached", "consumer.py"],
        check=True,
        capture_output=True,
    )
    second = refresh(conn, scope).snapshot_id
    assert second != first
    assert (
        conn.execute(
            "SELECT count(*) FROM knowledge_snapshot_sources WHERE snapshot_id=? "
            "AND membership_state='deleted'",
            (second,),
        ).fetchone()[0]
        == 1
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM knowledge_snapshot_assertions WHERE snapshot_id=?", (first,)
        ).fetchone()[0]
        > 0
    )


def test_large_json_sources_publish_completely_without_decoding_payloads(graph):
    conn, scope = graph
    for name, size in [("historical-fixture.json", 988709), ("verification-contract.json", 518619)]:
        # A synthetic plain-text fixture exercises the real sizes, without any issuer payload.
        raw = json.dumps({"payload": "x" * (size - 15)}).encode()
        assert len(raw) == size
        (scope.repository / name).write_bytes(raw)
    subprocess.run(["git", "-C", str(scope.repository), "add", "."], check=True)
    result = refresh(conn, scope)
    assert result.status == "published"
    rows = conn.execute(
        "SELECT s.locator,v.content_sha256,m.membership_state FROM knowledge_snapshot_sources m "
        "JOIN knowledge_sources s ON s.source_id=m.source_id "
        "JOIN knowledge_source_versions v ON v.version_id=m.version_id WHERE m.snapshot_id=?",
        (result.snapshot_id,),
    ).fetchall()
    assert {r["locator"] for r in rows if r["membership_state"] == "present"} == {
        "consumer.py",
        "service.py",
        "historical-fixture.json",
        "verification-contract.json",
    }
    assert {
        c["locator"] for c in result.coverage if c["status"] == "text-only-no-dependency-analysis"
    } == {"historical-fixture.json", "verification-contract.json"}


def test_per_source_overflow_preserves_snapshot_without_tombstones(graph):
    conn, scope = graph
    first = refresh(conn, scope).snapshot_id
    (scope.repository / "consumer.py").write_bytes(b"x" * (MAX_SOURCE_BYTES + 1))
    result = refresh(conn, scope)
    assert result.status == "incomplete" and result.snapshot_id == first
    cap = next(c for c in result.coverage if c.get("reason") == "source-size-capped")
    assert cap["locator"] == "consumer.py" and cap["limit"] == MAX_SOURCE_BYTES
    assert conn.execute("SELECT count(*) FROM knowledge_snapshots").fetchone()[0] == 1
    assert (
        conn.execute(
            "SELECT count(*) FROM knowledge_snapshot_sources WHERE membership_state='deleted'"
        ).fetchone()[0]
        == 0
    )


def test_aggregate_overflow_bounds_retained_sources_and_preserves_snapshot(graph, monkeypatch):
    import harness.knowledge.sources as sources

    conn, scope = graph
    first = refresh(conn, scope).snapshot_id
    payload = b"x" * MAX_SOURCE_BYTES
    names = [f"part-{i:02}.json" for i in range(65)]
    reads = []
    monkeypatch.setattr(sources, "inventory_names", lambda *_: (names, {}))

    def bounded_read(root, locator, limit):
        reads.append(limit)
        if len(payload) > limit:
            raise sources.SourceSizeCapped(limit, len(payload))
        return payload

    monkeypatch.setattr(sources, "read_source", bounded_read)
    inventory = sources.enumerate_sources(scope, RefreshRequest())
    assert MAX_REFRESH_BYTES == 67108864
    assert sum(len(s.raw) for s in inventory.sources) == MAX_REFRESH_BYTES
    assert reads[-1] == 0
    cap = next(c for c in inventory.coverage if c.get("reason") == "refresh-bytes-capped")
    assert cap["limit"] == MAX_REFRESH_BYTES and not inventory.complete
    monkeypatch.setattr(
        import_module("harness.knowledge.refresh"), "enumerate_sources", lambda *_: inventory
    )
    result = refresh(conn, scope)
    assert result.snapshot_id == first and result.status == "incomplete"
    assert conn.execute("SELECT count(*) FROM knowledge_snapshots").fetchone()[0] == 1


@pytest.mark.parametrize("failure", ["unreadable", "changed"])
def test_failed_source_revalidation_preserves_snapshot(graph, monkeypatch, failure):
    from harness.knowledge.sources import read_source

    conn, scope = graph
    first = refresh(conn, scope).snapshot_id

    def unstable_read(root, locator, limit):
        if failure == "unreadable":
            raise PermissionError("source not readable")
        return read_source(root, locator, limit) + b"changed"

    monkeypatch.setattr(import_module("harness.knowledge.refresh"), "read_source", unstable_read)
    result = refresh(conn, scope)
    assert result.status == "incomplete" and result.snapshot_id == first
    assert conn.execute("SELECT count(*) FROM knowledge_snapshots").fetchone()[0] == 1
    assert (
        conn.execute(
            "SELECT count(*) FROM knowledge_snapshot_sources WHERE membership_state='deleted'"
        ).fetchone()[0]
        == 0
    )


def test_0016_is_additive_schema_only(tmp_path):
    conn = connect(tmp_path / "schema.db")
    assert (
        conn.execute("SELECT name FROM schema_migrations WHERE version=16").fetchone()[0]
        == "0016_knowledge_index.sql"
    )
    for table in (
        "knowledge_extraction_cache",
        "knowledge_extraction_members",
        "knowledge_assertion_fts",
        "knowledge_source_class",
        "knowledge_snapshot_cursor",
        "knowledge_source_cursor",
        "knowledge_contributions",
        "knowledge_review_projections",
        "knowledge_receipts",
    ):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
