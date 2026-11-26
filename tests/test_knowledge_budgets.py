"""Budget, integrity and partition non-interference regressions."""

import json
from dataclasses import replace

import pytest

from harness.knowledge.models import ContextRequest, QueryLimits, canonical
from harness.knowledge.query import explain, query
from harness.knowledge.refresh import refresh
from tests.test_knowledge_snapshots import graph as graph


def test_legacy_snapshot_without_fts_has_bounded_compatible_retrieval(graph):
    conn, scope = graph
    snapshot = refresh(conn, scope).snapshot_id
    conn.execute("DELETE FROM knowledge_assertion_fts")
    bundle = query(conn, scope, ContextRequest("service", as_of_snapshot=snapshot))
    assert any(a["subject"] == "module:consumer" for a in bundle.payload["assertions"])
    assert conn.execute("SELECT count(*) FROM knowledge_assertion_fts").fetchone()[0] == 0


def test_edge_budget_and_typed_reverse_paths(graph):
    conn, scope = graph
    refresh(conn, scope)
    bundle = query(
        conn,
        scope,
        ContextRequest(entity_refs=("module:service",), limits=QueryLimits(examined_edges=1)),
    )
    assert bundle.payload["budget"]["examined_edges"] <= 1
    assert any(
        edge["predicate"] == "imports" and edge["direction"] == "reverse"
        for path in bundle.payload["paths"]
        for edge in path["edges"]
    )
    assert bundle.payload["entity_refs"] == ["module:service"]
    assert all(len(p["edges"]) <= 2 for p in bundle.payload["paths"])


def test_receipts_have_counts_and_hashes_without_request_or_source_text(graph):
    conn, scope = graph
    refresh(conn, scope)
    bundle = query(conn, scope, ContextRequest("private-search-string service"))
    explain(conn, scope, bundle.bundle_hash, bundle.payload["assertions"][0]["assertion_id"])
    rows = conn.execute("SELECT * FROM knowledge_receipts").fetchall()
    assert len(rows) == 2 and rows[0]["source_reads"] > 0
    assert rows[0]["policy_digest"] == bundle.payload["policy_digest"]
    assert "private-search-string" not in json.dumps([dict(r) for r in rows])
    assert "import service" not in json.dumps([dict(r) for r in rows])


def test_revoked_and_purged_sources_are_denied_on_query_and_explain(graph):
    conn, scope = graph
    snapshot = refresh(conn, scope).snapshot_id
    bundle = query(conn, scope, ContextRequest("consumer"))
    aid = bundle.payload["assertions"][0]["assertion_id"]
    version = next(c["version_id"] for c in bundle.payload["citations"] if c["assertion_id"] == aid)
    conn.execute(
        "INSERT INTO knowledge_grants VALUES ('g','source',?,'hash','approval',NULL)",
        (scope.project_id,),
    )
    conn.execute("INSERT INTO knowledge_import_sources VALUES (?,'g','lesson',1)", (version,))
    conn.execute("UPDATE knowledge_grants SET revoked_at='now'")
    with pytest.raises(PermissionError):
        explain(conn, scope, bundle.bundle_hash, aid)
    current = query(conn, scope, ContextRequest("consumer", as_of_snapshot=snapshot))
    assert aid not in {a["assertion_id"] for a in current.payload["assertions"]}
    conn.execute("DELETE FROM knowledge_import_sources")
    conn.execute("INSERT INTO knowledge_assertion_state VALUES (?,'purged',NULL)", (aid,))
    with pytest.raises(PermissionError):
        explain(conn, scope, bundle.bundle_hash, aid)


def test_cycle_and_forbidden_derivation_never_enter_candidates(graph):
    conn, scope = graph
    refresh(conn, scope)
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT assertion_id FROM knowledge_assertions ORDER BY assertion_id LIMIT 2"
        )
    ]
    conn.executemany(
        "INSERT INTO knowledge_derivations VALUES (?,?)", [(ids[0], ids[1]), (ids[1], ids[0])]
    )
    bundle = query(conn, scope, ContextRequest("service consumer"))
    assert not set(ids) & {a["assertion_id"] for a in bundle.payload["assertions"]}
    allowed = frozenset(
        r[0]
        for r in conn.execute(
            "SELECT v.version_id FROM knowledge_source_versions v JOIN knowledge_sources s "
            "USING(source_id) WHERE s.locator='service.py'"
        )
    )
    reviewer = replace(
        scope, purpose="fresh-review", packet_hash="packet", permitted_source_versions=allowed
    )
    restricted = query(conn, reviewer, ContextRequest("consumer"))
    assert restricted.payload["assertions"] == []
    assert restricted.payload["budget"]["examined_edges"] == 0
    assert restricted.payload["coverage"] == [
        {"status": "packet-local", "whole_repository_coverage": "not-assessed"}
    ]


