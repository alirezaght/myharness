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
    assert max(reads) == MAX_SOURCE_BYTES
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
    # An unchanged lstat identity otherwise lets a hash-only source skip the second read.
    monkeypatch.setattr(
        import_module("harness.knowledge.refresh"), "stat_identity", lambda *_: None
    )
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


def test_populated_pre_index_graph_survives_0016_and_current_query(graph, tmp_path, monkeypatch):
    from contextlib import closing

    from harness import db as db_module
    from harness.knowledge.models import ContextRequest, digest
    from harness.knowledge.query import query
    from tests.test_migration_compatibility import old_database

    donor, scope = graph
    historical_snapshot = refresh(donor, scope).snapshot_id
    path = tmp_path / "pre-index.db"
    with closing(old_database(path, common=15)) as conn:
        # Copy only the actual 0015 tables into real pre-0016 SQL. The owned public
        # refresh supplies valid source/citation records, not invented native authority.
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name LIKE 'knowledge_%' "
                "ORDER BY rowid"
            )
        ]
        for table in tables:
            rows = donor.execute(f'SELECT * FROM "{table}"').fetchall()
            if rows:
                placeholders = ",".join("?" for _ in rows[0])
                conn.executemany(f'INSERT INTO "{table}" VALUES ({placeholders})', rows)
        conn.execute(
            "INSERT INTO tasks VALUES ('t-pre-index','Preserve populated history',"
            "'simple','proposed','design','original-created','original-updated')"
        )
        tables.append("tasks")

        def contents(connection):
            return {
                table: sorted(
                    (tuple(row) for row in connection.execute(f'SELECT * FROM "{table}"')),
                    key=repr,
                )
                for table in tables
            }

        before = contents(conn)
        assert before["tasks"] and before["knowledge_assertions"] and before["knowledge_evidence"]
        assert len(before["knowledge_sources"]) == 2
        assert conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 15
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_schema WHERE name='knowledge_extraction_cache'"
            ).fetchone()
            is None
        )
        original_ledger = [tuple(row) for row in conn.execute("SELECT * FROM schema_migrations")]
        migrations = db_module._migrations()
        with monkeypatch.context() as patch:
            patch.setattr(db_module, "_migrations", lambda: [m for m in migrations if m[0] <= 16])
            assert db_module.apply_migrations(conn) == ["0016_knowledge_index.sql"]
            assert contents(conn) == before
            assert [
                tuple(row)
                for row in conn.execute("SELECT * FROM schema_migrations WHERE version<=15")
            ] == original_ledger
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            assert db_module.apply_migrations(conn) == []

    # Apply remaining real migrations through connect, retaining the original
    # restart test and generic migration-history coverage separately.
    conn = connect(path)
    try:
        assert contents(conn) == before
        assert db_module.apply_migrations(conn) == []
        retained = refresh(conn, scope)
        assert retained.status == "unchanged" and retained.snapshot_id == historical_snapshot
        # Unchanged legacy sources need no publication. A real source edit then
        # exercises the new extraction cache and current query/receipt tables.
        (scope.repository / "service.py").write_text("def serve():\n    return 2\n")
        updated = refresh(conn, scope)
        assert updated.status == "published" and updated.snapshot_id != historical_snapshot
        first = query(conn, scope, ContextRequest("service"))
        second = query(conn, scope, ContextRequest("service"))
        assert first.payload["assertions"] and first.payload["citations"]
        assert first.bundle_hash == second.bundle_hash
        assert first.receipt_id and second.receipt_id and first.receipt_id != second.receipt_id
        for table in (
            "knowledge_extraction_cache",
            "knowledge_extraction_members",
            "knowledge_bundles",
            "knowledge_receipts",
        ):
            assert conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] > 0
        for citation in first.payload["citations"]:
            source = conn.execute(
                "SELECT s.locator,v.content_sha256 FROM knowledge_source_versions v "
                "JOIN knowledge_sources s USING(source_id) WHERE v.version_id=?",
                (citation["version_id"],),
            ).fetchone()
            raw = (scope.repository / source["locator"]).read_bytes()
            assert digest(raw) == source["content_sha256"]
            span = raw[citation["start_byte"] : citation["end_byte"]]
            assert digest(span) == citation["span_sha256"]
        assert conn.execute(
            "SELECT count(*) FROM knowledge_snapshot_assertions WHERE snapshot_id=?",
            (historical_snapshot,),
        ).fetchone()[0] == len(before["knowledge_snapshot_assertions"])
        for table in ("knowledge_assertions", "knowledge_evidence", "knowledge_snapshots"):
            assert set(before[table]) <= set(contents(conn)[table])
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        observed_receipts = [tuple(row) for row in conn.execute("SELECT * FROM knowledge_receipts")]
    finally:
        conn.close()
    conn = connect(path)
    try:
        assert db_module.apply_migrations(conn) == []
        assert [tuple(row) for row in conn.execute("SELECT * FROM knowledge_receipts")] == (
            observed_receipts
        )
        assert [tuple(row) for row in conn.execute("SELECT * FROM tasks")] == before["tasks"]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_hash_only_source_changed_during_refresh_is_revalidated(graph, monkeypatch):
    """A cached (hash-only) source edited after inventory fails lstat and is fully re-read."""
    refresh_module = import_module("harness.knowledge.refresh")

    conn, scope = graph
    first = refresh(conn, scope).snapshot_id
    original = refresh_module.inventory_names
    target = next(p for p in scope.repository.iterdir() if p.suffix == ".py")

    def edit_then_list(*args):
        target.write_text(target.read_text() + "\n# edited\n")
        return original(*args)

    monkeypatch.setattr(refresh_module, "inventory_names", edit_then_list)
    result = refresh(conn, scope)
    assert result.status == "incomplete" and result.snapshot_id == first
    assert any(c.get("reason") == "unstable" for c in result.coverage)
