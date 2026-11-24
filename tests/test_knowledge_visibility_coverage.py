"""Diagnostic visibility and retained snapshot coverage through consumer boundaries."""

import importlib
import json
from dataclasses import replace

import pytest

from harness.cli_knowledge import inspect_knowledge, refresh_knowledge
from harness.controller import HarnessController
from harness.db import connect
from harness.knowledge.models import ContextRequest, RefreshRequest
from harness.knowledge.query import query
from harness.knowledge.refresh import refresh
from tests.test_knowledge_lifecycle import git
from tests.test_knowledge_lifecycle import graph as graph


@pytest.mark.parametrize("failure", ["syntax", "text", "deferred", "extraction"])
def test_private_diagnostic_visibility_current_cached_and_history(graph, monkeypatch, failure):
    conn, scope = graph
    name = "private-acquisition.json" if failure == "text" else "private-acquisition.py"
    (scope.repository / name).write_text("{" if failure == "syntax" else "{}")
    module = importlib.import_module("harness.knowledge.refresh")
    if failure == "deferred":
        monkeypatch.setattr(module, "MAX_PUBLISH_SOURCES", 2)
    if failure == "extraction":
        original = module.extract_source

        def fail(source):
            if source.locator == name:
                raise ValueError("injected extraction failure")
            return original(source)

        monkeypatch.setattr(module, "extract_source", fail)
    a, b = replace(scope, task_id="A"), replace(scope, task_id="B")
    first = refresh(conn, a, RefreshRequest(included_paths=(name,)))
    for snapshot in (None, first.snapshot_id):
        assert name in json.dumps(
            query(conn, a, ContextRequest("alpha", as_of_snapshot=snapshot)).payload
        )
        assert name not in json.dumps(
            query(conn, b, ContextRequest("alpha", as_of_snapshot=snapshot)).payload
        )
    controller = HarnessController(conn)
    assert name in json.dumps(inspect_knowledge(controller, scope.repository, task_id="A"))
    assert name not in json.dumps(inspect_knowledge(controller, scope.repository, task_id="B"))
    refresh(conn, a, RefreshRequest(included_paths=(name,)))
    assert name not in json.dumps(query(conn, b, ContextRequest("alpha")).payload)
    assert name not in json.dumps(inspect_knowledge(controller, scope.repository, task_id="B"))
    if failure in {"syntax", "text"}:
        assert conn.execute(
            "SELECT 1 FROM knowledge_extraction_cache c JOIN knowledge_source_versions v "
            "USING(version_id) JOIN knowledge_sources s USING(source_id) WHERE s.locator=?",
            (name,),
        ).fetchone()


def test_structured_augmentation_preserves_applicable_coverage_after_reopen(graph, tmp_path):
    conn, scope = graph
    (scope.repository / "unsupported.sql").write_text("public limitation")
    git(scope.repository, "add", "unsupported.sql")
    private = "private-malformed.py"
    (scope.repository / private).write_text("{")
    controller = HarnessController(conn)
    task = controller.create_task("Investigate public limitation")
    controller.workspace(task.id).checkpoint("coverage-fixture", {"purpose": "regression"})
    result = refresh_knowledge(
        controller, scope.repository, task_id=task.id, included_paths=(private,)
    )
    assert result["snapshot_id"]
    path = tmp_path / "state.db"
    with connect(path) as reopened:
        reader = HarnessController(reopened)
        a = inspect_knowledge(reader, scope.repository, task_id=task.id)
        b = inspect_knowledge(reader, scope.repository, task_id="B")
        assert any(c.get("locator") == "unsupported.sql" for c in b["snapshot_coverage"])
        assert private in json.dumps(a["snapshot_coverage"])
        assert private not in json.dumps(b)
    (scope.repository / private).write_text("import repaired\n")
    (scope.repository / "unsupported.sql").unlink()
    git(scope.repository, "rm", "--cached", "unsupported.sql")
    refresh_knowledge(controller, scope.repository, task_id=task.id, included_paths=(private,))
    coverage = inspect_knowledge(controller, scope.repository, task_id=task.id)["snapshot_coverage"]
    assert not any(c.get("status") == "syntax-error" for c in coverage)
    assert not any(c.get("status") == "text-only-no-dependency-analysis" for c in coverage)