def test_stale_observations_survive_small_bundle_budget(graph):
    conn, scope = graph
    refresh(conn, scope)
    (scope.repository / "consumer.py").write_text("changed source")
    bundle = query(conn, scope, ContextRequest("consumer service", limits=QueryLimits(bytes=2048)))
    assert bundle.payload["staleness"]
    assert len(canonical(bundle.to_dict()).encode()) <= 2048
    assert len(canonical(bundle.to_dict()).encode()) == bundle.payload["budget"]["serialized_bytes"]


def test_hidden_sources_and_aliases_do_not_change_restricted_bundle(graph):
    from harness.knowledge.identity import record_alias

    conn, scope = graph
    first = refresh(conn, scope)
    allowed = frozenset(
        r[0]
        for r in conn.execute(
            "SELECT m.version_id FROM knowledge_snapshot_sources m JOIN knowledge_sources "
            "s USING(source_id) "
            "WHERE m.snapshot_id=? AND s.locator='service.py'",
            (first.snapshot_id,),
        )
    )
    reviewer = replace(
        scope, purpose="fresh-review", packet_hash="packet", permitted_source_versions=allowed
    )
    before = query(
        conn, reviewer, ContextRequest("canary service", entity_refs=("hidden-alias",))
    ).payload
    hidden = conn.execute(
        "SELECT a.assertion_id,a.subject_id FROM knowledge_assertions a JOIN "
        "knowledge_evidence e USING(assertion_id) JOIN knowledge_source_versions v "
        "USING(version_id) JOIN knowledge_sources s USING(source_id) WHERE "
        "s.locator='consumer.py' LIMIT 1"
    ).fetchone()
    record_alias(
        conn, scope.project_id, "hidden-alias", hidden["subject_id"], hidden["assertion_id"]
    )
    (scope.repository / "consumer.py").write_text("canary = 'hidden knowledge'\n")
    refresh(conn, scope)
    after = query(
        conn, reviewer, ContextRequest("canary service", entity_refs=("hidden-alias",))
    ).payload
    assert {k: v for k, v in before.items() if k not in {"snapshot_id", "policy_digest"}} == {
        k: v for k, v in after.items() if k not in {"snapshot_id", "policy_digest"}
    }


def test_current_decision_version_conflict_survives_budget(graph):
    import subprocess

    from harness.decisions import render_record

    conn, scope = graph
    for version in (1, 2):
        name = f"DEC-0100-version{version}.md"
        (scope.repository / name).write_text(
            render_record(
                "DEC-0100", "Decision", version, "now", "service", None, "Local", "cost", "none"
            )
        )
    subprocess.run(["git", "-C", str(scope.repository), "add", "."], check=True)
    refresh(conn, scope)
    bundle = query(
        conn,
        scope,
        ContextRequest(entity_refs=("decision:DEC-0100",), limits=QueryLimits(bytes=2048)),
    )
    encoded = canonical(bundle.to_dict()).encode()
    assert len(encoded) <= 2048
    assert "conflicting-current-decision-versions" in json.dumps(bundle.payload["conflicts"])


def test_head_ancestry_reports_staleness_without_timestamp_guessing(graph):
    import subprocess

    conn, scope = graph
    command = [
        "git",
        "-C",
        str(scope.repository),
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
    ]
    subprocess.run([*command, "commit", "-qm", "first"], check=True)
    refresh(conn, scope)
    subprocess.run([*command, "commit", "--allow-empty", "-qm", "later"], check=True)
    bundle = query(conn, scope, ContextRequest("service"))
    assert {"status": "snapshot-behind-head"} in bundle.payload["staleness"]
    assert bundle.payload["assertions"]


