import json
import subprocess

import pytest

from harness.cli_knowledge import evidence_knowledge
from harness.controller import HarnessController
from harness.knowledge.models import ContextRequest, QueryLimits, canonical, digest
from harness.knowledge.query import explain, query
from harness.knowledge.refresh import refresh
from tests.test_knowledge_snapshots import graph as graph


def test_sourced_impact_paths_survive_reconnect_and_observe_staleness(graph):
    conn, scope = graph
    refresh(conn, scope)
    bundle = query(conn, scope, ContextRequest(entity_refs=("module:service",)))
    assert any(
        a["subject"] == "module:consumer" and a["predicate"] == "imports"
        for a in bundle.payload["assertions"]
    )
    assert bundle.payload["citations"]
    aid = bundle.payload["assertions"][0]["assertion_id"]
    assert explain(conn, scope, bundle.bundle_hash, aid)["citations"]
    (scope.repository / "consumer.py").write_text("import changed\n")
    updated = query(conn, scope, ContextRequest(entity_refs=("module:service",)))
    assert updated.payload["staleness"]
    assert not any(a["subject"] == "module:consumer" for a in updated.payload["assertions"])


def test_budget_includes_all_citations_paths_and_metadata(graph):
    conn, scope = graph
    refresh(conn, scope)
    bundle = query(conn, scope, ContextRequest("service", limits=QueryLimits(bytes=2048)))
    encoded = canonical(bundle.to_dict()).encode()
    assert len(encoded) <= 2048
    assert len(encoded) == bundle.payload["budget"]["serialized_bytes"]
    assert {a["assertion_id"] for a in bundle.payload["assertions"]} == {
        c["assertion_id"] for c in bundle.payload["citations"]
    }
    assert json.loads(encoded)["snapshot_id"]


def test_large_source_citations_bind_whole_content_and_return_bounded_evidence(graph):
    conn, scope = graph
    raw = json.dumps({"description": "large-contract", "payload": "x" * 519000}).encode()
    path = scope.repository / "large-contract.json"
    path.write_bytes(raw)
    subprocess.run(["git", "-C", str(scope.repository), "add", "."], check=True)
    assert refresh(conn, scope).status == "published"
    bundle = query(conn, scope, ContextRequest("large-contract"))
    assertion = next(
        a for a in bundle.payload["assertions"] if a["subject"] == "file:large-contract.json"
    )
    aid = assertion["assertion_id"]
    refs = explain(conn, scope, bundle.bundle_hash, aid)["citations"]
    assert refs[0]["content_sha256"] == digest(raw)
    evidence = evidence_knowledge(
        HarnessController(conn), scope.repository, bundle.bundle_hash, aid
    )
    assert len(evidence["citations"][0]["text"].encode()) <= 4096
    assert evidence["citations"][0]["truncated"]
    path.write_bytes(raw[:-3] + b"y" + raw[-2:])
    # The edit is beyond the cited prefix; whole-source integrity still invalidates it.
    assert refs[0]["end_byte"] < len(raw) - 3
    with pytest.raises(ValueError, match="stale"):
        explain(conn, scope, bundle.bundle_hash, aid)
    current = query(conn, scope, ContextRequest("large-contract"))
    assert all(a["assertion_id"] != aid for a in current.payload["assertions"])


@pytest.mark.parametrize("byte_limit", [4096, 32768])
def test_saturated_lexical_matches_leave_room_for_graph_only_consumer(graph, byte_limit):
    conn, scope = graph
    (scope.repository / "service.py").write_text(
        "\n".join(f"def needle_{index}():\n    return {index}\n" for index in range(60))
    )
    refresh(conn, scope)
    bundle = query(
        conn, scope, ContextRequest("needle", limits=QueryLimits(assertions=8, bytes=byte_limit))
    )
    consumer = next(
        (
            a
            for a in bundle.payload["assertions"]
            if a["subject"] == "module:consumer" and a["predicate"] == "imports"
        ),
        None,
    )
    assert consumer is not None
    assert any(
        p["edges"][0]["from"] == "module:service"
        and p["edges"][-1]["to"] == "module:consumer"
        and consumer["assertion_id"] in p["assertion_ids"]
        for p in bundle.payload["paths"]
    )


