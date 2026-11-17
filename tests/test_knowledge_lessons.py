"""Exact local sharing is approval-bound, non-transitive and revocable on every read."""

import sqlite3

import pytest

from harness.db import connect
from harness.knowledge.lessons import (
    approval_marker,
    export_lesson,
    grant,
    import_lesson,
    lesson_body,
    revoke,
)
from harness.knowledge.models import ContextRequest, PreparedSnapshot, SourceInventory, canonical
from harness.knowledge.policy import current_operator_scope, operator_scope
from harness.knowledge.query import explain, query
from harness.knowledge.refresh import refresh
from harness.knowledge.store import ensure_project, publish_snapshot
from tests.test_knowledge_lifecycle import git
from tests.test_knowledge_lifecycle import graph as graph


def approve(scope, body, *, status="approved", marker=None):
    locator = "docs/decisions/DEC-9001-lesson.md"
    path = scope.repository / locator
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# Lesson approval\n\n- **ID:** DEC-9001\n- **Status:** {status}\n- **Version:** 1\n"
        "- **Timestamp:** 2026-11-16T00:00:00+00:00\n\n## Decision\n"
        + (marker if marker is not None else approval_marker(body))
        + "\n"
    )
    return locator


@pytest.fixture
def sharing(graph, tmp_path):
    conn, scope = graph
    refresh(conn, scope)
    body = lesson_body(
        scope.project_id, "portable", 1, "local Python", "Use bounded parsing", "Static only"
    )
    decision = approve(scope, body)
    exported = export_lesson(conn, scope, body, decision)
    root = tmp_path / "destination"
    root.mkdir()
    git(root, "init", "-q")
    other = connect(tmp_path / "destination.db")
    target = operator_scope(root)
    ensure_project(other, target)
    yield conn, scope, other, target, body, decision, exported
    other.close()


def install(conn, scope, payload):
    locator = "docs/knowledge/imports/portable.md"
    path = scope.repository / locator
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload)
    git(scope.repository, "add", locator)
    return import_lesson(conn, scope, locator)


def test_export_exact_approval_and_immutable_version(sharing):
    conn, scope, _, _, body, decision, exported = sharing
    assert export_lesson(conn, scope, body, decision) == exported
    mutated = body.replace(b"bounded", b"unbounded")
    with pytest.raises(PermissionError, match="approval"):
        export_lesson(conn, scope, mutated, decision)
    approve(scope, mutated)
    with pytest.raises(ValueError, match="content-mismatch"):
        export_lesson(conn, scope, mutated, decision)
    for status in ("draft", "rejected", "superseded"):
        approve(scope, body, status=status)
        with pytest.raises(PermissionError, match="approval"):
            export_lesson(conn, scope, body, decision)
    approve(scope, body, marker="Incidentally " + approval_marker(body))
    with pytest.raises(PermissionError, match="approval"):
        export_lesson(conn, scope, body, decision)
    with pytest.raises(PermissionError, match="project"):
        export_lesson(conn, scope, body.replace(scope.project_id.encode(), b"a" * 64), decision)
    with pytest.raises(PermissionError, match="approval"):
        export_lesson(conn, scope, body, "docs/decisions/history/DEC-9001.md")


def test_exact_import_grants_missing_mapping_and_revocation(sharing, tmp_path):
    conn, scope, other, target, _, _, exported = sharing
    assert grant(conn, scope, other, target, "portable", 1, grant_id="one")["status"] == "granted"
    result = install(other, target, exported["payload"])
    bundle = query(other, target, ContextRequest("bounded"))
    assert bundle.payload["assertions"]
    aid = bundle.payload["assertions"][0]["assertion_id"]
    assert bundle.payload["assertions"][0]["verification"] == "unverified"
    assert bundle.payload["assertions"][0]["applicability"]["state"] == "suggestion"
    assert explain(other, target, bundle.bundle_hash, aid, include_evidence=True)["citations"][0][
        "text"
    ]
    row = other.execute("SELECT * FROM knowledge_import_sources").fetchone()
    other.execute("DELETE FROM knowledge_import_sources")
    assert not query(
        other, target, ContextRequest("bounded", as_of_snapshot=result["snapshot_id"])
    ).payload["assertions"]
    other.execute("INSERT INTO knowledge_import_sources VALUES (?,?,?,?)", tuple(row))
    other.execute("UPDATE knowledge_grants SET material_sha256=?", ("f" * 64,))
    assert not query(other, target, ContextRequest("bounded")).payload["assertions"]
    other.execute("UPDATE knowledge_grants SET material_sha256=?", (exported["material_sha256"],))
    other.execute(
        "INSERT INTO knowledge_bundle_pins VALUES (?,?)", (bundle.bundle_hash, "task:pin")
    )
    assert revoke(conn, scope, other, target, "one")["status"] == "revoked"
    target = current_operator_scope(other, target.repository)
    assert not query(
        other, target, ContextRequest("bounded", as_of_snapshot=result["snapshot_id"])
    ).payload["assertions"]
    with pytest.raises(PermissionError, match="bundle-invalidated"):
        explain(other, target, bundle.bundle_hash, aid)
    with pytest.raises(PermissionError, match="grant-revoked"):
        grant(conn, scope, other, target, "portable", 1, grant_id="one")
    assert revoke(conn, scope, other, target, "one")["enforced"]
    assert (
        other.execute(
            "SELECT payload FROM knowledge_bundles WHERE bundle_hash=?", (bundle.bundle_hash,)
        ).fetchone()[0]
        is None
    )