def test_typed_paths_are_contiguous_when_expansion_branches_from_a_seed(graph):
    from collections import Counter

    from harness.knowledge.query import _ROW, _expand, _partition

    conn, scope = graph
    snapshot = refresh(conn, scope).snapshot_id
    _partition(conn, scope, snapshot)
    seed = dict(conn.execute(_ROW + "WHERE a.predicate='imports' LIMIT 1").fetchone())
    _, paths, examined = _expand(
        conn,
        {seed["assertion_id"]: seed},
        [seed["assertion_id"]],
        {"module:service"},
        QueryLimits(),
        Counter(),
    )
    assert examined <= 200
    for path in paths.values():
        assert all(
            a["to"] == b["from"] for a, b in zip(path["edges"], path["edges"][1:], strict=False)
        )


def test_broken_evidence_trust_state_is_visible(graph):
    conn, scope = graph
    refresh(conn, scope)
    conn.execute(
        "UPDATE knowledge_assertions SET verification='broken-evidence' WHERE predicate='imports'"
    )
    bundle = query(conn, scope, ContextRequest("service"))
    assert "broken-evidence" in json.dumps(bundle.payload["conflicts"])


def test_preexisting_schema_one_bundle_paths_still_explain(graph):
    from harness.db import utcnow
    from harness.knowledge.models import digest

    conn, scope = graph
    refresh(conn, scope)
    bundle = query(conn, scope, ContextRequest("service"))
    payload = dict(bundle.payload)
    aid = payload["assertions"][0]["assertion_id"]
    payload.update(schema_version=1, paths=[[aid]])
    payload.pop("as_of_snapshot")
    payload.pop("entity_refs")
    encoded = canonical(payload)
    legacy_hash = digest(encoded.encode())
    conn.execute(
        "INSERT INTO knowledge_bundles VALUES (?,?,?,?,?,?,?,NULL)",
        (
            legacy_hash,
            scope.project_id,
            payload["snapshot_id"],
            payload["policy_digest"],
            scope.purpose,
            encoded,
            utcnow(),
        ),
    )
    assert explain(conn, scope, legacy_hash, aid)["paths"] == [[aid]]


@pytest.mark.parametrize("limit", [1024, 2048, 32768])
def test_metadata_compaction_preserves_order_parents_and_integrity_counts(graph, limit):
    from collections import Counter
    from copy import deepcopy

    from harness.knowledge.query import _fit

    conn, scope = graph
    refresh(conn, scope)
    parent = conn.execute(
        "SELECT assertion_id FROM knowledge_assertions WHERE predicate='described-by' "
        "ORDER BY assertion_id LIMIT 1"
    ).fetchone()[0]
    children = [
        r[0]
        for r in conn.execute(
            "SELECT assertion_id FROM knowledge_assertions "
            "WHERE predicate IN ('defines','imports') ORDER BY assertion_id"
        )
    ]
    conn.executemany(
        "INSERT INTO knowledge_derivations VALUES (?,?)", [(child, parent) for child in children]
    )
    payload = deepcopy(query(conn, scope, ContextRequest("consumer service")).payload)
    original_ids = [a["assertion_id"] for a in payload["assertions"]]
    assert set(children) <= set(original_ids)
    original_counts = {}
    for field in ("coverage", "staleness", "conflicts", "ambiguities"):
        payload[field] = [
            {"status": f"{field}-{index % 2}", "detail": "diagnostic detail " * 40}
            for index in range(60)
        ]
        original_counts[field] = Counter(item["status"] for item in payload[field])
    exclusions = Counter()
    try:
        _fit(payload, limit, exclusions)
    except ValueError as error:
        assert str(error) == "request metadata exceeds bundle budget"
        assert not payload["assertions"] and limit == 1024
        return
    ids = [a["assertion_id"] for a in payload["assertions"]]
    assert ids == original_ids[: len(ids)]
    if limit == 32768:
        assert ids == original_ids and "byte-budget" not in exclusions
    if set(children) & set(ids):
        assert parent in ids
    assert {c["assertion_id"] for c in payload["citations"]} == set(ids)
    assert all(set(path["assertion_ids"]) <= set(ids) for path in payload["paths"])
    for field, counts in original_counts.items():
        observations = payload[field]
        if len(observations) == 1 and observations[0].get("summarized"):
            assert observations[0]["counts"] == counts
        else:
            assert Counter(item["status"] for item in observations) == counts
    encoded = canonical({"bundle_hash": "0" * 64, "receipt_id": "0" * 32, **payload}).encode()
    assert len(encoded) == payload["budget"]["serialized_bytes"] <= limit
    assert payload["truncation"] and exclusions["metadata-detail-budget"]
    counts_before = deepcopy({field: payload[field] for field in original_counts})
    _fit(payload, limit, exclusions)
    assert counts_before == {field: payload[field] for field in original_counts}