@pytest.mark.parametrize("byte_limit", [2048, 3072, 4096, 8192])
def test_graph_units_keep_two_hop_evidence_coherent_under_byte_pressure(graph, byte_limit):
    conn, scope = graph
    (scope.repository / "service.py").write_text("needle = 1\n")
    (scope.repository / "upper.py").write_text("import consumer\n")
    subprocess.run(["git", "-C", str(scope.repository), "add", "."], check=True)
    refresh(conn, scope)
    bundle = query(conn, scope, ContextRequest("needle", limits=QueryLimits(bytes=byte_limit)))
    payload = bundle.payload
    ids = {a["assertion_id"] for a in payload["assertions"]}
    assert ids == {c["assertion_id"] for c in payload["citations"]}
    assert len(canonical(bundle.to_dict()).encode()) <= byte_limit
    assert all(set(p["assertion_ids"]) <= ids for p in payload["paths"])
    upper = [a for a in payload["assertions"] if a["subject"] == "module:upper"]
    if upper:
        assert any(
            [e["from"] for e in p["edges"]] == ["module:service", "module:consumer"]
            and p["edges"][-1]["to"] == "module:upper"
            for p in payload["paths"]
        )
    else:
        assert payload["truncation"] and payload["exclusions"]["byte-budget"]
    if byte_limit == 8192:
        assert upper


def test_graph_paths_with_duplicates_and_cycles_are_stable_after_reconnect(graph, monkeypatch):
    from dataclasses import replace
    from importlib import import_module

    from harness.db import connect

    conn, scope = graph
    (scope.repository / "service.py").write_text("import upper\nneedle = 1\n")
    (scope.repository / "consumer.py").write_text("import service\nimport service\n")
    (scope.repository / "upper.py").write_text("import consumer\n")
    subprocess.run(["git", "-C", str(scope.repository), "add", "."], check=True)
    refresh(conn, scope)
    request = ContextRequest("needle")
    first = query(conn, scope, request)
    for path in first.payload["paths"]:
        assert len(path["edges"]) <= 2
        assert len(set(path["assertion_ids"])) == len(path["assertion_ids"])
        assert all(
            a["to"] == b["from"] for a, b in zip(path["edges"], path["edges"][1:], strict=False)
        )
    database = conn.execute("PRAGMA database_list").fetchone()[2]
    reopened = connect(database)
    try:
        assert query(reopened, scope, request).bundle_hash == first.bundle_hash
    finally:
        reopened.close()
    refresher = import_module("harness.knowledge.refresh")
    original = refresher.extract_source
    monkeypatch.setattr(
        refresher,
        "extract_source",
        lambda source: replace(
            original(source), assertions=tuple(reversed(original(source).assertions))
        ),
    )
    reordered = connect(scope.repository.parent / "reordered.db")
    try:
        refresh(reordered, scope)
        second = query(reordered, scope, request)
        assert {k: v for k, v in first.payload.items() if k != "snapshot_id"} == {
            k: v for k, v in second.payload.items() if k != "snapshot_id"
        }
    finally:
        reordered.close()


def test_task_private_neighbors_do_not_change_public_selection_counts_or_order(graph):
    from dataclasses import replace

    from harness.knowledge.models import RefreshRequest

    conn, scope = graph
    (scope.repository / "service.py").write_text("needle = 1\n")
    refresh(conn, scope)
    request = ContextRequest("needle", limits=QueryLimits(assertions=4))
    before = query(conn, scope, request).payload
    private = scope.repository / "private_neighbor.py"
    private.write_text("import service\n")
    refresh(
        conn, replace(scope, task_id="t-private"), RefreshRequest(included_paths=(private.name,))
    )
    after = query(conn, scope, request).payload
    assert {k: v for k, v in before.items() if k != "snapshot_id"} == {
        k: v for k, v in after.items() if k != "snapshot_id"
    }
    owner = query(conn, replace(scope, task_id="t-private"), request)
    assert any(a["subject"] == "module:private_neighbor" for a in owner.payload["assertions"])


@pytest.mark.parametrize("denial", ["stale", "revoked", "purged"])
def test_graph_only_neighbor_denials_survive_priority_packing(graph, denial):
    conn, scope = graph
    (scope.repository / "service.py").write_text("needle = 1\n")
    refresh(conn, scope)
    request = ContextRequest("needle")
    bundle = query(conn, scope, request)
    aid = next(
        a["assertion_id"]
        for a in bundle.payload["assertions"]
        if a["subject"] == "module:consumer" and a["predicate"] == "imports"
    )
    if denial == "stale":
        (scope.repository / "consumer.py").write_text("import changed\n")
    elif denial == "purged":
        conn.execute("INSERT INTO knowledge_assertion_state VALUES (?,'purged',NULL)", (aid,))
    else:
        version = next(
            c["version_id"] for c in bundle.payload["citations"] if c["assertion_id"] == aid
        )
        conn.execute(
            "INSERT INTO knowledge_grants VALUES ('g','source',?,'hash','approval','now')",
            (scope.project_id,),
        )
        conn.execute("INSERT INTO knowledge_import_sources VALUES (?,'g','lesson',1)", (version,))
    current = query(conn, scope, request)
    assert aid not in {a["assertion_id"] for a in current.payload["assertions"]}
    assert all(aid not in p["assertion_ids"] for p in current.payload["paths"])
    with pytest.raises((PermissionError, ValueError)):
        explain(conn, scope, bundle.bundle_hash, aid)