def test_ungranted_import_never_falls_through_as_text(sharing):
    _, _, other, target, _, _, exported = sharing
    with pytest.raises(PermissionError, match="withheld"):
        install(other, target, exported["payload"])
    result = (
        query(other, target, ContextRequest("bounded"))
        if other.execute("SELECT active_snapshot_id FROM knowledge_projects").fetchone()[0]
        else None
    )
    assert result is None or not result.payload["assertions"]


def test_imported_ancestor_forbids_reexport(sharing):
    from harness.knowledge.models import (
        EXTRACTOR_VERSION,
        Assertion,
        Extraction,
        SourceVersion,
        digest,
        identity,
    )

    conn, scope, other, target, _, _, exported = sharing
    grant(conn, scope, other, target, "portable", 1, grant_id="one")
    install(other, target, exported["payload"])
    aid = query(other, target, ContextRequest("bounded")).payload["assertions"][0]["assertion_id"]
    raw = b"local summary"
    sid = identity(target.project_id, "summary.md")
    source = SourceVersion(
        sid,
        identity(sid, digest(raw), EXTRACTOR_VERSION, "text"),
        "summary.md",
        "text",
        digest(raw),
        raw,
        {},
    )
    extraction = Extraction(
        (
            Assertion(
                "summary:x",
                "summarizes",
                None,
                None,
                "summary",
                0,
                len(raw),
                derivation_parents=(aid,),
            ),
        )
    )
    active = other.execute("SELECT active_snapshot_id FROM knowledge_projects").fetchone()[0]
    snap = publish_snapshot(
        other,
        PreparedSnapshot(
            target, SourceInventory((source,), True, (), {}), (extraction,), 0, carry_forward=True
        ),
        expected_active=active,
    )
    child = other.execute(
        "SELECT assertion_id FROM knowledge_snapshot_assertions WHERE "
        "snapshot_id=? AND assertion_id!=?",
        (snap, aid),
    ).fetchone()[0]
    for reference in (aid, child):
        body = lesson_body(
            target.project_id,
            "forward",
            1,
            "local",
            "summary",
            "inference",
            [{"assertion_id": reference, "description": "public description"}],
        )
        with pytest.raises(PermissionError, match="non-transitive"):
            export_lesson(other, target, body, approve(target, body))


def test_local_partial_grant_and_revoke_failures(sharing, monkeypatch):
    import importlib

    module = importlib.import_module("harness.knowledge.lessons")
    conn, scope, other, target, _, _, _ = sharing
    original = module._insert_grant

    def fail_destination(db, selected, row):
        if db is other:
            raise sqlite3.OperationalError("injected destination failure")
        return original(db, selected, row)

    monkeypatch.setattr(module, "_insert_grant", fail_destination)
    assert (
        grant(conn, scope, other, target, "portable", 1, grant_id="retry")["status"]
        == "source-only"
    )
    assert not other.execute("SELECT * FROM knowledge_grants").fetchall()
    monkeypatch.setattr(module, "_insert_grant", original)
    grant(conn, scope, other, target, "portable", 1, grant_id="retry")
    other.execute(
        "CREATE TRIGGER fail_revoke BEFORE UPDATE ON knowledge_grants BEGIN "
        "SELECT RAISE(FAIL,'injected'); END"
    )
    assert revoke(conn, scope, other, target, "retry")["status"] == "revocation-not-enforced"
    assert conn.execute("SELECT revoked_at FROM knowledge_grants").fetchone()[0] is None
    other.execute("DROP TRIGGER fail_revoke")
    conn.execute(
        "CREATE TRIGGER fail_revoke BEFORE UPDATE ON knowledge_grants BEGIN "
        "SELECT RAISE(FAIL,'injected'); END"
    )
    assert revoke(conn, scope, other, target, "retry")["status"] == "revoked-destination-only"
    assert other.execute("SELECT revoked_at FROM knowledge_grants").fetchone()[0]
    conn.execute("DROP TRIGGER fail_revoke")
    target = current_operator_scope(other, target.repository)
    assert revoke(conn, scope, other, target, "retry")["status"] == "revoked"