def test_fitting_detail_is_unchanged_and_nonshrinking_summary_is_not_applied(graph):
    from collections import Counter
    from copy import deepcopy

    from harness.knowledge.query import _fit

    conn, scope = graph
    refresh(conn, scope)
    payload = deepcopy(query(conn, scope, ContextRequest("service")).payload)
    payload["coverage"] = [{"status": "x"}]
    _fit(payload, 32768, Counter())
    original = deepcopy(payload)
    _fit(payload, payload["budget"]["serialized_bytes"], Counter())
    assert payload == original
    payload.update(assertions=[], citations=[], paths=[], query="mandatory query " * 300)
    exclusions = Counter()
    with pytest.raises(ValueError, match="request metadata exceeds bundle budget"):
        _fit(payload, 1024, exclusions)
    assert payload["coverage"] == [{"status": "x"}]
    assert "metadata-detail-budget" not in exclusions


def test_incremental_packing_merges_new_integrity_observations_without_losing_counts(graph):
    from collections import Counter
    from copy import deepcopy

    from harness.knowledge.query import _fit

    conn, scope = graph
    refresh(conn, scope)
    payload = deepcopy(query(conn, scope, ContextRequest("service")).payload)
    payload["staleness"] = [
        {
            "status": "integrity-summary",
            "summarized": True,
            "counts": {"withheld-stale-evidence": 5},
        },
        *[{"status": "withheld-stale-evidence", "detail": "x" * 200} for _ in range(30)],
    ]
    _fit(payload, 2048, Counter())
    assert payload["staleness"] == [
        {
            "status": "integrity-summary",
            "summarized": True,
            "counts": {"withheld-stale-evidence": 35},
        }
    ]
    assert (
        len(canonical({"bundle_hash": "0" * 64, "receipt_id": "0" * 32, **payload}).encode())
        <= 2048
    )


def derived_graph(graph):
    conn, scope = graph
    (scope.repository / "service.py").write_text(
        "".join(
            f"def closure_{name}():\n    return 1\n" for name in ("root", "middle", "leaf", "peer")
        )
    )
    refresh(conn, scope)
    ids = {
        row["object"].rsplit(":", 1)[1]: row["assertion_id"]
        for row in conn.execute(
            "SELECT a.assertion_id,o.scoped_key AS object FROM knowledge_assertions a "
            "JOIN knowledge_entities o ON o.entity_id=a.object_id "
            "WHERE a.predicate='defines' AND o.scoped_key LIKE 'symbol:service:closure_%'"
        )
    }
    links = [
        (ids["closure_leaf"], ids["closure_middle"]),
        (ids["closure_peer"], ids["closure_middle"]),
        (ids["closure_middle"], ids["closure_root"]),
    ]
    conn.executemany("INSERT INTO knowledge_derivations VALUES (?,?)", links)
    return conn, scope, ids, links


@pytest.mark.parametrize("edges", [0, 1, 2, 3])
def test_shared_transitive_closure_charges_each_parent_edge_once(graph, edges):
    conn, scope, ids, links = derived_graph(graph)
    bundle = query(
        conn,
        scope,
        ContextRequest(
            "closure_leaf closure_peer", limits=QueryLimits(hops=0, examined_edges=edges)
        ),
    )
    returned = {a["assertion_id"] for a in bundle.payload["assertions"]}
    assert len(bundle.payload["assertions"]) == len(returned)
    for child, parent in links:
        assert child not in returned or parent in returned
    assert bundle.payload["budget"]["examined_edges"] == edges
    children = {ids["closure_leaf"], ids["closure_peer"]}
    if edges == 3:
        assert set(ids.values()) <= returned
    else:
        assert not children <= returned
        assert bundle.payload["exclusions"]["parent-budget"]
    assert returned == {c["assertion_id"] for c in bundle.payload["citations"]}


