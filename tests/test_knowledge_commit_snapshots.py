"""Commit evidence is bound to selected snapshot objects, independently of extraction reuse."""

import importlib

import pytest

from harness.knowledge.models import ContextRequest, RefreshRequest
from harness.knowledge.query import explain, query
from harness.knowledge.refresh import refresh
from tests.test_knowledge_lifecycle import git
from tests.test_knowledge_lifecycle import graph as graph


def commit(root):
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    return git(root, "rev-parse", "HEAD").stdout.decode().strip()


def test_commit_reuses_extraction_but_not_working_tree_observation(graph, monkeypatch):
    conn, scope = graph
    oid = commit(scope.repository)
    working = refresh(conn, scope)
    module = importlib.import_module("harness.knowledge.refresh")
    calls = []
    original = module.extract_source
    monkeypatch.setattr(
        module, "extract_source", lambda source: (calls.append(source.locator), original(source))[1]
    )
    pinned = refresh(conn, scope, RefreshRequest(commit="HEAD"))
    assert pinned.snapshot_id != working.snapshot_id and not calls
    (scope.repository / "a.py").unlink()
    (scope.repository / "b.py").write_text("import dirty\n")
    bundle = query(conn, scope, ContextRequest("alpha"))
    assert bundle.payload["assertions"] and bundle.payload["evidence_mode"] == "commit"
    assert bundle.payload["commit"] == oid
    assert all(a["historical"] for a in bundle.payload["assertions"])
    aid = bundle.payload["assertions"][0]["assertion_id"]
    evidence = explain(conn, scope, bundle.bundle_hash, aid, include_evidence=True)
    assert "alpha" in evidence["citations"][0]["text"]
    assert oid in evidence["citations"][0]["observation_json"]
    # Ref movement cannot rewrite the stored selected snapshot.
    (scope.repository / "a.py").write_text("import newer\n")
    commit(scope.repository)
    old = query(conn, scope, ContextRequest("alpha", as_of_snapshot=pinned.snapshot_id))
    assert old.payload["assertions"] and old.payload["commit"] == oid
    refresh(conn, scope)
    assert not query(conn, scope, ContextRequest("alpha")).payload["assertions"]
    assert query(conn, scope, ContextRequest("newer")).payload["assertions"]


def test_commit_first_cache_then_working_tree_still_requires_current_bytes(graph):
    conn, scope = graph
    commit(scope.repository)
    refresh(conn, scope, RefreshRequest(commit="HEAD"))
    refresh(conn, scope)
    (scope.repository / "a.py").write_text("import changed\n")
    bundle = query(conn, scope, ContextRequest("alpha"))
    assert not bundle.payload["assertions"]
    assert any(s["status"] == "withheld-stale-evidence" for s in bundle.payload["staleness"])


def test_commit_excludes_symlinks_gitlinks_secrets_and_mutable_members(graph):
    from harness.knowledge.models import (
        EXTRACTOR_VERSION,
        Extraction,
        PreparedSnapshot,
        SourceInventory,
        SourceVersion,
        digest,
        identity,
    )
    from harness.knowledge.store import publish_snapshot

    conn, scope = graph
    (scope.repository / "alias.py").symlink_to("a.py")
    (scope.repository / "secret.py").write_text('api_key = "abcdefghijklmno"\n')
    oid = commit(scope.repository)
    git(scope.repository, "update-index", "--add", "--cacheinfo", "160000", oid, "submodule.py")
    git(
        scope.repository,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "gitlink",
    )
    refresh(conn, scope)
    raw = b"mutable record"
    sid = identity(scope.project_id, "runtime:test")
    source = SourceVersion(
        sid,
        identity(sid, digest(raw), EXTRACTOR_VERSION, "checkpoint"),
        "runtime:test",
        "checkpoint",
        digest(raw),
        raw,
        {},
        "harness-record",
    )
    active = conn.execute("SELECT active_snapshot_id FROM knowledge_projects").fetchone()[0]
    publish_snapshot(
        conn,
        PreparedSnapshot(
            scope,
            SourceInventory((source,), True, (), {}),
            (Extraction(()),),
            0,
            carry_forward=True,
        ),
        expected_active=active,
    )
    result = refresh(conn, scope, RefreshRequest(commit="HEAD", max_file_bytes=64))
    members = conn.execute(
        "SELECT s.locator,m.membership_state FROM knowledge_snapshot_sources m "
        "JOIN knowledge_sources s USING(source_id) WHERE snapshot_id=?",
        (result.snapshot_id,),
    ).fetchall()
    assert "runtime:test" not in {r[0] for r in members}
    assert all(
        r[1] != "present" for r in members if r[0] in {"alias.py", "secret.py", "submodule.py"}
    )
    with pytest.raises(ValueError, match="inclusions"):
        refresh(conn, scope, RefreshRequest(commit="HEAD", included_paths=("other.py",)))


def test_git_replace_refs_cannot_rewrite_pinned_blob_evidence(graph):
    conn, scope = graph
    commit(scope.repository)
    pinned = refresh(conn, scope, RefreshRequest(commit="HEAD"))
    original = git(scope.repository, "rev-parse", "HEAD:a.py").stdout.decode().strip()
    alternate = git(scope.repository, "rev-parse", "HEAD:b.py").stdout.decode().strip()
    git(scope.repository, "replace", original, alternate)
    bundle = query(conn, scope, ContextRequest("alpha", as_of_snapshot=pinned.snapshot_id))
    assert bundle.payload["assertions"]
    aid = bundle.payload["assertions"][0]["assertion_id"]
    assert (
        "alpha"
        in explain(conn, scope, bundle.bundle_hash, aid, include_evidence=True)["citations"][0][
            "text"
        ]
    )