def test_stable_decision_reference_recalls_actual_version_content_and_rationale(graph):
    from harness.decisions import render_record

    conn, scope = graph
    (scope.repository / "DEC-0100.md").write_text(
        render_record(
            "DEC-0100",
            "Storage choice",
            1,
            "now",
            "Local persistence",
            None,
            "Use SQLite",
            "Keep atomic local writes",
            "none",
        )
    )
    subprocess.run(["git", "-C", str(scope.repository), "add", "."], check=True)
    refresh(conn, scope)
    bundle = query(conn, scope, ContextRequest(entity_refs=("decision:DEC-0100",)))
    facts = {
        a["predicate"]: a["value"]
        for a in bundle.payload["assertions"]
        if a["subject"] == "decision:DEC-0100:v1"
    }
    assert (
        facts.items()
        >= {
            "title": "Storage choice",
            "scope": "Local persistence",
            "status": "approved",
            "decision": "Use SQLite",
            "rationale": "Keep atomic local writes",
        }.items()
    )
    assert bundle.payload["citations"]
    bounded = query(
        conn,
        scope,
        ContextRequest(entity_refs=("decision:DEC-0100",), limits=QueryLimits(examined_edges=2)),
    )
    assert bounded.payload["budget"]["examined_edges"] <= 2
    assert bounded.payload["truncation"] and bounded.payload["exclusions"]["edge-budget"]
    assert all(
        edge["from"] is not None and edge["to"] is not None
        for path in bounded.payload["paths"]
        for edge in path["edges"]
    )


def test_task_reference_recalls_linked_design_text_status_and_rationale(graph):
    from dataclasses import replace

    from harness.design_models import DesignSection, EntryStatus
    from harness.knowledge.integration import refresh_with_records

    conn, scope = graph
    controller = HarnessController(conn)
    task = controller.create_task("Recall linked design")
    workspace = controller.workspace(task.id)
    entry = workspace.propose_design(
        DesignSection.COMPONENTS, "Use the shared projection", "Keep one source of truth"
    )
    workspace.resolve_design(entry.id, EntryStatus.ACCEPTED)
    scope = replace(scope, task_id=task.id)
    refresh_with_records(controller, scope)
    bundle = query(conn, scope, ContextRequest(entity_refs=(f"task:{task.id}",)))
    facts = {
        a["predicate"]: a["value"]
        for a in bundle.payload["assertions"]
        if a["subject"] == f"design:{task.id}:{entry.ref}"
    }
    assert (
        facts.items()
        >= {
            "text": "Use the shared projection",
            "binding": "fixed",
            "status": "accepted",
            "rationale": "Keep one source of truth",
        }.items()
    )
    assert bundle.payload["citations"]

    public = query(
        conn, replace(scope, task_id=None), ContextRequest(entity_refs=(f"task:{task.id}",))
    )
    assert public.payload["assertions"] == []
    assert public.payload["citations"] == []
    assert public.payload["budget"]["examined_edges"] == 0


@pytest.mark.parametrize("byte_limit", [8192, 32768])
def test_parented_direct_matches_interleave_with_saturated_reverse_impact(graph, byte_limit):
    conn, scope = graph
    (scope.repository / "service.py").write_text(
        "".join(f"def rankedneedle_{index}():\n    return {index}\n" for index in range(4))
    )
    for index in range(230):
        (scope.repository / f"dependent_{index:03d}.py").write_text("import service\n")
    subprocess.run(["git", "-C", str(scope.repository), "add", "."], check=True)
    refresh(conn, scope)
    parent = conn.execute(
        "SELECT a.assertion_id FROM knowledge_assertions a JOIN knowledge_evidence e "
        "USING(assertion_id) JOIN knowledge_source_versions v USING(version_id) "
        "JOIN knowledge_sources s USING(source_id) WHERE s.locator='service.py' "
        "AND a.predicate='described-by'"
    ).fetchone()[0]
    direct = {
        r[0]
        for r in conn.execute(
            "SELECT assertion_id FROM knowledge_assertions WHERE predicate='defines' "
            "AND text LIKE '%rankedneedle_%'"
        )
    }
    conn.executemany(
        "INSERT INTO knowledge_derivations VALUES (?,?)", [(aid, parent) for aid in direct]
    )
    bundle = query(
        conn,
        scope,
        ContextRequest("rankedneedle", limits=QueryLimits(assertions=8, bytes=byte_limit)),
    )
    ids = {a["assertion_id"] for a in bundle.payload["assertions"]}
    assert len(ids & direct) >= 2
    assert parent in ids
    assert any(
        a["predicate"] == "imports" and a["subject"].startswith("module:dependent_")
        for a in bundle.payload["assertions"]
    )
    assert bundle.payload["budget"]["examined_edges"] == 200
    assert ids == {c["assertion_id"] for c in bundle.payload["citations"]}
    assert all(set(p["assertion_ids"]) <= ids for p in bundle.payload["paths"])


