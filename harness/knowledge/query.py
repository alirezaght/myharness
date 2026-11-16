"""Partition-first lexical/graph retrieval with complete citations and serialized budgets."""

import json
import re
from collections import deque

from harness.db import transaction, utcnow
from harness.knowledge.models import (
    MAX_SOURCE_BYTES,
    ContextBundle,
    ContextRequest,
    canonical,
    digest,
)
from harness.knowledge.policy import policy_digest, validate_scope
from harness.knowledge.sources import read_source

_DEFAULT_CONTEXT_REQUEST = ContextRequest()


def _partition(conn, scope, snapshot):
    versions = {
        r[0]
        for r in conn.execute(
            "SELECT version_id FROM knowledge_snapshot_sources WHERE snapshot_id=? "
            "AND membership_state='present'",
            (snapshot,),
        )
    }
    if scope.permitted_source_versions is not None:
        versions &= scope.permitted_source_versions
    if not versions:
        return {}, {}
    marks = ",".join("?" for _ in versions)
    candidates = conn.execute(
        "SELECT DISTINCT m.assertion_id FROM knowledge_snapshot_assertions m "
        "JOIN knowledge_evidence e ON e.assertion_id=m.assertion_id "
        f"WHERE m.snapshot_id=? AND e.version_id IN ({marks}) "
        "AND NOT EXISTS (SELECT 1 FROM knowledge_evidence denied "
        f"WHERE denied.assertion_id=m.assertion_id AND denied.version_id NOT IN ({marks}))",
        (snapshot, *sorted(versions), *sorted(versions)),
    ).fetchall()
    evidence, eligible = {}, {}
    for candidate in candidates:
        aid = candidate[0]
        row = conn.execute(
            "SELECT a.*, s.scoped_key AS subject, o.scoped_key AS object "
            "FROM knowledge_assertions a JOIN knowledge_entities s ON s.entity_id=a.subject_id "
            "LEFT JOIN knowledge_entities o ON o.entity_id=a.object_id "
            "WHERE a.assertion_id=? AND a.project_id=?",
            (aid, scope.project_id),
        ).fetchone()
        if row is None:
            continue
        refs = [
            dict(r)
            for r in conn.execute(
                "SELECT e.*, v.content_sha256, s.locator FROM knowledge_evidence e "
                "JOIN knowledge_source_versions v ON v.version_id=e.version_id "
                "JOIN knowledge_sources s ON s.source_id=v.source_id WHERE e.assertion_id=?",
                (aid,),
            )
        ]
        evidence[aid], eligible[aid] = refs, dict(row)
    # Reject incomplete/cyclic transitive provenance before matching or counting.
    parents = {
        aid: [
            r[0]
            for r in conn.execute(
                "SELECT parent_assertion_id FROM knowledge_derivations WHERE assertion_id=?", (aid,)
            )
        ]
        for aid in eligible
    }

    def allowed(aid, visiting):
        return (
            aid in eligible
            and aid not in visiting
            and all(allowed(parent, visiting | {aid}) for parent in parents[aid])
        )

    eligible = {aid: row for aid, row in eligible.items() if allowed(aid, set())}
    return eligible, evidence


def _citation_current(scope, ref):
    try:
        raw = read_source(scope.repository, ref["locator"], MAX_SOURCE_BYTES)
        return (
            digest(raw) == ref["content_sha256"]
            and digest(raw[ref["start_byte"] : ref["end_byte"]]) == ref["span_sha256"]
        )
    except (OSError, UnicodeError, ValueError):
        return False