@pytest.mark.parametrize("assertions", [2, 3, 8])
@pytest.mark.parametrize("byte_limit", [2048, 4096, 8192])
def test_derived_units_rollback_without_orphans_under_packing_limits(graph, assertions, byte_limit):
    conn, scope, ids, links = derived_graph(graph)
    bundle = query(
        conn,
        scope,
        ContextRequest(
            "closure_leaf closure_peer",
            limits=QueryLimits(assertions=assertions, bytes=byte_limit, hops=0),
        ),
    )
    returned = {a["assertion_id"] for a in bundle.payload["assertions"]}
    for child, parent in links:
        assert child not in returned or parent in returned
    assert returned == {c["assertion_id"] for c in bundle.payload["citations"]}
    assert len(returned) <= assertions
    assert len(canonical(bundle.to_dict()).encode()) <= byte_limit
    assert all(set(p["assertion_ids"]) <= returned for p in bundle.payload["paths"])
    if assertions == 2:
        assert not {ids["closure_leaf"], ids["closure_peer"]} & returned
    if assertions == 8 and byte_limit == 8192:
        assert set(ids.values()) <= returned


@pytest.mark.parametrize("denial", ["stale", "revoked", "purged", "cycle", "private"])
def test_required_parent_denial_withholds_derived_descendants(graph, denial):
    conn, scope, ids, _ = derived_graph(graph)
    request = ContextRequest("closure_leaf closure_peer", limits=QueryLimits(hops=0))
    before = query(conn, scope, request)
    assert set(ids.values()) <= {a["assertion_id"] for a in before.payload["assertions"]}
    parent = ids["closure_root"]
    version = next(
        c["version_id"] for c in before.payload["citations"] if c["assertion_id"] == parent
    )
    if denial == "stale":
        (scope.repository / "service.py").write_text("changed source\n")
    elif denial == "purged":
        conn.execute("INSERT INTO knowledge_assertion_state VALUES (?,'purged',NULL)", (parent,))
    elif denial == "cycle":
        conn.execute(
            "INSERT INTO knowledge_derivations VALUES (?,?)", (parent, ids["closure_leaf"])
        )
    elif denial == "private":
        conn.execute(
            "UPDATE knowledge_source_versions SET observation_json="
            "json_set(observation_json,'$.visibility','task:other') WHERE version_id=?",
            (version,),
        )
    else:
        conn.execute(
            "INSERT INTO knowledge_grants VALUES ('g','source',?,'hash','approval','now')",
            (scope.project_id,),
        )
        conn.execute("INSERT INTO knowledge_import_sources VALUES (?,'g','lesson',1)", (version,))
    after = query(conn, scope, request)
    assert not set(ids.values()) & {a["assertion_id"] for a in after.payload["assertions"]}
    with pytest.raises((ValueError, PermissionError)):
        explain(conn, scope, before.bundle_hash, ids["closure_leaf"])


def test_failed_units_keep_read_work_and_distinguish_budget_from_staleness(graph):
    import subprocess

    conn, scope = graph
    for index in range(22):
        (scope.repository / f"parent_{index}.md").write_text(f"Supporting origin {index}.")
        (scope.repository / f"child_{index}.md").write_text(f"readcapneedle observation {index}.")
    subprocess.run(["git", "-C", str(scope.repository), "add", "."], check=True)
    refresh(conn, scope)
    ids = {
        r["locator"]: r["assertion_id"]
        for r in conn.execute(
            "SELECT s.locator,a.assertion_id FROM knowledge_assertions a JOIN knowledge_evidence e "
            "USING(assertion_id) JOIN knowledge_source_versions v USING(version_id) "
            "JOIN knowledge_sources s USING(source_id) WHERE a.predicate='described-by'"
        )
    }
    conn.executemany(
        "INSERT INTO knowledge_derivations VALUES (?,?)",
        [(ids[f"child_{index}.md"], ids[f"parent_{index}.md"]) for index in range(22)],
    )
    for index in range(22):
        (scope.repository / f"child_{index}.md").write_text("changed source")
    bundle = query(conn, scope, ContextRequest("readcapneedle", limits=QueryLimits(hops=0)))
    assert not bundle.payload["assertions"]
    assert bundle.payload["budget"]["examined_edges"] == 22
    statuses = {o["status"] for o in bundle.payload["staleness"]}
    assert statuses == {"withheld-stale-evidence", "withheld-source-read-budget"}
    assert bundle.payload["exclusions"]["source-read-budget"]
    receipt = conn.execute(
        "SELECT source_reads FROM knowledge_receipts WHERE bundle_hash=?", (bundle.bundle_hash,)
    ).fetchone()
    assert receipt[0] == 40