def test_explanation_returns_actual_shared_transitive_derivation_and_bundle_evidence(graph):
    from tests.test_knowledge_budgets import derived_graph

    conn, scope, ids, links = derived_graph(graph)
    extra = (ids["closure_leaf"], ids["closure_peer"])
    conn.execute("INSERT INTO knowledge_derivations VALUES (?,?)", extra)
    bundle = query(conn, scope, ContextRequest("closure_leaf closure_peer"))
    rows = {a["assertion_id"]: a for a in bundle.payload["assertions"]}
    for child, parent in [*links, extra]:
        assert parent in rows[child]["derivation_parents"]
    result = explain(conn, scope, bundle.bundle_hash, ids["closure_leaf"])
    derivation = result["derivation"]
    assert derivation["status"] == "complete"
    assert set(derivation["direct_parent_ids"]) == {ids["closure_middle"], ids["closure_peer"]}
    assert {
        (e["child_assertion_id"], e["parent_assertion_id"]) for e in derivation["edges"]
    } == set([*links, extra])
    parents = {ids["closure_middle"], ids["closure_peer"], ids["closure_root"]}
    assert {a["assertion_id"] for a in derivation["assertions"]} == parents
    assert {c["assertion_id"] for c in derivation["citations"]} == parents
    assert len(canonical(result).encode()) <= bundle.payload["budget"]["max_bytes"]
    assert result["receipt_id"] != bundle.receipt_id
    assert "receipt_id" not in bundle.payload


def test_receipt_failure_rolls_back_bundle_and_receipt_without_success_reference(
    graph, monkeypatch
):
    from importlib import import_module

    conn, scope = graph
    refresh(conn, scope)
    module = import_module("harness.knowledge.query")
    original = module.record_receipt

    def failed(*args):
        original(*args)
        raise ValueError("injected persistence failure")

    monkeypatch.setattr(module, "record_receipt", failed)
    with pytest.raises(ValueError, match="injected persistence failure"):
        query(conn, scope, ContextRequest("service"))
    assert conn.execute("SELECT count(*) FROM knowledge_receipts").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM knowledge_bundles").fetchone()[0] == 0


def test_dense_derivation_explanation_refuses_optional_detail_within_32k(graph):
    conn, scope = graph
    (scope.repository / "service.py").write_text(
        "".join(f"def chain_needle_{i}():\n    return {i}\n" for i in range(16))
    )
    refresh(conn, scope)
    ids = {
        r["name"]: r["assertion_id"]
        for r in conn.execute(
            "SELECT a.assertion_id,o.scoped_key AS name FROM knowledge_assertions a "
            "JOIN knowledge_entities o ON o.entity_id=a.object_id WHERE a.predicate='defines'"
        )
    }
    ordered = [ids[f"symbol:service:chain_needle_{i}"] for i in range(16)]
    conn.executemany(
        "INSERT INTO knowledge_derivations VALUES (?,?)",
        [(child, parent) for index, child in enumerate(ordered) for parent in ordered[:index]],
    )
    bundle = query(
        conn,
        scope,
        ContextRequest(entity_refs=("symbol:service:chain_needle_15",), limits=QueryLimits(hops=0)),
    )
    assert set(ordered) <= {a["assertion_id"] for a in bundle.payload["assertions"]}
    assert bundle.payload["budget"]["examined_edges"] == 120
    result = explain(conn, scope, bundle.bundle_hash, ordered[-1])
    assert result["derivation"]["status"] == "unavailable-budget"
    assert not result["derivation"]["edges"]
    assert not result["derivation"]["assertions"]
    assert len(canonical(result).encode()) <= 32768
    assert len(canonical(bundle.to_dict()).encode()) <= 32768


def test_authored_prose_never_creates_derivation_edges(graph):
    conn, scope = graph
    (scope.repository / "service.py").write_text(
        "# parents: " + "a" * 64 + '\nneedle = "derived from source.py:1"\n'
    )
    refresh(conn, scope)
    bundle = query(conn, scope, ContextRequest("needle"))
    assert bundle.payload["assertions"]
    for assertion in bundle.payload["assertions"]:
        assert assertion["derivation_parents"] == []
        result = explain(conn, scope, bundle.bundle_hash, assertion["assertion_id"])
        assert result["derivation"]["status"] == "complete"
        assert result["derivation"]["edges"] == []