def query(conn, scope, request: ContextRequest = _DEFAULT_CONTEXT_REQUEST) -> ContextBundle:
    request.limits.validate()
    if len(request.query.encode()) > 4096 or len(request.entity_refs) > 40:
        raise ValueError("knowledge request too large")
    project = validate_scope(conn, scope)
    snapshot = request.as_of_snapshot or project["active_snapshot_id"]
    snap = conn.execute(
        "SELECT * FROM knowledge_snapshots WHERE snapshot_id=? AND project_id=?",
        (snapshot, scope.project_id),
    ).fetchone()
    if not snap:
        raise ValueError("no authorized knowledge snapshot; refresh first")
    eligible, evidence = _partition(conn, scope, snapshot)
    tokens = set(re.findall(r"[\w.-]+", request.query.lower()))
    scored = []
    for aid, row in eligible.items():
        haystack = f"{row['subject']} {row['object'] or ''} {row['text']}".lower()
        score = sum(token in haystack for token in tokens)
        explicit = row["subject"] in request.entity_refs or row["object"] in request.entity_refs
        if explicit or score:
            scored.append((-int(explicit), -score, aid))
    scored.sort()
    seeds = [aid for _, _, aid in scored[: request.limits.assertions]]
    selected, paths, examined = list(seeds), {}, 0
    queue = deque((aid, 0, [aid]) for aid in seeds)
    while queue and examined < request.limits.examined_edges:
        aid, depth, path = queue.popleft()
        if depth >= request.limits.hops:
            continue
        row = eligible[aid]
        entities = {row["subject"], row["object"]} - {None}
        for other, edge in sorted(eligible.items()):
            if edge["predicate"] not in {"imports", "defines", "references", "supersedes"}:
                continue
            if not entities.intersection({edge["subject"], edge["object"]}):
                continue
            if examined >= request.limits.examined_edges:
                break
            examined += 1
            if other not in selected:
                selected.append(other)
                paths[other] = [*path, other]
                queue.append((other, depth + 1, paths[other]))
    ordered, visited = [], set()

    def include_parents(aid):
        if aid in visited:
            return
        visited.add(aid)
        for parent in conn.execute(
            "SELECT parent_assertion_id FROM knowledge_derivations WHERE assertion_id=?", (aid,)
        ):
            include_parents(parent[0])
        ordered.append(aid)

    for aid in selected:
        include_parents(aid)
    selected = ordered
    historical = snapshot != project["active_snapshot_id"]
    payload = {
        "schema_version": 1,
        "snapshot_id": snapshot,
        "policy_digest": policy_digest(scope),
        "purpose": scope.purpose,
        "query": request.query,
        "assertions": [],
        "citations": [],
        "paths": [],
        "ambiguities": [],
        "conflicts": [],
        "staleness": [],
        "coverage": json.loads(snap["coverage_json"]),
        "budget": {
            "max_bytes": request.limits.bytes,
            "max_assertions": request.limits.assertions,
            "max_hops": request.limits.hops,
            "max_examined_edges": request.limits.examined_edges,
            "examined_edges": examined,
            "serialized_bytes": 0,
        },
        "truncation": False,
        "notice": "Context grants no execution or approval authority; "
        "no result proves no dependency.",
    }
    # Coverage is project-wide metadata and cannot enter packet-restricted retrieval.
    if scope.permitted_source_versions is not None:
        payload["coverage"] = [
            {"status": "packet-local", "whole_repository_coverage": "not-assessed"}
        ]
    for aid in selected:
        if len(payload["assertions"]) >= request.limits.assertions:
            payload["truncation"] = True
            break
        parents = {
            r[0]
            for r in conn.execute(
                "SELECT parent_assertion_id FROM knowledge_derivations WHERE assertion_id=?", (aid,)
            )
        }
        if not parents <= {a["assertion_id"] for a in payload["assertions"]}:
            payload["truncation"] = True
            continue
        refs = evidence[aid]
        if not historical and not all(_citation_current(scope, ref) for ref in refs):
            payload["staleness"].append({"assertion_id": aid, "status": "withheld-stale-evidence"})
            continue
        row = eligible[aid]
        assertion = {
            k: row[k]
            for k in (
                "assertion_id",
                "subject",
                "predicate",
                "object",
                "text",
                "origin",
                "verification",
                "confidence",
            )
        }
        applicability = json.loads(row["applicability_json"])
        assertion.update(
            value=json.loads(row["value_json"]),
            historical=historical or bool(applicability.get("historical")),
            applicability=applicability,
        )
        citations = [
            {
                k: ref[k]
                for k in (
                    "assertion_id",
                    "version_id",
                    "locator",
                    "content_sha256",
                    "start_byte",
                    "end_byte",
                    "span_sha256",
                )
            }
            for ref in refs
        ]
        candidate = {
            **payload,
            "assertions": [*payload["assertions"], assertion],
            "citations": [*payload["citations"], *citations],
        }
        if aid in paths:
            packed = {item["assertion_id"] for item in candidate["assertions"]}
            if not set(paths[aid]) <= packed:
                continue
            candidate["paths"] = [*payload["paths"], paths[aid]]
        if len(canonical(candidate).encode()) + 128 > request.limits.bytes:
            payload["truncation"] = True
            continue
        payload = candidate
    # Only explicit single-valued predicates admit deterministic contradiction checks.
    groups = {}
    for assertion in payload["assertions"]:
        if assertion["predicate"] in {"status", "scope", "title"}:
            key = (
                assertion["subject"],
                assertion["predicate"],
                canonical(assertion["applicability"]),
            )
            groups.setdefault(key, []).append(assertion)
    for (subject, predicate, _), assertions in groups.items():
        if len({canonical(a["value"]) for a in assertions}) > 1:
            payload["conflicts"].append(
                {
                    "subject": subject,
                    "predicate": predicate,
                    "assertion_ids": [a["assertion_id"] for a in assertions],
                    "status": "incompatible-sourced-values",
                }
            )
    # Bound metadata as well as assertions. Never truncate evidence or a JSON value.
    if len(canonical(payload).encode()) + 128 > request.limits.bytes:
        payload["staleness"], payload["coverage"] = [], [{"status": "metadata-truncated"}]
        payload["conflicts"] = []
        payload["truncation"] = True
    while True:
        size = len(canonical({"bundle_hash": "0" * 64, **payload}).encode())
        if payload["budget"]["serialized_bytes"] == size:
            break
        payload["budget"]["serialized_bytes"] = size
    if size > request.limits.bytes:
        raise ValueError("request metadata exceeds bundle budget")
    validate_scope(conn, scope)
    # A last source read closes edits during selection; callers retry on a changed citation.
    if not historical and not all(_citation_current(scope, ref) for ref in payload["citations"]):
        raise ValueError("selected source changed before bundle publication")
    encoded = canonical(payload)
    bundle_hash = digest(encoded.encode())
    with transaction(conn):
        current = validate_scope(conn, scope)
        if not request.as_of_snapshot and current["active_snapshot_id"] != snapshot:
            raise ValueError("knowledge snapshot changed before bundle publication")
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_bundles VALUES (?,?,?,?,?,?,?,NULL)",
            (
                bundle_hash,
                scope.project_id,
                snapshot,
                policy_digest(scope),
                scope.purpose,
                encoded,
                utcnow(),
            ),
        )
        for ref in payload["citations"]:
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_bundle_sources VALUES (?,?)",
                (bundle_hash, ref["version_id"]),
            )
    return ContextBundle(bundle_hash, payload)


def explain(conn, scope, bundle_hash: str, assertion_id: str) -> dict:
    validate_scope(conn, scope)
    row = conn.execute(
        "SELECT * FROM knowledge_bundles WHERE bundle_hash=? AND project_id=?",
        (bundle_hash, scope.project_id),
    ).fetchone()
    if not row or row["invalidated_at"] or row["policy_digest"] != policy_digest(scope):
        raise PermissionError("bundle unavailable to current scope")
    payload = json.loads(row["payload"])
    assertions = [a for a in payload["assertions"] if a["assertion_id"] == assertion_id]
    if not assertions:
        raise ValueError("assertion absent from bundle")
    citations = [c for c in payload["citations"] if c["assertion_id"] == assertion_id]
    if not assertions[0]["historical"] and not all(_citation_current(scope, c) for c in citations):
        raise ValueError("bundle evidence is stale")
    return {
        "assertion": assertions[0],
        "citations": citations,
        "paths": [p for p in payload["paths"] if assertion_id in p],
        "authority": "derived context only",
    }
