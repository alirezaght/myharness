"""Incremental publication preserves provenance, cache identity and honest coverage."""

import importlib
import json
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from harness.db import connect
from harness.knowledge.models import (
    ContextRequest,
    PreparedSnapshot,
    RefreshRequest,
    identity,
)
from harness.knowledge.policy import operator_scope
from harness.knowledge.query import query
from harness.knowledge.refresh import refresh
from harness.knowledge.sources import enumerate_sources
from harness.knowledge.store import SnapshotConflict, publish_snapshot

refresh_module = importlib.import_module("harness.knowledge.refresh")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def graph(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "a.py").write_text("import alpha\n")
    (root / "b.py").write_text("import beta\n")
    git(root, "add", ".")
    conn = connect(tmp_path / "state.db")
    yield conn, operator_scope(root)
    conn.close()


def members(conn, snapshot):
    return {
        r["locator"]: dict(r)
        for r in conn.execute(
            "SELECT m.*,s.locator FROM knowledge_snapshot_sources m "
            "JOIN knowledge_sources s USING(source_id) WHERE snapshot_id=?",
            (snapshot,),
        )
    }


def test_cache_reuses_unchanged_and_reverted_versions(graph, monkeypatch):
    conn, scope = graph
    first = refresh(conn, scope)
    calls = []
    original = refresh_module.extract_source

    def counted(source):
        calls.append(source.locator)
        return original(source)

    monkeypatch.setattr(refresh_module, "extract_source", counted)
    assert refresh(conn, scope).status == "unchanged"
    assert calls == []
    (scope.repository / "a.py").write_text("import changed\n")
    second = refresh(conn, scope)
    assert calls == ["a.py"]
    (scope.repository / "a.py").write_text("import alpha\n")
    third = refresh(conn, scope)
    assert calls == ["a.py"]
    assert (
        members(conn, first.snapshot_id)["a.py"]["version_id"]
        == members(conn, third.snapshot_id)["a.py"]["version_id"]
    )
    assert second.snapshot_id != third.snapshot_id
    assert (
        conn.execute("SELECT count(*) FROM knowledge_assertion_fts").fetchone()[0]
        == conn.execute("SELECT count(*) FROM knowledge_assertions").fetchone()[0]
    )


def test_identical_bytes_different_locators_and_extractor_version(graph, monkeypatch):
    conn, scope = graph
    for name in ("pkg_a", "pkg_b"):
        directory = scope.repository / name
        directory.mkdir()
        (directory / "__init__.py").write_text("")
    git(scope.repository, "add", ".")
    refresh(conn, scope)
    keys = {r[0] for r in conn.execute("SELECT scoped_key FROM knowledge_entities")}
    assert {"module:pkg_a", "module:pkg_b"} <= keys
    calls = []
    original = refresh_module.extract_source
    monkeypatch.setattr(
        refresh_module, "extract_source", lambda s: (calls.append(s.locator), original(s))[1]
    )
    for module in ("sources", "store", "refresh"):
        target = importlib.import_module(f"harness.knowledge.{module}")
        monkeypatch.setattr(target, "EXTRACTOR_VERSION", "next")
        # The code-facts extractor (#34) derives from EXTRACTOR_VERSION.
        derived = (("CODE_FACTS_EXTRACTOR", "next+al"), ("EXTRACTORS", ("next", "next+al")))
        for name, value in derived:
            if hasattr(target, name):
                monkeypatch.setattr(target, name, value)
    refresh(conn, scope)
    assert len(calls) == 4


def test_partial_change_unknown_and_independent_deletion(graph):
    conn, scope = graph
    first = refresh(conn, scope)
    (scope.repository / "a.py").write_text("x" * 100)
    (scope.repository / "b.py").unlink()
    git(scope.repository, "rm", "--cached", "b.py")
    (scope.repository / "c.py").write_text("import gamma\n")
    git(scope.repository, "add", "c.py")
    result = refresh(conn, scope, RefreshRequest(max_file_bytes=32))
    assert result.status == "published-partial"
    rows = members(conn, result.snapshot_id)
    assert rows["a.py"]["membership_state"] == "unknown"
    assert rows["b.py"]["membership_state"] == "deleted"
    assert rows["c.py"]["membership_state"] == "present"
    assert rows["a.py"]["version_id"] == members(conn, first.snapshot_id)["a.py"]["version_id"]
    unknown = conn.execute(
        "SELECT * FROM knowledge_snapshot_source_state WHERE snapshot_id=?", (result.snapshot_id,)
    ).fetchone()
    assert unknown["reason"] == "source-size-capped"
    assert conn.execute(
        "SELECT names_complete,unknown_count,deleted_count FROM knowledge_snapshot_cursor "
        "WHERE snapshot_id=?",
        (result.snapshot_id,),
    ).fetchone()[:] == (1, 1, 1)
    bundle = query(conn, scope, ContextRequest("alpha gamma"))
    locators = {c["locator"] for c in bundle.payload["citations"]}
    assert "a.py" not in locators and "c.py" in locators