@pytest.mark.parametrize("failure", ["syntax", "deferred"])
def test_retained_pre_fix_diagnostics_do_not_default_to_public(graph, monkeypatch, failure):
    conn, scope = graph
    private = "private-legacy.py"
    (scope.repository / private).write_text("{")
    a, b = replace(scope, task_id="A"), replace(scope, task_id="B")
    if failure == "deferred":
        monkeypatch.setattr(
            importlib.import_module("harness.knowledge.refresh"), "MAX_PUBLISH_SOURCES", 2
        )
    result = refresh(conn, a, RefreshRequest(included_paths=(private,)))
    # Reproduce retained rows from the previous current-schema publisher.
    for table, key in (
        ("knowledge_snapshots", "snapshot_id"),
        ("knowledge_refresh_attempts", "project_id"),
    ):
        selected = result.snapshot_id if key == "snapshot_id" else scope.project_id
        row = conn.execute(
            f"SELECT coverage_json FROM {table} WHERE {key}=?", (selected,)
        ).fetchone()
        gaps = [
            {k: v for k, v in gap.items() if k not in {"visibility", "version_id"}}
            for gap in json.loads(row[0])
        ]
        conn.execute(
            f"UPDATE {table} SET coverage_json=? WHERE {key}=?", (json.dumps(gaps), selected)
        )
    if failure == "deferred":
        conn.execute(
            "UPDATE knowledge_source_versions SET observation_json=? WHERE source_id IN "
            "(SELECT source_id FROM knowledge_sources WHERE locator=?)",
            (json.dumps({"visibility": "project"}), private),
        )
        conn.execute(
            "UPDATE knowledge_snapshot_source_state SET detail_json=? WHERE snapshot_id=?",
            (
                json.dumps({"locator": private, "reason": "publish-batch-deferred"}),
                result.snapshot_id,
            ),
        )
    assert private not in json.dumps(query(conn, b, ContextRequest("alpha")).payload)
    assert private not in json.dumps(
        inspect_knowledge(HarnessController(conn), scope.repository, task_id="B")
    )
    assert private not in json.dumps(
        query(conn, b, ContextRequest("alpha", as_of_snapshot=result.snapshot_id)).payload
    )


def test_private_unknown_state_does_not_inherit_public_carried_version(graph, monkeypatch):
    conn, scope = graph
    name = "formerly-public.py"
    (scope.repository / name).write_text("import public\n")
    git(scope.repository, "add", name)
    refresh(conn, scope)
    git(scope.repository, "rm", "--cached", name)
    (scope.repository / name).write_text("import private\n")
    (scope.repository / "a.py").write_text("import alpha_changed\n")
    module = importlib.import_module("harness.knowledge.refresh")
    original = module.extract_source

    def fail(source):
        if source.locator == name:
            raise ValueError("injected extraction failure")
        return original(source)

    monkeypatch.setattr(module, "extract_source", fail)
    a, b = replace(scope, task_id="A"), replace(scope, task_id="B")
    refresh(conn, a, RefreshRequest(included_paths=(name,)))
    assert name in json.dumps(query(conn, a, ContextRequest("alpha")).payload)
    assert name not in json.dumps(query(conn, b, ContextRequest("alpha")).payload)


@pytest.mark.parametrize("failure", ["deferred", "extraction"])
def test_public_unknown_diagnostics_remain_available(graph, monkeypatch, failure):
    conn, scope = graph
    name = "public-diagnostic.py"
    (scope.repository / name).write_text("import public\n")
    git(scope.repository, "add", name)
    module = importlib.import_module("harness.knowledge.refresh")
    if failure == "deferred":
        monkeypatch.setattr(module, "MAX_PUBLISH_SOURCES", 2)
    else:
        original = module.extract_source

        def fail(source):
            if source.locator == name:
                raise ValueError("injected extraction failure")
            return original(source)

        monkeypatch.setattr(module, "extract_source", fail)
    refresh(conn, scope)
    for task in ("A", "B"):
        assert name in json.dumps(
            query(conn, replace(scope, task_id=task), ContextRequest("alpha")).payload
        )
        assert name in json.dumps(
            inspect_knowledge(HarnessController(conn), scope.repository, task_id=task)
        )


