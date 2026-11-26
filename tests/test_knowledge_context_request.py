"""Preference validation and immutable server binding, without delivery claims."""

from dataclasses import FrozenInstanceError

import pytest

from harness.knowledge.context_request import ContextBinding, preferences
from harness.knowledge.models import ContextRequest, QueryLimits
from tests.test_knowledge_snapshots import graph as graph


def test_existing_four_positional_fields_remain_compatible():
    limits = QueryLimits()
    request = ContextRequest("query", ("module:service",), None, limits)
    assert preferences(request)["entity_refs"] == ["module:service"]
    assert request.limits is limits
    with pytest.raises(TypeError):
        ContextRequest("query", (), None, limits, 1)


@pytest.mark.parametrize(
    "changes",
    [
        {"request_version": True},
        {"request_version": 2},
        {"stage": "approve"},
        {"stage": []},
        {"objective": "x" * 4097},
        {"objective": "a\0b"},
        {"affected_entities": ("x" * 513,)},
        {"affected_entities": ("module:x",) * 41},
        {"estimated_tokens": True},
        {"estimated_tokens": 0},
        {"estimated_tokens": 32769},
    ],
)
def test_preferences_cannot_smuggle_authority_or_unbounded_material(changes):
    with pytest.raises(ValueError):
        preferences(ContextRequest(**changes))
    with pytest.raises(TypeError):
        ContextRequest(project_id="other-project")


def test_binding_exposes_copies_and_is_immutable():
    binding = ContextBinding('{"source_versions":{"spec":null}}')
    before = binding.identity
    binding.to_dict()["source_versions"]["spec"] = "forged"
    assert binding.to_dict()["source_versions"]["spec"] is None
    assert binding.identity == before
    with pytest.raises(FrozenInstanceError):
        binding.encoded = "{}"


def binding_setup(graph, *, designs=0):
    from dataclasses import replace

    from harness.controller import HarnessController
    from harness.design_models import DesignSection
    from harness.knowledge.refresh import refresh

    conn, scope = graph
    snapshot = refresh(conn, scope).snapshot_id
    controller = HarnessController(conn)
    task = controller.create_task("Bounded binding")
    ws = controller.workspace(task.id)
    entries = [ws.state_design(DesignSection.COMPONENTS, f"Component {i}") for i in range(designs)]
    return conn, replace(scope, task_id=task.id), snapshot, ws, entries


def binding_reads():
    return {"count": 0, "bytes": 0, "max_count": 80, "max_bytes": 64 * 1024 * 1024}


def test_binding_caps_design_subset_without_registry_traversal(graph, monkeypatch):
    from harness.knowledge import adapters
    from harness.knowledge.context_request import derive_binding

    conn, scope, snapshot, _, entries = binding_setup(graph, designs=202)
    monkeypatch.setattr(adapters, "registry", lambda *_a, **_k: pytest.fail("registry traversed"))
    reads = binding_reads()
    value = derive_binding(conn, scope, snapshot, reads).to_dict()
    selected = value["source_versions"]["design"]
    assert selected["selected_ids"] == [e.id for e in entries[:2]]
    assert selected["selection_ids"] == [e.id for e in entries[:3]]
    assert selected["overflow"] and not selected["complete"]
    assert len(selected["versions"]) == 2
    assert value["source_versions"]["requirements"] is None
    assert reads["count"] == 3  # snapshot and two actual design bodies
    assert 0 < reads["bytes"] <= 4 * 1024 * 1024


@pytest.mark.parametrize("field", ["statement", "response", "rationale"])
def test_oversized_design_fields_are_not_reconstructed(graph, monkeypatch, field):
    from harness.knowledge import adapters
    from harness.knowledge.context_request import derive_binding
    from harness.knowledge.models import MAX_SOURCE_BYTES

    conn, scope, snapshot, _, entries = binding_setup(graph, designs=1)
    conn.execute(
        f"UPDATE design_entries SET {field}=? WHERE id=?",
        ("x" * (MAX_SOURCE_BYTES + 1), entries[0].id),
    )
    canonical = adapters.canonical

    def bounded_encoding(value):
        assert not any(isinstance(v, str) and len(v) > MAX_SOURCE_BYTES for v in value.values())
        return canonical(value)

    monkeypatch.setattr(adapters, "canonical", bounded_encoding)
    reads = binding_reads()
    selected = derive_binding(conn, scope, snapshot, reads).to_dict()["source_versions"]["design"]
    assert selected["versions"] == [] and not selected["complete"]
    assert selected["unavailable"] == [{"id": entries[0].id, "reason": "binding-byte-budget"}]
    assert reads["count"] == 2 and reads["bytes"] < MAX_SOURCE_BYTES


