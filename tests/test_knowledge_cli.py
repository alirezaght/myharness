"""The CLI exposes the same selected-task graph service used by the dashboard."""

import io
import json
from contextlib import redirect_stdout

from harness.cli import main
from harness.cli_knowledge import inspect_knowledge
from harness.controller import HarnessController
from harness.db import connect
from harness.knowledge.models import RefreshRequest
from harness.knowledge.refresh import refresh
from harness.knowledge.store import ensure_project
from tests.test_knowledge_snapshots import graph as graph


def test_task_scoped_knowledge_commands_refresh_query_and_explain(
    db_path, sample_repo, monkeypatch
):
    conn = connect(db_path)
    try:
        task = HarnessController(conn).create_task("inspect repository knowledge")
    finally:
        conn.close()
    monkeypatch.setattr(
        "harness.cli_knowledge.repository_for_task", lambda controller, task_id: sample_repo
    )

    def run(*args):
        output = io.StringIO()
        with redirect_stdout(output):
            assert main(["--db", str(db_path), "knowledge", *args]) == 0
        return json.loads(output.getvalue())

    before = run("inspect", task.id)
    assert before["status"] == "not-indexed"
    refreshed = run("refresh", task.id)
    assert refreshed["status"] == "published"
    bundle = run("query", task.id, "export")
    assert bundle["assertions"]
    selected = bundle["assertions"][0]
    explanation = run("explain", task.id, bundle["bundle_hash"], selected["assertion_id"])
    assert explanation["citations"]
    assert explanation["authority"] == "derived context only"


def test_knowledge_cli_requires_a_real_task(db_path):
    assert main(["--db", str(db_path), "start", "--complexity", "trivial", "seed"]) == 0
    assert main(["--db", str(db_path), "knowledge", "inspect", "t-missing000"]) == 1


def test_inspect_separates_snapshot_readiness_and_latest_refresh(graph):
    conn, scope = graph
    controller = HarnessController(conn)
    empty = inspect_knowledge(controller, scope.repository)
    assert empty["status"] == "not-indexed" and empty["latest_refresh"] is None
    ensure_project(conn, scope)
    assert inspect_knowledge(controller, scope.repository)["status"] == "not-indexed"
    incomplete = refresh(conn, scope, RefreshRequest(max_file_bytes=1))
    failed = inspect_knowledge(controller, scope.repository)
    assert incomplete.status == "incomplete" and failed["active_snapshot_id"] is None
    assert failed["status"] == "not-indexed"
    assert failed["latest_refresh"]["status"] == "incomplete"
    assert failed["latest_refresh"]["coverage"] and failed["snapshot_coverage"] == []
    published = refresh(conn, scope)
    ready = inspect_knowledge(controller, scope.repository)
    assert ready["status"] == "indexed" and ready["active_snapshot_id"] == published.snapshot_id
    assert ready["latest_refresh"]["status"] == "published"
    assert ready["latest_refresh"]["coverage"] == ready["snapshot_coverage"]
    refresh(conn, scope, RefreshRequest(max_file_bytes=1))
    retained = inspect_knowledge(controller, scope.repository)
    assert retained["status"] == "indexed" and retained["latest_refresh"]["status"] == "incomplete"
    for key in (
        "source_count",
        "entity_count",
        "relationship_count",
        "active_snapshot_id",
        "snapshot_coverage",
    ):
        assert retained[key] == ready[key]


def test_inspect_counts_only_entities_reachable_from_active_assertions(graph):
    conn, scope = graph
    controller = HarnessController(conn)
    refresh(conn, scope)
    (scope.repository / "service.py").write_text("def replacement():\n    return 2\n")
    refresh(conn, scope)
    view = inspect_knowledge(controller, scope.repository)
    historical_entities = conn.execute("SELECT count(*) FROM knowledge_entities").fetchone()[0]
    assert view["entity_count"] == historical_entities - 1
    assert conn.execute("SELECT count(*) FROM knowledge_snapshots").fetchone()[0] == 2


def test_cli_evidence_uses_structured_resolver_with_matching_task_scope(graph, monkeypatch):
    from dataclasses import replace

    from harness.cli_knowledge import evidence_knowledge, query_knowledge
    from harness.knowledge.integration import refresh_with_records

    conn, scope = graph
    controller = HarnessController(conn)
    task = controller.create_task("structured knowledge")
    controller.workspace(task.id).checkpoint(
        "context_accepted", {"requirements": {"criteria": [{"id": "AC-01"}], "invariants": []}}
    )
    refresh_with_records(controller, replace(scope, task_id=task.id))
    bundle = query_knowledge(
        controller, scope.repository, entity_refs=(f"task:{task.id}",), task_id=task.id
    )
    assertion = next(a for a in bundle["assertions"] if a["predicate"] == "governed-by")
    from harness.knowledge.sources import read_source

    def confined(root, locator, limit):
        assert not locator.startswith(("harness:", "contribution:"))
        return read_source(root, locator, limit)

    from importlib import import_module

    monkeypatch.setattr(import_module("harness.knowledge.query"), "read_source", confined)
    evidence = evidence_knowledge(
        controller,
        scope.repository,
        bundle["bundle_hash"],
        assertion["assertion_id"],
        task_id=task.id,
    )
    assert any("AC-01" in c["text"] for c in evidence["citations"])
