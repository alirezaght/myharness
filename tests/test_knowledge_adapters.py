"""Adapters use actual Harness contracts and retain safe version-bound provenance."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from harness.controller import HarnessController
from harness.decisions import parse_decision_text, render_record
from harness.knowledge.adapters import task_sources
from harness.knowledge.extract import extract_source
from harness.knowledge.models import (
    EXTRACTOR_VERSION,
    ContextRequest,
    PreparedSnapshot,
    SourceInventory,
    SourceVersion,
    digest,
    identity,
)
from harness.knowledge.policy import operator_scope
from harness.knowledge.query import explain, query
from harness.knowledge.store import publish_snapshot


def test_decision_parses_retained_bytes_without_reopening(tmp_path, monkeypatch):
    text = render_record(
        "DEC-0100",
        "Design",
        2,
        "now",
        "service",
        "DEC-0099",
        "Use local SQLite",
        "Bounded cost",
        "No service",
    )
    path = tmp_path / "DEC-0100.md"
    path.write_text("different mutable content")
    monkeypatch.setattr(
        Path, "read_text", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("reread"))
    )
    parsed = parse_decision_text(text, path)
    assert parsed.id == "DEC-0100" and parsed.version == 2
    source = SourceVersion(
        "s", "v", "docs/decisions/DEC-0100.md", "text", digest(text.encode()), text.encode(), {}
    )
    extraction = extract_source(source)
    assert any(
        a.subject == "decision:DEC-0100:v2"
        and a.object == "decision:DEC-0100"
        and a.predicate == "version-of"
        for a in extraction.assertions
    )
    assert any(
        a.object == "decision:DEC-0099" and a.predicate == "supersedes"
        for a in extraction.assertions
    )
    assert all(a.verification == "source-backed" for a in extraction.assertions)


def test_structured_sources_are_allowlisted_and_citations_revalidate(conn, tmp_path):
    controller = HarnessController(conn)
    task = controller.create_task("Build service")
    ws = controller.workspace(task.id)
    ws.checkpoint(
        "context_accepted",
        {
            "requirements": {"criteria": [{"id": "AC-01"}], "invariants": [{"id": "I1"}]},
            "stdout": "FORBIDDEN_LOG_CANARY",
        },
    )
    ws.checkpoint("investigation", {"arguments": "FORBIDDEN_ARGUMENT_CANARY"})
    root = tmp_path / "repo"
    root.mkdir()
    scope = replace(operator_scope(root), task_id=task.id)
    sources, coverage = task_sources(controller, scope, task.id)
    assert not coverage
    assert b"FORBIDDEN" not in b"".join(s.raw for s in sources)
    assert {s.kind for s in sources} == {"requirements", "checkpoint", "task-event"}
    inventory = SourceInventory(sources, True, (), {})
    snapshot = publish_snapshot(
        conn,
        PreparedSnapshot(scope, inventory, tuple(extract_source(s) for s in sources), 0),
        expected_active=None,
    )
    bundle = query(conn, scope, ContextRequest(entity_refs=(f"task:{task.id}",)))
    assert bundle.payload["assertions"] and bundle.payload["citations"]
    governed = next(
        a
        for a in bundle.payload["assertions"]
        if a["predicate"] == "governed-by" and a["object"].endswith(":AC-01")
    )
    assert governed["object"] == f"requirement:{task.id}:AC-01"
    assert explain(conn, scope, bundle.bundle_hash, governed["assertion_id"])["citations"]
    assert (
        conn.execute("SELECT material_class FROM knowledge_source_class LIMIT 1").fetchone()[0]
        == "harness-record"
    )
    assert (
        conn.execute("SELECT active_snapshot_id FROM knowledge_projects").fetchone()[0] == snapshot
    )


def test_artifact_citation_uses_confined_immutable_binding(conn, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    raw = b"immutable artifact description"
    (root / "safe.md").write_bytes(raw)
    scope = operator_scope(root)
    sid = identity(scope.project_id, "artifact:bound")
    source = SourceVersion(
        sid,
        identity(sid, digest(raw), EXTRACTOR_VERSION, "artifact"),
        "artifact:bound",
        "artifact",
        digest(raw),
        raw,
        {"artifact_locator": "safe.md", "artifact_sha256": digest(raw)},
        "runtime-record",
    )
    inventory = SourceInventory((source,), True, (), {})
    publish_snapshot(
        conn, PreparedSnapshot(scope, inventory, (extract_source(source),), 0), expected_active=None
    )
    bundle = query(conn, scope, ContextRequest("immutable"))
    assert bundle.payload["assertions"]
    (root / "safe.md").write_text("changed")
    with pytest.raises(ValueError, match="stale"):
        explain(conn, scope, bundle.bundle_hash, bundle.payload["assertions"][0]["assertion_id"])


def test_requirement_record_contains_identity_not_arbitrary_source_payload(conn, tmp_path):
    controller = HarnessController(conn)
    task = controller.create_task("task")
    controller.workspace(task.id).checkpoint(
        "context_accepted",
        {
            "requirements": {
                "criteria": [{"id": "AC-01", "stdout": "secret"}],
                "invariants": [],
                "unknown": {"transcript": "secret"},
            }
        },
    )
    scope = replace(operator_scope(tmp_path), task_id=task.id)
    sources, _ = task_sources(controller, scope, task.id)
    payload = json.loads(next(s.raw for s in sources if s.kind == "requirements"))
    assert payload["criteria"] == ["AC-01"] and "secret" not in json.dumps(payload)


def test_validated_execution_projection_preserves_failed_receipts(
    controller, sample_repo, monkeypatch
):
    from harness.execution_projection import ingest_event
    from tests.execution_helpers import approved_execution, event, started

    task_id, execution_id = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, execution_id)
    failed = event(
        controller.conn,
        execution_id,
        2,
        "test.result",
        {
            "id": "E-test",
            "area": "service",
            "status": "FAIL",
            "scope": ["service.py"],
            "validity": "INVALID",
            "stdout": "FORBIDDEN_STDOUT_CANARY",
            "arguments": "FORBIDDEN_ARGUMENTS",
        },
    )
    ingest_event(controller.conn, execution_id, failed)
    before = [tuple(r) for r in controller.conn.execute("SELECT * FROM execution_events")]
    scope = replace(
        operator_scope(sample_repo), task_id=task_id, execution_id=execution_id, epoch_id="legacy-0"
    )
    sources, _ = task_sources(controller, scope, task_id, max_records=2000)
    execution_sources = [s for s in sources if s.kind == "execution-event"]
    assert len(execution_sources) == 2
    raw = b"".join(s.raw for s in execution_sources)
    assert b'"FAIL"' in raw and b"FORBIDDEN" not in raw
    extraction = extract_source(execution_sources[-1])
    assert any(
        a.predicate == "observed-status" and a.value == "FAIL" for a in extraction.assertions
    )
    assert all(a.verification == "source-backed" for a in extraction.assertions)
    assert before == [tuple(r) for r in controller.conn.execute("SELECT * FROM execution_events")]


def test_effective_design_is_projected_with_existing_entry_identity(controller, tmp_path):
    from harness.design_models import DesignSection

    task = controller.create_task("Design component")
    ws = controller.workspace(task.id)
    effective = ws.state_design(DesignSection.COMPONENTS, "Use the existing shared service")
    ws.propose_design(DesignSection.COMPONENTS, "Unapproved alternative", "speculative")
    scope = replace(operator_scope(tmp_path), task_id=task.id)
    sources, _ = task_sources(controller, scope, task.id)
    design = [s for s in sources if s.kind == "design"]
    assert len(design) == 1
    value = json.loads(design[0].raw)
    assert value["entry_id"] == effective.ref and value["binding"] == "fixed"
    assert b"Unapproved alternative" not in b"".join(s.raw for s in sources)


def test_context_binding_marks_unbounded_requirement_versions_unavailable(conn, tmp_path):
    controller = HarnessController(conn)
    task = controller.create_task("Bound context requirements")
    ws = controller.workspace(task.id)
    accepted = {"requirements": {"criteria": [{"id": "AC-01"}], "invariants": []}}
    ws.checkpoint("context_accepted", accepted)
    root = tmp_path / "binding-repo"
    root.mkdir()
    scope = replace(operator_scope(root), task_id=task.id)
    sources, _ = task_sources(controller, scope, task.id)
    publish_snapshot(
        conn,
        PreparedSnapshot(
            scope,
            SourceInventory(sources, True, (), {}),
            tuple(extract_source(s) for s in sources),
            0,
        ),
        expected_active=None,
    )
    first = query(conn, scope, ContextRequest(entity_refs=(f"task:{task.id}",), stage="planning"))
    binding = first.payload["context"]["binding"]
    assert binding["source_versions"]["requirements"] is None
    assert binding["requirements_unavailable"] == "registry-dependency-reads-not-bounded"
    assert binding["source_versions"]["spec"] is None
    ws.checkpoint("context_accepted", accepted)
    changed = query(conn, scope, ContextRequest(entity_refs=(f"task:{task.id}",), stage="planning"))
    # Missing registry versions are not represented as freshly validated current content.
    assert changed.payload["context"]["binding"]["source_versions"]["requirements"] is None
    assert changed.payload["staleness"]
    assert not any(
        c["locator"] == f"harness:requirements:{task.id}" for c in changed.payload["citations"]
    )


def test_changed_task_binding_cannot_publish_a_bundle(conn, tmp_path, monkeypatch):
    import importlib

    from harness.design_models import DesignSection

    module = importlib.import_module("harness.knowledge.query")
    controller = HarnessController(conn)
    task = controller.create_task("Binding publication race")
    ws = controller.workspace(task.id)
    accepted = {"requirements": {"criteria": [{"id": "AC-01"}], "invariants": []}}
    ws.checkpoint("context_accepted", accepted)
    entry = ws.state_design(DesignSection.COMPONENTS, "Original component")
    scope = replace(operator_scope(tmp_path), task_id=task.id)
    sources, _ = task_sources(controller, scope, task.id)
    publish_snapshot(
        conn,
        PreparedSnapshot(
            scope,
            SourceInventory(sources, True, (), {}),
            tuple(extract_source(s) for s in sources),
            0,
        ),
        expected_active=None,
    )
    original = module.derive_binding
    observations = 0

    def change_design(*args, **kwargs):
        nonlocal observations
        observations += 1
        if observations == 2:
            conn.execute(
                "UPDATE design_entries SET statement=? WHERE id=?", ("Changed component", entry.id)
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "derive_binding", change_design)
    with pytest.raises(PermissionError, match="binding changed"):
        query(conn, scope, ContextRequest("AC-01", stage="planning"))
    assert observations == 2
    assert conn.execute("SELECT count(*) FROM knowledge_bundles").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0] == 0