def test_oversized_snapshot_is_refused_before_decoding(graph, monkeypatch):
    import json

    from harness.knowledge import context_request
    from harness.knowledge.models import MAX_SOURCE_BYTES

    conn, scope, snapshot, _, _ = binding_setup(graph)
    conn.execute(
        "UPDATE knowledge_snapshots SET observation_json=? WHERE snapshot_id=?",
        (json.dumps({"padding": "x" * (MAX_SOURCE_BYTES + 1)}), snapshot),
    )
    monkeypatch.setattr(context_request.json, "loads", lambda *_: pytest.fail("oversized decode"))
    reads = binding_reads()
    with pytest.raises(PermissionError, match="snapshot binding unavailable"):
        context_request.derive_binding(conn, scope, snapshot, reads)
    assert reads["count"] == 1 and reads["bytes"] == 0


def test_oversized_spec_is_unavailable_without_body_encoding(graph, monkeypatch):
    from harness.knowledge import adapters
    from harness.knowledge.context_request import derive_binding
    from harness.knowledge.models import MAX_SOURCE_BYTES

    conn, scope, snapshot, _, _ = binding_setup(graph)
    conn.execute(
        "INSERT INTO spec_versions(task_id,version,path,revision,working_tree,"
        "content_hash,status,created_at) "
        "VALUES (?,1,'spec.md',?,'clean','hash','approved','now')",
        (scope.task_id, "x" * (MAX_SOURCE_BYTES + 1)),
    )
    canonical = adapters.canonical

    def bounded_encoding(value):
        assert not any(isinstance(v, str) and len(v) > MAX_SOURCE_BYTES for v in value.values())
        return canonical(value)

    monkeypatch.setattr(adapters, "canonical", bounded_encoding)
    reads = binding_reads()
    spec = derive_binding(conn, scope, snapshot, reads).to_dict()["source_versions"]["spec"]
    assert spec["content_version"] is None and spec["unavailable"] == "binding-byte-budget"
    assert reads["count"] == 2 and reads["bytes"] < MAX_SOURCE_BYTES


@pytest.mark.parametrize("change", ["statement", "status", "withdrawal", "selection"])
def test_same_ids_never_stand_in_for_fresh_design_content(graph, change):
    from harness.knowledge.context_request import derive_binding

    conn, scope, snapshot, ws, entries = binding_setup(graph, designs=3)
    first = derive_binding(conn, scope, snapshot, binding_reads())
    if change == "statement":
        conn.execute("UPDATE design_entries SET statement='Changed' WHERE id=?", (entries[0].id,))
    elif change == "status":
        conn.execute("UPDATE design_entries SET status='accepted' WHERE id=?", (entries[0].id,))
    else:
        ws.withdraw_design(entries[0].id if change == "withdrawal" else entries[2].id)
    assert derive_binding(conn, scope, snapshot, binding_reads()) != first


def test_restricted_binding_does_not_inspect_hidden_task_or_snapshot_bodies(graph, monkeypatch):
    from dataclasses import replace

    from harness.knowledge import adapters
    from harness.knowledge.context_request import derive_binding

    conn, scope, snapshot, _, entries = binding_setup(graph, designs=3)
    restricted = replace(scope, permitted_source_versions=frozenset())
    monkeypatch.setattr(
        adapters, "binding_record", lambda *_a, **_k: pytest.fail("hidden body read")
    )
    reads = binding_reads()
    first = derive_binding(conn, restricted, snapshot, reads)
    conn.execute("UPDATE design_entries SET statement='Hidden change' WHERE id=?", (entries[0].id,))
    conn.execute(
        "UPDATE knowledge_snapshots SET observation_json='{}' WHERE snapshot_id=?", (snapshot,)
    )
    assert derive_binding(conn, restricted, snapshot, reads) == first
    assert reads["count"] == reads["bytes"] == 0