@pytest.mark.parametrize("success", ["published", "unchanged"])
@pytest.mark.parametrize("same_second", [True, False])
def test_latest_incomplete_attempt_same_timestamp_and_success_recovery(
    graph, monkeypatch, success, same_second
):
    conn, scope = graph
    store = importlib.import_module("harness.knowledge.store")
    module = importlib.import_module("harness.knowledge.refresh")
    clock = {"now": "2026-11-17T12:00:00+00:00"}
    monkeypatch.setattr(store, "utcnow", lambda: clock["now"])
    initial = refresh(conn, scope)
    if not same_second:
        clock["now"] = "2026-11-17T12:00:01+00:00"
    (scope.repository / "a.py").write_text("import changed\n")
    original = module.extract_source

    def fail(source):
        if source.locator == "a.py":
            raise ValueError("injected extraction failure")
        return original(source)

    monkeypatch.setattr(module, "extract_source", fail)
    failed = refresh(conn, scope)
    assert failed.status == "incomplete" and failed.snapshot_id == initial.snapshot_id
    assert any(
        c.get("reason") == "extraction-failed"
        for c in query(conn, scope, ContextRequest("beta")).payload["coverage"]
    )
    monkeypatch.setattr(module, "extract_source", original)
    if not same_second:
        clock["now"] = "2026-11-17T12:00:02+00:00"
    if success == "unchanged":
        (scope.repository / "a.py").write_text("import alpha\n")
    assert refresh(conn, scope).status == success
    assert not any(
        c.get("reason") == "extraction-failed"
        for c in query(conn, scope, ContextRequest("beta")).payload["coverage"]
    )
    assert not any(
        c.get("reason") == "extraction-failed"
        for c in inspect_knowledge(HarnessController(conn), scope.repository)["latest_refresh"][
            "coverage"
        ]
    )


@pytest.mark.parametrize("limitation", ["unavailable", "unavailable-all", "cap"])
def test_task_record_diagnostics_are_private_to_the_selected_task(graph, monkeypatch, limitation):
    conn, scope = graph
    controller = HarnessController(conn)
    refresh(conn, scope)
    b = replace(scope, task_id="B")
    before = query(conn, b, ContextRequest("alpha")).payload
    before_counts = inspect_knowledge(controller, scope.repository, task_id="B")
    task = controller.create_task("Private task record fixture")
    controller.workspace(task.id).checkpoint("private-record", {})
    adapters = importlib.import_module("harness.knowledge.adapters")
    integration = importlib.import_module("harness.knowledge.integration")
    original = adapters._payload
    if limitation == "cap":
        real_sources = adapters.task_sources
        monkeypatch.setattr(
            integration, "task_sources", lambda c, s, t: real_sources(c, s, t, max_records=1)
        )
        status = "structured-record-cap"
    else:

        def fail(selected, kind, key):
            if kind == "checkpoint" or limitation == "unavailable-all":
                raise ValueError("injected record failure")
            return original(selected, kind, key)

        monkeypatch.setattr(adapters, "_payload", fail)
        status = "structured-source-unavailable"
    result = integration.refresh_with_records(controller, replace(scope, task_id=task.id))
    assert status in json.dumps(
        query(conn, replace(scope, task_id=task.id), ContextRequest("alpha")).payload["coverage"]
    )
    after = query(conn, b, ContextRequest("alpha")).payload
    after_counts = inspect_knowledge(controller, scope.repository, task_id="B")
    assert before["coverage"] == after["coverage"]
    assert status not in json.dumps(after_counts)
    for name in ("source_count", "entity_count", "relationship_count"):
        assert before_counts[name] == after_counts[name]
    assert status not in json.dumps(
        query(conn, b, ContextRequest("alpha", as_of_snapshot=result.snapshot_id)).payload
    )

    # Retained old-shaped task diagnostics cannot acquire a public default on read.
    row = conn.execute(
        "SELECT coverage_json FROM knowledge_snapshots WHERE snapshot_id=?", (result.snapshot_id,)
    ).fetchone()
    gaps = [{k: v for k, v in gap.items() if k != "visibility"} for gap in json.loads(row[0])]
    conn.execute(
        "UPDATE knowledge_snapshots SET coverage_json=? WHERE snapshot_id=?",
        (json.dumps(gaps), result.snapshot_id),
    )
    assert status not in json.dumps(query(conn, b, ContextRequest("alpha")).payload)
    assert status not in json.dumps(
        inspect_knowledge(controller, scope.repository, task_id="B")["snapshot_coverage"]
    )
    monkeypatch.setattr(adapters, "_payload", original)
    monkeypatch.setattr(integration, "task_sources", adapters.task_sources)
    integration.refresh_with_records(controller, replace(scope, task_id=task.id))
    assert status not in json.dumps(
        query(conn, replace(scope, task_id=task.id), ContextRequest("alpha")).payload["coverage"]
    )
