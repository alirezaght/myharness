import subprocess

from harness.controller import HarnessController
from harness.db import connect
from harness.knowledge.integration import investigate_with_context
from harness.knowledge.models import ContextRequest
from harness.knowledge.policy import operator_scope
from harness.knowledge.query import query
from tests.test_knowledge_lifecycle import graph as graph


def test_native_investigation_records_persistent_shared_context(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "service.py").write_text("def calculate():\n    return 3\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    path = tmp_path / "state.db"
    conn = connect(path)
    controller = HarnessController(conn)
    task = controller.create_task("Investigate service calculate")
    result = investigate_with_context(controller, task.id, root)
    assert result["context"]["assertions"]
    assert controller.workspace(task.id).latest_checkpoint("investigation")
    receipt = controller.workspace(task.id).latest_checkpoint("knowledge-context")
    assert receipt
    conn.close()
    conn = connect(path)
    later = query(conn, operator_scope(root), ContextRequest("calculate"))
    assert later.payload["assertions"]
    assert conn.execute("SELECT count(*) FROM knowledge_bundle_pins").fetchone()[0] == 1
    conn.close()


def test_record_augmentation_withholds_purged_versions(graph):
    from dataclasses import replace

    from harness.knowledge.integration import refresh_with_records
    from harness.knowledge.policy import current_operator_scope
    from harness.knowledge.retention import purge

    conn, scope = graph
    controller = HarnessController(conn)
    task = controller.create_task("Investigate imports")
    controller.workspace(task.id).checkpoint("purge-fixture", {})
    scope = replace(scope, task_id=task.id)
    refresh_with_records(controller, scope)
    version = conn.execute(
        "SELECT v.version_id FROM knowledge_source_versions v "
        "JOIN knowledge_sources s USING(source_id) WHERE s.kind='checkpoint' LIMIT 1"
    ).fetchone()[0]
    purge(conn, scope, [version])
    scope = current_operator_scope(conn, scope.repository, task_id=task.id)
    result = refresh_with_records(controller, scope)
    assert result.status == "published-partial"
    assert any(c.get("reason") == "purged" for c in result.coverage)
    assert not conn.execute(
        "SELECT * FROM knowledge_extraction_cache WHERE version_id=?", (version,)
    ).fetchall()


def test_investigation_uses_returned_receipt_not_latest_same_bundle(graph, monkeypatch):
    from importlib import import_module

    conn, scope = graph
    controller = HarnessController(conn)
    task = controller.create_task("Investigate alpha")
    integration = import_module("harness.knowledge.integration")
    actual = integration.query
    issued = []

    def interleaved(*args, **kwargs):
        first = actual(*args, **kwargs)
        second = actual(*args, **kwargs)
        assert first.bundle_hash == second.bundle_hash
        assert first.receipt_id != second.receipt_id
        issued.append(first.receipt_id)
        return first

    monkeypatch.setattr(integration, "query", interleaved)
    result = integration.investigate_with_context(controller, task.id, scope.repository)
    assert result["context"]["receipt_id"] == issued[0]
    checkpoint = controller.workspace(task.id).latest_checkpoint("knowledge-context")
    assert checkpoint.payload["receipt"]["receipt_id"] == issued[0]