def test_guarded_body_fetch_refuses_growth_after_length_preflight(graph):
    from harness.knowledge.adapters import binding_record
    from harness.knowledge.models import MAX_SOURCE_BYTES

    conn, scope, _, _, entries = binding_setup(graph, designs=1)

    class ChangedConnection:
        def execute(self, sql, args):
            cursor = conn.execute(sql, args)
            if sql.startswith("SELECT coalesce(length(CAST(statement AS BLOB))"):
                conn.execute(
                    "UPDATE design_entries SET statement=? WHERE id=?",
                    ("x" * (MAX_SOURCE_BYTES + 1), entries[0].id),
                )
            return cursor

    reads = binding_reads()
    value, reason = binding_record(
        ChangedConnection(),
        "design_entries",
        {"statement": "statement"},
        "task_id=? AND id=?",
        (scope.task_id, entries[0].id),
        reads,
        {"count": 0, "bytes": 0},
    )
    assert value is None and reason == "binding-source-changed"
    assert reads["count"] == 1 and reads["bytes"] == 0


def test_all_four_binding_body_slots_and_bytes_are_charged(graph):
    from harness.knowledge.context_request import derive_binding
    from harness.knowledge.models import canonical

    conn, scope, snapshot, _, entries = binding_setup(graph, designs=2)
    conn.execute(
        "INSERT INTO spec_versions(task_id,version,path,revision,working_tree,"
        "content_hash,status,created_at) "
        "VALUES (?,1,'spec.md','revision','clean','hash','approved','now')",
        (scope.task_id,),
    )
    reads = binding_reads()
    value = derive_binding(conn, scope, snapshot, reads).to_dict()
    snapshot_body = {
        "observation_json": conn.execute(
            "SELECT observation_json FROM knowledge_snapshots WHERE snapshot_id=?",
            (snapshot,),
        ).fetchone()[0]
    }
    spec_body = {
        "version": 1,
        "content_hash": "hash",
        "status": "approved",
        "revision": "revision",
        "approval_id": None,
    }
    bodies = [snapshot_body, spec_body]
    for entry in entries:
        bodies.append(
            dict(
                conn.execute(
                    "SELECT task_id,section,binding,status,statement,response,rationale "
                    "FROM design_entries "
                    "WHERE id=?",
                    (entry.id,),
                ).fetchone()
            )
        )
    assert reads["count"] == 4
    assert reads["bytes"] == sum(len(canonical(body).encode()) for body in bodies)
    assert value["source_versions"]["spec"]["content_version"]
    assert value["source_versions"]["design"]["complete"]


@pytest.mark.parametrize("change", ["spec-revision", "spec-status", "snapshot"])
def test_mutable_record_ids_do_not_mask_spec_or_snapshot_content_changes(graph, change):
    from harness.knowledge.context_request import derive_binding

    conn, scope, snapshot, _, _ = binding_setup(graph)
    conn.execute(
        "INSERT INTO spec_versions(task_id,version,path,revision,working_tree,"
        "content_hash,status,created_at) "
        "VALUES (?,1,'spec.md','revision','clean','hash','approved','now')",
        (scope.task_id,),
    )
    first = derive_binding(conn, scope, snapshot, binding_reads())
    if change == "spec-revision":
        conn.execute(
            "UPDATE spec_versions SET revision='changed' WHERE task_id=?", (scope.task_id,)
        )
    elif change == "spec-status":
        conn.execute(
            "UPDATE spec_versions SET status='superseded' WHERE task_id=?", (scope.task_id,)
        )
    else:
        conn.execute(
            "UPDATE knowledge_snapshots SET observation_json='{}' WHERE snapshot_id=?", (snapshot,)
        )
    assert derive_binding(conn, scope, snapshot, binding_reads()) != first


@pytest.mark.parametrize(
    "change",
    [
        {"limits": QueryLimits(bytes=30000)},
        {"limits": QueryLimits(assertions=39)},
        {"limits": QueryLimits(hops=1)},
        {"limits": QueryLimits(examined_edges=199)},
        {"as_of_snapshot": "explicit-current"},
    ],
)
def test_request_identity_binds_every_limit_and_historical_selector(graph, change):
    from dataclasses import replace

    from harness.knowledge.query import query
    from harness.knowledge.refresh import refresh

    conn, scope = graph
    snapshot = refresh(conn, scope).snapshot_id
    request = ContextRequest("service", stage="implementation")
    if "as_of_snapshot" in change:
        change = {"as_of_snapshot": snapshot}
    first = query(conn, scope, request)
    second = query(conn, scope, replace(request, **change))
    assert first.payload["assertions"] == second.payload["assertions"]
    assert (
        first.payload["context"]["request_identity"]
        != second.payload["context"]["request_identity"]
    )