def test_sensitive_source_withheld_without_deletion(graph):
    conn, scope = graph
    first = refresh(conn, scope)
    (scope.repository / "a.py").write_text('password = "abcdefgh12345678"\n')
    result = refresh(conn, scope)
    assert result.status == "published-partial"
    assert members(conn, result.snapshot_id)["a.py"]["membership_state"] == "unknown"
    assert members(conn, first.snapshot_id)["a.py"]["membership_state"] == "present"
    reason = conn.execute(
        "SELECT reason FROM knowledge_snapshot_source_state WHERE snapshot_id=?",
        (result.snapshot_id,),
    ).fetchone()[0]
    assert reason == "excluded-sensitive"
    assert "abcdefgh" not in json.dumps(result.coverage)


def test_incomplete_names_never_establish_deletion(graph, monkeypatch):
    conn, scope = graph
    refresh(conn, scope)
    (scope.repository / "a.py").unlink()
    git(scope.repository, "rm", "--cached", "a.py")
    (scope.repository / "b.py").write_text("import changed\n")
    original = refresh_module.enumerate_sources
    monkeypatch.setattr(
        refresh_module, "enumerate_sources", lambda *a: replace(original(*a), names_complete=False)
    )
    result = refresh(conn, scope)
    assert result.status == "published-partial"
    assert members(conn, result.snapshot_id)["a.py"]["membership_state"] == "unknown"


def test_io_extraction_and_hashing_outside_write_lock(graph, monkeypatch):
    conn, scope = graph
    for name, attributes in (
        ("sources", ("read_source", "_git", "digest")),
        ("refresh", ("read_source", "extract_source", "digest")),
        ("store", ("digest", "canonical")),
    ):
        module = importlib.import_module(f"harness.knowledge.{name}")
        for attribute in attributes:
            original = getattr(module, attribute)

            def checked(*args, _original=original, **kwargs):
                assert not conn.in_transaction
                return _original(*args, **kwargs)

            monkeypatch.setattr(module, attribute, checked)
    assert refresh(conn, scope).status == "published"
    (scope.repository / "a.py").write_text("import modified\n")
    assert refresh(conn, scope).status == "published"


