import json
import subprocess
from pathlib import Path

import pytest

from harness.controller import HarnessController
from harness.db import connect
from harness.knowledge.integration import investigate_with_context
from harness.knowledge.models import ContextRequest
from harness.knowledge.policy import operator_scope
from harness.knowledge.query import query
from tests.test_execution_operations import operations as operations
from tests.test_knowledge_lifecycle import graph as graph


def test_native_investigation_records_persistent_shared_context(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_KNOWLEDGE", "on")
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

    monkeypatch.setenv("HARNESS_KNOWLEDGE", "on")
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


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "service.py").write_text("def calculate():\n    return 3\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    return root


def _knowledge_rows(conn):
    return tuple(
        conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("knowledge_projects", "knowledge_snapshots", "knowledge_refresh_attempts")
    )


def test_default_investigation_skips_an_unindexed_repository(tmp_path, monkeypatch):
    """#12: the graph is opt-in, so an unindexed repository pays nothing at investigation."""
    monkeypatch.delenv("HARNESS_KNOWLEDGE", raising=False)
    root = _repo(tmp_path)
    conn = connect(tmp_path / "state.db")
    controller = HarnessController(conn)
    task = controller.create_task("Investigate service calculate")
    result = investigate_with_context(controller, task.id, root)
    assert result["diagnostic"]["status"] == "skipped"
    assert "harness knowledge refresh" in result["diagnostic"]["reason"]
    assert result["context"] is None
    assert _knowledge_rows(conn) == (0, 0, 0)
    assert controller.workspace(task.id).latest_checkpoint("investigation")
    assert controller.workspace(task.id).latest_checkpoint("knowledge-context")


def test_auto_investigation_keeps_an_opted_in_repository_current(tmp_path, monkeypatch):
    from harness.cli_knowledge import refresh_knowledge

    monkeypatch.delenv("HARNESS_KNOWLEDGE", raising=False)
    root = _repo(tmp_path)
    conn = connect(tmp_path / "state.db")
    controller = HarnessController(conn)
    task = controller.create_task("Investigate service calculate")
    refreshed = refresh_knowledge(controller, root, task_id=task.id)
    assert refreshed["summary"]["languages"] == ["python"]
    (root / "service.py").write_text("def calculate():\n    return 4\n")
    result = investigate_with_context(controller, task.id, root)
    assert result["diagnostic"]["status"] in {"published", "published-partial"}
    assert result["diagnostic"]["summary"]["line"].startswith("knowledge graph: ")
    assert result["context"]["assertions"]
    monkeypatch.setenv("HARNESS_KNOWLEDGE", "off")
    before = _knowledge_rows(conn)
    assert investigate_with_context(controller, task.id, root)["diagnostic"]["status"] == "skipped"
    assert _knowledge_rows(conn) == before


def _assignment_scope(operations, paths):
    from tests.test_execution_operations import generated_handoff_fixture
    from tests.test_knowledge_delivery import register

    generated_handoff_fixture(operations)
    reader = register(operations)
    path = Path(operations[3]["source"])
    state = json.loads(path.read_bytes())
    state["integration"]["tasks"]["handoff-task"].update(title="routine maintenance", files=paths)
    path.write_text(json.dumps(state))
    return reader


@pytest.mark.parametrize("scope", ["pkg/api.py", "pkg"])
def test_generic_objective_delivers_affected_source_and_applicable_guidance(operations, scope):
    from harness.knowledge.integration import refresh_with_records
    from harness.knowledge.policy import current_operator_scope

    operation, _, controller, _, _, execution, _ = operations
    root = Path(operation.envelope["repository"])
    (root / "pkg").mkdir()
    (root / "pkg/api.py").write_text("def route_request():\n    return 17\n")
    (root / "pkg/AGENTS.md").write_text("Preserve the stable wire interface.\n")
    (root / "unrelated.py").write_text("def decoy():\n    return 91\n")
    subprocess.run(["git", "-C", str(root), "add", "pkg", "unrelated.py"], check=True)
    reader = _assignment_scope(operations, [scope])
    refresh_with_records(
        controller, current_operator_scope(controller.conn, root, task_id=execution["task_id"])
    )
    value = operation.dispatch(reader, "await_assignment", {"timeout": 1})["knowledge_context"]
    assert value["status"] == "available"
    bundle = value["context"]
    refs = bundle["context"]["request"]["affected_entities"]
    assert "file:pkg/api.py" in refs
    assert all(ref.startswith("file:pkg/") for ref in refs)
    locators = {c["locator"] for c in bundle["citations"]}
    assert {"pkg/api.py", "pkg/AGENTS.md"} <= locators
    assert "unrelated.py" not in locators
    assert "not a complete repository inventory" in value["presentation"]["scope_basis"]
    for citation in bundle["citations"]:
        if citation["locator"] in {"pkg/api.py", "pkg/AGENTS.md"}:
            from harness.knowledge.models import digest

            raw = (root / citation["locator"]).read_bytes()
            assert (
                digest(raw[citation["start_byte"] : citation["end_byte"]])
                == citation["span_sha256"]
            )


def test_directory_expansion_refuses_overflow_without_silently_taking_first_members(operations):
    from harness.knowledge.models import canonical
    from harness.knowledge.policy import current_operator_scope
    from harness.knowledge.refresh import refresh

    operation, _, controller, _, _, _, _ = operations
    root = Path(operation.envelope["repository"])
    (root / "pkg").mkdir()
    for number in range(41):
        (root / f"pkg/m{number}.py").write_text(f"def endpoint_{number}():\n    return {number}\n")
    subprocess.run(["git", "-C", str(root), "add", "pkg"], check=True)
    reader = _assignment_scope(operations, ["pkg"])
    refresh(controller.conn, current_operator_scope(controller.conn, root))
    value = operation.dispatch(reader, "await_assignment", {"timeout": 1})
    assert value["status"] == "ReadOnly"
    context = value["knowledge_context"]
    assert context["status"] == "unavailable"
    assert context["reason"] == "knowledge:directory-scope-exceeds-context-budget"
    assert "read_file" in context["fallback"] and len(canonical(context).encode()) < 1024
    assert controller.conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0] == 0


def test_accepted_long_assignment_scope_gets_exact_query_metadata_budget_fallback(operations):
    from harness.knowledge.models import canonical
    from tests.test_knowledge_workflow_context import seeded

    operation, _, controller, _, _, _, _ = operations
    paths = ["a" * 230 + "/" + "b" * 230 + f"/endpoint_{i}.py" for i in range(40)]
    assert len(paths) == 40 and all(len(("file:" + path).encode()) <= 512 for path in paths)
    reader = _assignment_scope(operations, paths)
    seeded(operations)
    before = controller.conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0]
    value = operation.dispatch(reader, "await_assignment", {"timeout": 1})
    assert value["status"] == "ReadOnly"
    context = value["knowledge_context"]
    assert context["status"] == "unavailable"
    assert context["reason"] == "request metadata exceeds bundle budget"
    assert "read_file" in context["fallback"] and len(canonical(context).encode()) < 1024
    assert (
        controller.conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0] == before
    )