def explanation_setup(graph):
    from harness.knowledge.query import query

    conn, scope, _, _, entries = binding_setup(graph, designs=1)
    conn.execute(
        "INSERT INTO spec_versions(task_id,version,path,revision,working_tree,"
        "content_hash,status,created_at) "
        "VALUES (?,1,'spec.md','revision','clean','hash','approved','now')",
        (scope.task_id,),
    )
    bundle = query(conn, scope, ContextRequest("service", stage="implementation"))
    aid = bundle.payload["assertions"][0]["assertion_id"]
    return conn, scope, entries[0].id, bundle, aid


@pytest.mark.parametrize("change", ["design-response", "design-status", "spec"])
@pytest.mark.parametrize("publication_race", [False, True])
def test_explain_refuses_changed_bound_context_without_issuing_receipt(
    graph, monkeypatch, change, publication_race
):
    from importlib import import_module

    module = import_module("harness.knowledge.query")
    conn, scope, entry, bundle, aid = explanation_setup(graph)
    before = conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0]
    source_bytes = (scope.repository / "service.py").read_bytes()

    def mutate():
        if change == "spec":
            conn.execute(
                "UPDATE spec_versions SET revision='changed' WHERE task_id=?", (scope.task_id,)
            )
        else:
            field = "response" if change == "design-response" else "status"
            value = "Changed response" if change == "design-response" else "accepted"
            conn.execute(f"UPDATE design_entries SET {field}=? WHERE id=?", (value, entry))

    if publication_race:
        original = module._fit_explanation

        def fit(*args):
            original(*args)
            mutate()

        monkeypatch.setattr(module, "_fit_explanation", fit)
    else:
        mutate()
    with pytest.raises(PermissionError, match="stale.*context|context.*changed"):
        module.explain(conn, scope, bundle.bundle_hash, aid)
    assert conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0] == before
    assert (scope.repository / "service.py").read_bytes() == source_bytes


def test_unchanged_explanation_charges_both_binding_passes_and_cited_bytes(graph, monkeypatch):
    import json
    from importlib import import_module

    from harness.knowledge.context_request import derive_binding

    module = import_module("harness.knowledge.query")
    conn, scope, _, bundle, aid = explanation_setup(graph)
    expected = binding_reads()
    derive_binding(conn, scope, bundle.payload["snapshot_id"], expected)
    passes, evidence_limits = [], []
    original_binding, original_citation = module.derive_binding, module._citation_current

    def binding(*args, **kwargs):
        reads = args[3]
        passes.append((reads["max_count"], reads["max_bytes"]))
        return original_binding(*args, **kwargs)

    def citation(*args, **kwargs):
        reads = kwargs["reads"]
        evidence_limits.append((reads["max_count"], reads["max_bytes"]))
        return original_citation(*args, **kwargs)

    monkeypatch.setattr(module, "derive_binding", binding)
    monkeypatch.setattr(module, "_citation_current", citation)
    result = module.explain(conn, scope, bundle.bundle_hash, aid, include_evidence=True)
    receipt = conn.execute(
        "SELECT * FROM knowledge_receipts WHERE receipt_id=?", (result["receipt_id"],)
    ).fetchone()
    assert passes == [(4, 4 * 1024 * 1024), (80, 64 * 1024 * 1024)]
    assert set(evidence_limits) == {(expected["count"] + 72, expected["bytes"] + 56 * 1024 * 1024)}
    sources = {c["locator"] for c in bundle.payload["citations"]}
    assert receipt["source_reads"] == 2 * expected["count"] + len(sources)
    assert json.loads(receipt["integrity_json"])["source_bytes"] == (
        2 * expected["bytes"] + sum(len((scope.repository / p).read_bytes()) for p in sources)
    )
    assert result["citations"] and all("text" in c for c in result["citations"])