def test_new_explicit_grant_can_reauthorize_material_without_reviving_old_bundle(sharing):
    conn, scope, other, target, _, _, exported = sharing
    grant(conn, scope, other, target, "portable", 1, grant_id="old")
    install(other, target, exported["payload"])
    old = query(other, target, ContextRequest("bounded"))
    revoke(conn, scope, other, target, "old")
    target = current_operator_scope(other, target.repository)
    grant(conn, scope, other, target, "portable", 1, grant_id="new-explicit-action")
    install(other, target, exported["payload"])
    assert query(other, target, ContextRequest("bounded")).payload["assertions"]
    assert (
        other.execute(
            "SELECT payload FROM knowledge_bundles WHERE bundle_hash=?", (old.bundle_hash,)
        ).fetchone()[0]
        is None
    )


def test_same_portable_bytes_in_third_project_are_not_authorized(sharing, tmp_path):
    conn, scope, other, target, _, _, exported = sharing
    grant(conn, scope, other, target, "portable", 1, grant_id="only-destination")
    root = tmp_path / "third"
    root.mkdir()
    git(root, "init", "-q")
    third = connect(tmp_path / "third.db")
    try:
        selected = operator_scope(root)
        ensure_project(third, selected)
        # Copying bookkeeping intended for a different destination grants no authority.
        row = other.execute("SELECT * FROM knowledge_grants").fetchone()
        third.execute("INSERT INTO knowledge_grants VALUES (?,?,?,?,?,?)", tuple(row))
        with pytest.raises(PermissionError, match="withheld"):
            install(third, selected, exported["payload"])
    finally:
        third.close()


def test_lesson_citations_project_only_observation_with_private_inventory(sharing):
    import json
    from dataclasses import replace

    from harness.knowledge.models import RefreshRequest

    conn, scope, other, target, _, _, exported = sharing
    grant(conn, scope, other, target, "portable", 1, grant_id="observation")
    locator = "docs/knowledge/imports/portable.md"
    path = target.repository / locator
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(exported["payload"])
    git(target.repository, "add", locator)
    private = "private-acquisition.md"
    (target.repository / private).write_text("PRIVATE_INVENTORY")
    a, b = replace(target, task_id="A"), replace(target, task_id="B")
    first = refresh(other, a, RefreshRequest(included_paths=(private,)))
    for _ in range(2):
        bundle = query(other, b, ContextRequest("bounded"))
        assert bundle.payload["assertions"]
        assert private not in json.dumps(bundle.payload)
        observation = json.loads(bundle.payload["citations"][0]["observation_json"])
        assert observation["grant_id"] == "observation"
        assert observation["lesson_sha256"] == exported["material_sha256"]
        assert observation["source_project"] == scope.project_id
        assert observation["destination_project"] == target.project_id
        aid = bundle.payload["assertions"][0]["assertion_id"]
        assert private not in json.dumps(
            explain(other, b, bundle.bundle_hash, aid, include_evidence=True)
        )
        refresh(other, a, RefreshRequest(included_paths=(private,)))
    assert private not in json.dumps(
        query(other, b, ContextRequest("bounded", as_of_snapshot=first.snapshot_id)).payload
    )
    git(
        target.repository,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "lesson",
    )
    refresh(other, b, RefreshRequest(commit="HEAD"))
    bundle = query(other, b, ContextRequest("bounded"))
    assert bundle.payload["assertions"]
    assert private not in json.dumps(bundle.payload)
    aid = bundle.payload["assertions"][0]["assertion_id"]
    assert private not in json.dumps(
        explain(other, b, bundle.bundle_hash, aid, include_evidence=True)
    )


def test_pre_fix_lesson_bundle_explanation_reprojects_citation_metadata(sharing, monkeypatch):
    import importlib
    import json

    conn, scope, other, target, _, _, exported = sharing
    grant(conn, scope, other, target, "portable", 1, grant_id="retained")
    install(other, target, exported["payload"])
    module = importlib.import_module("harness.knowledge.commit_sources")
    original = module.snapshot_observation

    def legacy_observation(db, ref):
        return dict(original(db, ref), task_private_paths=["private-old-acquisition.md"])

    # Issue authentic immutable bundle bytes using the old citation projection shape.
    with monkeypatch.context() as patch:
        patch.setattr(module, "snapshot_observation", legacy_observation)
        bundle = query(other, target, ContextRequest("bounded"))
    assert "private-old-acquisition.md" in json.dumps(bundle.payload)
    aid = bundle.payload["assertions"][0]["assertion_id"]
    for include in (False, True):
        result = explain(other, target, bundle.bundle_hash, aid, include_evidence=include)
        assert "private-old-acquisition.md" not in json.dumps(result)
    assert other.execute(
        "SELECT payload FROM knowledge_bundles WHERE bundle_hash=?", (bundle.bundle_hash,)
    ).fetchone()[0] == canonical(bundle.payload)