def test_failed_publication_rolls_back_fts_cache_cursor_and_restart(graph):
    conn, scope = graph
    first = refresh(conn, scope)
    tables = (
        "knowledge_snapshots",
        "knowledge_assertions",
        "knowledge_assertion_fts",
        "knowledge_extraction_cache",
        "knowledge_snapshot_sources",
        "knowledge_source_cursor",
    )
    before = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables}
    conn.execute(
        "CREATE TEMP TRIGGER fail_cursor BEFORE INSERT ON knowledge_snapshot_cursor "
        "BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )
    (scope.repository / "a.py").write_text("import rollback_canary\n")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        refresh(conn, scope)
    assert {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables} == before
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    reopened = connect(Path(path))
    assert (
        reopened.execute("SELECT active_snapshot_id FROM knowledge_projects").fetchone()[0]
        == first.snapshot_id
    )
    assert reopened.execute("PRAGMA foreign_key_check").fetchall() == []
    reopened.close()


def test_policy_and_pointer_cas_preserve_winner(graph):
    conn, scope = graph
    first = refresh(conn, scope)
    inventory = enumerate_sources(scope, RefreshRequest())
    prepared = PreparedSnapshot(
        scope, inventory, tuple(refresh_module.extract_source(s) for s in inventory.sources), 0
    )
    second = publish_snapshot(conn, prepared, expected_active=first.snapshot_id)
    with pytest.raises(SnapshotConflict):
        publish_snapshot(conn, prepared, expected_active=first.snapshot_id)
    conn.execute("UPDATE knowledge_projects SET policy_generation=1")
    with pytest.raises(SnapshotConflict):
        publish_snapshot(conn, prepared, expected_active=second)
    assert conn.execute("SELECT active_snapshot_id FROM knowledge_projects").fetchone()[0] == second


def test_deferred_batch_exposes_unknown_then_finishes(graph, monkeypatch):
    conn, scope = graph
    monkeypatch.setattr(refresh_module, "MAX_PUBLISH_SOURCES", 1)
    first = refresh(conn, scope)
    assert first.status == "published-partial"
    assert sorted(r["membership_state"] for r in members(conn, first.snapshot_id).values()) == [
        "present",
        "unknown",
    ]
    second = refresh(conn, scope)
    assert second.status == "published"
    assert all(
        r["membership_state"] == "present" for r in members(conn, second.snapshot_id).values()
    )


def test_branch_ancestry_and_history(graph):
    conn, scope = graph
    root = scope.repository

    def commit(message):
        git(
            root,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            message,
        )

    commit("first")
    first = refresh(conn, scope)
    git(root, "checkout", "-qb", "descendant")
    (root / "a.py").write_text("import descendant\n")
    git(root, "add", ".")
    commit("second")
    second = refresh(conn, scope)
    git(root, "checkout", "--orphan", "unrelated")
    git(root, "add", ".")
    commit("unrelated")
    third = refresh(conn, scope)
    cursors = {
        r["snapshot_id"]: r["ancestry"]
        for r in conn.execute("SELECT * FROM knowledge_snapshot_cursor")
    }
    assert [cursors[s.snapshot_id] for s in (first, second, third)] == [
        "initial",
        "descendant",
        "diverged",
    ]
    assert (
        members(conn, first.snapshot_id)["a.py"]["version_id"]
        != members(conn, second.snapshot_id)["a.py"]["version_id"]
    )


def test_nonrepository_sources_carried_and_deleted_file_reappears(graph):
    conn, scope = graph
    first = refresh(conn, scope)
    source_id = members(conn, first.snapshot_id)["b.py"]["source_id"]
    conn.execute(
        "UPDATE knowledge_source_class SET material_class='harness-record' WHERE source_id=?",
        (source_id,),
    )
    (scope.repository / "a.py").unlink()
    (scope.repository / "b.py").unlink()
    git(scope.repository, "rm", "--cached", "a.py", "b.py")
    second = refresh(conn, scope)
    assert members(conn, second.snapshot_id)["a.py"]["membership_state"] == "deleted"
    assert members(conn, second.snapshot_id)["b.py"]["membership_state"] == "present"
    (scope.repository / "a.py").write_text("import resurrected\n")
    git(scope.repository, "add", "a.py")
    third = refresh(conn, scope)
    assert members(conn, third.snapshot_id)["a.py"]["membership_state"] == "present"
    assert members(conn, first.snapshot_id)["a.py"]["membership_state"] == "present"


def test_kind_participates_in_source_version_identity(graph):
    conn, scope = graph
    first = refresh(conn, scope)
    inventory = enumerate_sources(scope, RefreshRequest(), code_facts=True)  # as refresh did
    source = inventory.sources[0]
    changed = replace(
        source,
        kind="text",
        version_id=identity(source.source_id, source.content_sha256, source.extractor, "text"),
    )
    inventory = replace(inventory, sources=(changed, *inventory.sources[1:]))
    extractions = tuple(refresh_module.extract_source(s) for s in inventory.sources)
    snapshot = publish_snapshot(
        conn, PreparedSnapshot(scope, inventory, extractions, 0), expected_active=first.snapshot_id
    )
    assert (
        members(conn, snapshot)[source.locator]["version_id"]
        != members(conn, first.snapshot_id)[source.locator]["version_id"]
    )
    assert conn.execute("SELECT count(*) FROM knowledge_extraction_cache").fetchone()[0] == 3


def test_policy_race_does_not_mask_publication_conflict(graph, monkeypatch):
    conn, scope = graph
    first = refresh(conn, scope)
    (scope.repository / "a.py").write_text("import raced\n")
    original = refresh_module.extract_source

    def revoke(source):
        result = original(source)
        conn.execute("UPDATE knowledge_projects SET policy_generation=1")
        return result

    monkeypatch.setattr(refresh_module, "extract_source", revoke)
    with pytest.raises(SnapshotConflict):
        refresh(conn, scope)
    assert (
        conn.execute("SELECT active_snapshot_id FROM knowledge_projects").fetchone()[0]
        == first.snapshot_id
    )