def test_explain_restricted_context_never_reads_hidden_binding_bodies(graph, monkeypatch):
    from dataclasses import replace

    from harness.knowledge import adapters
    from harness.knowledge.query import explain, query

    conn, scope, _, _, entries = binding_setup(graph, designs=1)
    versions = frozenset(
        row[0]
        for row in conn.execute(
            "SELECT version_id FROM knowledge_source_versions v "
            "JOIN knowledge_sources s USING(source_id) WHERE s.locator='service.py'"
        )
    )
    restricted = replace(scope, permitted_source_versions=versions)
    monkeypatch.setattr(
        adapters, "binding_record", lambda *_a, **_k: pytest.fail("hidden body read")
    )
    bundle = query(conn, restricted, ContextRequest("service", stage="verification"))
    conn.execute("UPDATE design_entries SET response='Hidden change' WHERE id=?", (entries[0].id,))
    result = explain(
        conn, restricted, bundle.bundle_hash, bundle.payload["assertions"][0]["assertion_id"]
    )
    assert result["citations"] and {c["locator"] for c in result["citations"]} == {"service.py"}
    assert bundle.payload["context"]["binding"]["source_version_state"] == (
        "restricted-current-task-versions-unavailable"
    )


def test_historical_source_explanation_still_revalidates_current_task_context(graph):
    from dataclasses import replace

    from harness.knowledge.query import explain, query
    from harness.knowledge.refresh import refresh

    conn, scope, snapshot, _, entries = binding_setup(graph, designs=1)
    (scope.repository / "service.py").write_text("def changed():\n    return 2\n")
    current = refresh(conn, replace(scope, task_id=None)).snapshot_id
    assert current != snapshot
    bundle = query(
        conn, scope, ContextRequest("service", as_of_snapshot=snapshot, stage="recovery")
    )
    assert bundle.payload["snapshot_id"] == snapshot
    aid = bundle.payload["assertions"][0]["assertion_id"]
    assert explain(conn, scope, bundle.bundle_hash, aid)["citations"]
    before = conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0]
    conn.execute("UPDATE design_entries SET response='Changed' WHERE id=?", (entries[0].id,))
    with pytest.raises(PermissionError, match="stale.*context"):
        explain(conn, scope, bundle.bundle_hash, aid)
    assert conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0] == before


@pytest.mark.parametrize("corruption", ["identity", "body", "oversized-snapshot"])
def test_invalid_or_unavailable_explanation_binding_never_issues_receipt(graph, corruption):
    import json

    from harness.knowledge.models import canonical
    from harness.knowledge.query import explain

    conn, scope, _, bundle, aid = explanation_setup(graph)
    before = conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0]
    if corruption == "oversized-snapshot":
        conn.execute(
            "UPDATE knowledge_snapshots SET observation_json=? WHERE snapshot_id=?",
            (json.dumps({"padding": "x" * (4 * 1024 * 1024)}), bundle.payload["snapshot_id"]),
        )
    else:
        payload = json.loads(canonical(bundle.payload))
        if corruption == "identity":
            payload["context"]["binding_identity"] = "0" * 64
        else:
            payload["context"]["binding"]["task_id"] = "different-task"
        conn.execute(
            "UPDATE knowledge_bundles SET payload=? WHERE bundle_hash=?",
            (canonical(payload), bundle.bundle_hash),
        )
    with pytest.raises(PermissionError, match="stale.*context"):
        explain(conn, scope, bundle.bundle_hash, aid)
    assert conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0] == before


def test_explanation_final_binding_budget_exhaustion_refuses_without_receipt(graph, monkeypatch):
    from importlib import import_module

    module = import_module("harness.knowledge.query")
    conn, scope, _, bundle, aid = explanation_setup(graph)
    before = conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0]
    original = module.derive_binding
    calls = 0

    def exhausted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            args[3]["count"] = args[3]["max_count"]
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "derive_binding", exhausted)
    with pytest.raises(PermissionError, match="stale.*context"):
        module.explain(conn, scope, bundle.bundle_hash, aid)
    assert calls == 2
    assert conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0] == before


def test_legacy_explanation_does_not_derive_task_binding(graph, monkeypatch):
    from importlib import import_module

    module = import_module("harness.knowledge.query")
    conn, scope, _, _, _ = binding_setup(graph, designs=202)
    bundle = module.query(conn, scope, ContextRequest("service"))
    monkeypatch.setattr(module, "derive_binding", lambda *_a, **_k: pytest.fail("legacy binding"))
    assert "context" not in bundle.payload
    assert module.explain(
        conn, scope, bundle.bundle_hash, bundle.payload["assertions"][0]["assertion_id"]
    )["citations"]