def test_deep_failed_seed_does_not_poison_shorter_parent_closure(graph):
    conn, scope = graph
    (scope.repository / "service.py").write_text(
        'def ranked_A():\n    "priorityword priorityextra"\n    return 1\n'
        "def supporting_B():\n    return 2\n"
        "def later_C():\n    return 3\n"
        "def origin_D():\n    return 4\n"
    )
    refresh(conn, scope)
    ids = {
        r["name"]: r["assertion_id"]
        for r in conn.execute(
            "SELECT a.assertion_id,o.scoped_key AS name FROM knowledge_assertions a "
            "JOIN knowledge_entities o ON o.entity_id=a.object_id WHERE a.predicate='defines'"
        )
    }
    a, b, c, d = [
        ids[f"symbol:service:{name}"]
        for name in ("ranked_A", "supporting_B", "later_C", "origin_D")
    ]
    conn.executemany("INSERT INTO knowledge_derivations VALUES (?,?)", [(a, b), (b, c), (c, d)])
    bundle = query(
        conn,
        scope,
        ContextRequest(
            "priorityword priorityextra",
            entity_refs=("symbol:service:ranked_A", "symbol:service:later_C"),
            limits=QueryLimits(assertions=3, hops=0),
        ),
    )
    returned = {item["assertion_id"] for item in bundle.payload["assertions"]}
    assert {c, d} <= returned
    assert a not in returned
    assert len(returned) <= 3
    assert returned == {ref["assertion_id"] for ref in bundle.payload["citations"]}


def test_secondary_estimate_counts_complete_serialized_bundle(graph):
    conn, scope = graph
    refresh(conn, scope)
    bundle = query(
        conn, scope, ContextRequest("service", stage="verification", estimated_tokens=4096)
    )
    size = len(canonical(bundle.to_dict()).encode())
    assert size <= 10922
    assert bundle.payload["budget"]["estimated_tokens"] == (3 * size + 7) // 8
    assert bundle.payload["budget"]["estimated_tokens"] <= 4096
    assert bundle.payload["budget"]["effective_max_bytes"] == 10922
    assert "not an upper bound" in bundle.payload["budget"]["token_estimator_limits"]
    assert bundle.payload["budget"]["serialized_bytes"] == size
    assert "not-tokenizer-exact" in bundle.payload["budget"]["token_estimator"]
    assert {a["assertion_id"] for a in bundle.payload["assertions"]} == {
        c["assertion_id"] for c in bundle.payload["citations"]
    }


def test_binding_passes_share_receipt_accounting_under_citation_pressure(graph):
    import subprocess

    from tests.test_knowledge_context_request import binding_setup

    conn, scope, _, _, _ = binding_setup(graph, designs=3)
    for index in range(45):
        (scope.repository / f"candidate_{index}.py").write_text(f"needle = {index}\n")
    subprocess.run(["git", "-C", str(scope.repository), "add", "."], check=True)
    # Index files without triggering structured task ingestion; bind the actual task only at query.
    refresh(conn, replace(scope, task_id=None))
    bundle = query(conn, scope, ContextRequest("needle", stage="implementation"))
    receipt = conn.execute(
        "SELECT * FROM knowledge_receipts WHERE receipt_id=?", (bundle.receipt_id,)
    ).fetchone()
    sources = {c["version_id"] for c in bundle.payload["citations"]}
    details = json.loads(receipt["integrity_json"])
    assert bundle.payload["exclusions"]["source-read-budget"]
    # Both snapshot and two chosen-design projections are freshly charged in each pass.
    assert receipt["source_reads"] >= 6 + 2 * len(sources)
    assert receipt["source_reads"] <= 80
    assert 0 < details["source_bytes"] <= 64 * 1024 * 1024
    assert bundle.payload["context"]["binding"]["source_versions"]["design"]["overflow"]
    assert {a["assertion_id"] for a in bundle.payload["assertions"]} == {
        c["assertion_id"] for c in bundle.payload["citations"]
    }
