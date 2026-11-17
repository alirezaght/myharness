"""Partition-first, bounded lexical and typed relationship retrieval."""

import json
import re
from collections import Counter, deque
from time import monotonic

from harness.db import transaction, utcnow
from harness.knowledge.identity import resolve
from harness.knowledge.models import (
    MAX_REFRESH_BYTES,
    MAX_SOURCE_BYTES,
    ContextBundle,
    ContextRequest,
    canonical,
    digest,
)
from harness.knowledge.policy import policy_digest, validate_scope
from harness.knowledge.receipts import record as record_receipt
from harness.knowledge.sources import SourceSizeCapped, _git, read_source

_DEFAULT_CONTEXT_REQUEST = ContextRequest()
CANDIDATE_CAP = 2000
_RELATIONS = {
    "imports",
    "defines",
    "references",
    "supersedes",
    "version-of",
    "governed-by",
    "designed-by",
    "observed-at",
}
_ROW = (
    "SELECT a.*,s.scoped_key AS subject,o.scoped_key AS object "
    "FROM knowledge_assertions a JOIN temp.k_eligible k USING(assertion_id) "
    "JOIN knowledge_entities s ON s.entity_id=a.subject_id "
    "LEFT JOIN knowledge_entities o ON o.entity_id=a.object_id "
)


def _partition(conn, scope, snapshot):
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS k_snapshot(snapshot_id TEXT)")
    conn.execute("DELETE FROM temp.k_snapshot")
    conn.execute("INSERT INTO temp.k_snapshot VALUES (?)", (snapshot,))
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS k_versions(version_id TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM temp.k_versions")
    conn.execute(
        "INSERT INTO temp.k_versions SELECT m.version_id FROM knowledge_snapshot_sources m "
        "JOIN knowledge_sources s USING(source_id) JOIN knowledge_source_versions v "
        "USING(version_id) "
        "WHERE m.snapshot_id=? AND s.project_id=? AND "
        "(json_extract(v.observation_json,'$.visibility') IS NULL "
        "OR json_extract(v.observation_json,'$.visibility')='project' "
        "OR json_extract(v.observation_json,'$.visibility')=?) "
        "AND m.membership_state='present' AND NOT EXISTS (SELECT 1 FROM knowledge_purges p "
        "WHERE p.target_kind='source-version' AND p.target_sha256=v.version_id) "
        "AND NOT EXISTS (SELECT 1 FROM knowledge_evidence pe JOIN knowledge_assertion_state ps "
        "USING(assertion_id) WHERE pe.version_id=v.version_id) "
        "AND (NOT EXISTS (SELECT 1 FROM knowledge_source_class c WHERE c.source_id=s.source_id "
        "AND c.material_class='lesson-import') AND NOT EXISTS "
        "(SELECT 1 FROM knowledge_import_sources i WHERE i.version_id=m.version_id) "
        "AND s.kind!='lesson' "
        "AND s.locator NOT LIKE 'docs/knowledge/imports/%' OR EXISTS "
        "(SELECT 1 FROM knowledge_import_sources i JOIN knowledge_grants g USING(grant_id) "
        "JOIN knowledge_imports l ON l.grant_id=i.grant_id AND l.lesson_id=i.lesson_id "
        "AND l.version=i.version AND l.project_id=? WHERE i.version_id=m.version_id "
        "AND g.source_project=json_extract(v.observation_json,'$.source_project') "
        "AND g.destination_project=? AND "
        "g.material_sha256=json_extract(v.observation_json,'$.lesson_sha256') "
        "AND g.approval_ref=json_extract(v.observation_json,'$.approval_ref') "
        "AND i.lesson_id=json_extract(v.observation_json,'$.lesson_id') "
        "AND i.version=json_extract(v.observation_json,'$.lesson_version') AND "
        "g.revoked_at IS NULL))",
        (
            snapshot,
            scope.project_id,
            "task:" + (scope.task_id or ""),
            scope.project_id,
            scope.project_id,
        ),
    )
    if scope.permitted_source_versions is not None:
        # No variable-number IN clause or corpus-dependent candidate statistics.
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS k_allowed(version_id TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM temp.k_allowed")
        conn.executemany(
            "INSERT INTO temp.k_allowed VALUES (?)",
            [(v,) for v in sorted(scope.permitted_source_versions)],
        )
        conn.execute(
            "DELETE FROM temp.k_versions WHERE version_id NOT IN (SELECT version_id FROM "
            "temp.k_allowed)"
        )
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS k_possible(assertion_id TEXT PRIMARY KEY,rowid INTEGER)"
    )
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS k_eligible(assertion_id TEXT PRIMARY KEY,rowid INTEGER)"
    )
    conn.execute("DELETE FROM temp.k_possible")
    conn.execute("DELETE FROM temp.k_eligible")
    conn.execute(
        "INSERT INTO temp.k_possible SELECT a.assertion_id,a.rowid FROM knowledge_assertions a "
        "JOIN knowledge_snapshot_assertions m USING(assertion_id) WHERE m.snapshot_id=? "
        "AND a.project_id=? AND NOT EXISTS (SELECT 1 FROM knowledge_assertion_state p "
        "WHERE p.assertion_id=a.assertion_id) AND EXISTS (SELECT 1 FROM knowledge_evidence e "
        "JOIN temp.k_versions v USING(version_id) WHERE e.assertion_id=a.assertion_id) "
        "AND NOT EXISTS (SELECT 1 FROM knowledge_evidence e WHERE e.assertion_id=a.assertion_id "
        "AND NOT EXISTS (SELECT 1 FROM temp.k_versions v WHERE v.version_id=e.version_id))",
        (snapshot, scope.project_id),
    )
    # Admit roots then nodes whose entire parent set is admitted. Cycles never enter.
    conn.execute(
        "INSERT INTO temp.k_eligible SELECT p.* FROM temp.k_possible p WHERE NOT EXISTS "
        "(SELECT 1 FROM knowledge_derivations d WHERE d.assertion_id=p.assertion_id)"
    )
    while True:
        changed = conn.execute(
            "INSERT OR IGNORE INTO temp.k_eligible SELECT p.* FROM temp.k_possible p "
            "WHERE NOT EXISTS (SELECT 1 FROM knowledge_derivations d "
            "WHERE d.assertion_id=p.assertion_id AND NOT EXISTS (SELECT 1 FROM temp.k_eligible e "
            "WHERE e.assertion_id=d.parent_assertion_id))"
        ).rowcount
        if not changed:
            break


def _evidence(conn, aid):
    refs = [
        dict(r)
        for r in conn.execute(
            "SELECT e.*,v.content_sha256,s.locator,CASE WHEN s.locator LIKE "
            "'docs/knowledge/imports/%' THEN 'lesson' ELSE s.kind END AS "
            "kind,v.observation_json FROM "
            "knowledge_evidence e "
            "JOIN knowledge_source_versions v USING(version_id) JOIN knowledge_sources s "
            "USING(source_id) "
            "JOIN temp.k_eligible k ON k.assertion_id=e.assertion_id WHERE e.assertion_id=? "
            "ORDER BY e.version_id,e.start_byte,e.end_byte",
            (aid,),
        )
    ]

    from harness.knowledge.commit_sources import snapshot_observation

    for ref in refs:
        ref["observation_json"] = canonical(snapshot_observation(conn, ref))
    return refs


def _citation_current(scope, ref, *, conn=None, cache=None, reads=None):
    cache = {} if cache is None else cache
    key = ref["version_id"]
    read_limit = MAX_SOURCE_BYTES
    try:
        if key not in cache:
            if reads is not None:
                if reads["count"] >= reads.get("max_count", 80) or (
                    reads["bytes"] >= reads.get("max_bytes", MAX_REFRESH_BYTES)
                ):
                    reads["limited"] = True
                    return False
                reads["count"] += 1
            if reads is not None:
                read_limit = min(
                    MAX_SOURCE_BYTES, reads.get("max_bytes", MAX_REFRESH_BYTES) - reads["bytes"]
                )
            kind = ref.get("kind", "text")
            observation = json.loads(ref["observation_json"])
            if kind in {"python", "text"} and observation.get("mode") == "commit":
                from harness.knowledge.commit_sources import read_blob

                raw = read_blob(scope.repository, observation, read_limit)
            elif kind in {"python", "text"}:
                raw = read_source(scope.repository, ref["locator"], read_limit)
            elif kind == "lesson" and conn is not None:
                from harness.knowledge.lessons import current_bytes

                raw = current_bytes(conn, scope, ref)
            elif (
                kind in {"requirements", "design", "checkpoint", "task-event", "execution-event"}
                and conn is not None
            ):
                from harness.knowledge.adapters import current_bytes

                raw = current_bytes(conn, scope, kind, json.loads(ref["observation_json"]))
            elif kind == "contribution" and conn is not None:
                from harness.knowledge.contributions import current_bytes

                raw = current_bytes(conn, scope, json.loads(ref["observation_json"]))
            elif kind == "artifact":
                observation = json.loads(ref["observation_json"])
                if observation.get("artifact_sha256") != ref["content_sha256"]:
                    return False
                raw = read_source(scope.repository, observation["artifact_locator"], read_limit)
            else:
                # Contribution and lesson carriers require their supervisor-validated resolver.
                return False
            if len(raw) > read_limit:
                if reads is not None:
                    reads["limited"] = True
                cache[key] = (b"", "")
                return False
            if reads is not None:
                reads["bytes"] += len(raw)
            cache[key] = (raw, digest(raw))
        raw, content_hash = cache[key]
        return (
            0 <= ref["start_byte"] <= ref["end_byte"] <= len(raw)
            and content_hash == ref["content_sha256"]
            and digest(raw[ref["start_byte"] : ref["end_byte"]]) == ref["span_sha256"]
        )
    except (OSError, UnicodeError, ValueError, KeyError, LookupError) as error:
        if (
            reads is not None
            and isinstance(error, SourceSizeCapped)
            and read_limit < MAX_SOURCE_BYTES
        ):
            reads["limited"] = True
        cache[key] = (b"", "")
        return False


def _head_observation(conn, scope, snapshot):
    cursor = conn.execute(
        "SELECT head,branch,mode FROM knowledge_snapshot_cursor WHERE snapshot_id=?", (snapshot,)
    ).fetchone()
    if cursor and cursor["mode"] == "commit":
        return [{"status": "immutable-commit-evidence", "commit": cursor["head"]}]
    if not cursor or not cursor["head"]:
        return []
    head = _git(scope.repository, "rev-parse", "HEAD")
    branch = _git(scope.repository, "symbolic-ref", "--quiet", "HEAD")
    current = head.stdout.decode().strip() if not head.returncode else None
    current_branch = branch.stdout.decode().strip() if not branch.returncode else None
    if current == cursor["head"]:
        return [] if current_branch == cursor["branch"] else [{"status": "snapshot-branch-changed"}]
    if not current:
        return [{"status": "ancestry-unknown"}]
    ancestry = _git(scope.repository, "merge-base", "--is-ancestor", cursor["head"], current)
    return [
        {
            "status": {0: "snapshot-behind-head", 1: "snapshot-diverged"}.get(
                ancestry.returncode, "ancestry-unknown"
            )
        }
    ]


def _candidates(conn, scope, request, exclusions):
    references, ambiguities = set(), []
    for ref in request.entity_refs:
        alternatives = resolve(conn, scope.project_id, ref)
        references.update(alternatives)
        if len(alternatives) != 1:
            ambiguities.append(
                {
                    "reference": ref,
                    "status": "ambiguous-identity" if alternatives else "unresolved-identity",
                    "alternatives": list(alternatives),
                }
            )
    tokens = sorted(set(re.findall(r"[\w.-]+", request.query.lower())))[:64]
    ids = set()
    if tokens:
        expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        ids.update(
            r[0]
            for r in conn.execute(
                "SELECT k.assertion_id FROM knowledge_assertion_fts f "
                "JOIN temp.k_eligible k ON k.rowid=f.rowid WHERE knowledge_assertion_fts MATCH ? "
                "ORDER BY k.assertion_id LIMIT ?",
                (expression, CANDIDATE_CAP),
            )
        )
        # Legacy snapshots use a bounded permitted-only slice, without backfill.
        legacy = conn.execute(
            _ROW
            + "WHERE NOT EXISTS (SELECT 1 FROM knowledge_assertion_fts f WHERE f.rowid=a.rowid) "
            "ORDER BY a.assertion_id LIMIT ?",
            (CANDIDATE_CAP,),
        ).fetchall()
        for row in legacy:
            haystack = f"{row['subject']} {row['object'] or ''} {row['text']}".lower()
            if any(t in haystack for t in tokens):
                ids.add(row["assertion_id"])
        if len(legacy) == CANDIDATE_CAP:
            exclusions["legacy-candidate-cap"] += 1
    explicit = set()
    for ref in sorted(references):
        if len(explicit) >= CANDIDATE_CAP:
            exclusions["candidate-cap"] += 1
            break
        explicit.update(
            r[0]
            for r in conn.execute(
                "SELECT a.assertion_id FROM knowledge_assertions a JOIN temp.k_eligible k "
                "USING(assertion_id) "
                "WHERE a.subject_id=(SELECT entity_id FROM knowledge_entities WHERE "
                "project_id=? AND scoped_key=?) "
                "OR a.object_id=(SELECT entity_id FROM knowledge_entities WHERE "
                "project_id=? AND scoped_key=?) "
                "ORDER BY a.assertion_id LIMIT ?",
                (scope.project_id, ref, scope.project_id, ref, CANDIDATE_CAP - len(explicit)),
            )
        )
    ids.update(explicit)
    if len(ids) >= CANDIDATE_CAP:
        exclusions["candidate-cap"] += 1
    ids = [*sorted(explicit), *sorted(ids - explicit)][:CANDIDATE_CAP]
    rows = {}
    for aid in ids:
        row = conn.execute(_ROW + "WHERE a.assertion_id=?", (aid,)).fetchone()
        rows[aid] = dict(row)
    ordered = sorted(
        rows,
        key=lambda aid: (
            -int(aid in explicit),
            -sum(
                t
                in f"{rows[aid]['subject']} {rows[aid]['object'] or ''} {rows[aid]['text']}".lower()
                for t in tokens
            ),
            aid,
        ),
    )
    return rows, ordered, ambiguities, references


def _path(row, entity):
    reverse = row["object"] == entity
    return {
        "assertion_id": row["assertion_id"],
        "predicate": row["predicate"],
        "direction": "reverse" if reverse else "forward",
        "from": row["object"] if reverse else row["subject"],
        "to": row["subject"] if reverse else row["object"],
    }


def _expand(conn, rows, seeds, references, limits, exclusions):
    selected, paths = list(seeds), {}
    queue = deque()
    for aid in seeds:
        row = rows[aid]
        edge = next(
            (
                _path(row, ref)
                for ref in sorted(references)
                if ref in {row["subject"], row["object"]}
            ),
            None,
        )
        if edge and limits.hops and row["object"] and row["predicate"] in _RELATIONS:
            paths[aid] = {"assertion_ids": [aid], "edges": [edge], "impact": "likely-source-impact"}
        queue.append((aid, 0))
    examined = 0
    while queue and examined < limits.examined_edges:
        aid, depth = queue.popleft()
        if depth >= limits.hops:
            continue
        row = rows[aid]
        for entity_id in sorted({row["subject_id"], row["object_id"]} - {None}):
            remaining = limits.examined_edges - examined
            if not remaining:
                break
            edges = conn.execute(
                _ROW + "WHERE (a.subject_id=? OR a.object_id=?) ORDER BY a.assertion_id LIMIT ?",
                (entity_id, entity_id, remaining),
            ).fetchall()
            examined += len(edges)
            for edge in edges:
                other = edge["assertion_id"]
                if other in selected:
                    continue
                rows[other] = dict(edge)
                selected.append(other)
                connecting = row["subject"] if entity_id == row["subject_id"] else row["object"]
                prefix = list(paths.get(aid, {"edges": []})["edges"])
                if prefix and prefix[-1]["to"] != connecting:
                    # A branch at an earlier node starts from that node's actual prefix.
                    cut = next(
                        (i for i, step in enumerate(prefix) if step["from"] == connecting), None
                    )
                    prefix = prefix[:cut] if cut is not None else []
                typed = [*prefix, _path(edge, connecting)]
                if len(typed) <= limits.hops:
                    paths[other] = {
                        "assertion_ids": [step["assertion_id"] for step in typed],
                        "edges": typed,
                        "impact": "likely-source-impact",
                    }
                queue.append((other, depth + 1))
    if queue and examined == limits.examined_edges:
        exclusions["edge-budget"] += 1
    return selected, paths, examined


def _integrity(rows):
    groups, decisions, conflicts = {}, {}, []
    for row in rows.values():
        if row["verification"] == "broken-evidence":
            conflicts.append({"assertion_id": row["assertion_id"], "status": "broken-evidence"})
        applicability = json.loads(row["applicability_json"])
        if row["predicate"] in {"title", "status", "scope", "binding"}:
            key = (row["subject"], row["predicate"], canonical(applicability))
            groups.setdefault(key, []).append(row)
        if (
            row["predicate"] == "version-of"
            and row["subject"].startswith("decision:")
            and not applicability.get("historical")
        ):
            decisions.setdefault(row["object"], []).append(row)
    for (subject, predicate, _), claims in groups.items():
        if len({r["value_json"] for r in claims}) > 1:
            conflicts.append(
                {
                    "subject": subject,
                    "predicate": predicate,
                    "status": "incompatible-sourced-values",
                    "assertion_ids": sorted(r["assertion_id"] for r in claims),
                }
            )
    for subject, claims in decisions.items():
        if len({r["subject"] for r in claims}) > 1:
            conflicts.append(
                {
                    "subject": subject,
                    "status": "conflicting-current-decision-versions",
                    "assertion_ids": sorted(r["assertion_id"] for r in claims),
                }
            )
    return conflicts


def _fit(payload, limit, exclusions):
    while True:
        payload["exclusions"] = dict(sorted(exclusions.items()))
        payload["truncation"] = bool(exclusions)
        size = len(canonical({"bundle_hash": "0" * 64, **payload}).encode())
        if payload["budget"]["serialized_bytes"] != size:
            payload["budget"]["serialized_bytes"] = size
            continue
        if size <= limit:
            return
        if payload["assertions"]:
            removed = payload["assertions"].pop()["assertion_id"]
            payload["citations"] = [c for c in payload["citations"] if c["assertion_id"] != removed]
            payload["paths"] = [p for p in payload["paths"] if removed not in p["assertion_ids"]]
            exclusions["byte-budget"] += 1
            continue
        # Preserve each integrity kind and its count; never silently erase observations.
        collapsed = False
        for field in ("coverage", "staleness", "conflicts", "ambiguities"):
            observations = payload[field]
            if observations and not (len(observations) == 1 and observations[0].get("summarized")):
                counts = Counter(item.get("status", "observation") for item in observations)
                payload[field] = [
                    {"status": "integrity-summary", "summarized": True, "counts": dict(counts)}
                ]
                exclusions["metadata-detail-budget"] += 1
                collapsed = True
                break
        if not collapsed:
            raise ValueError("request metadata exceeds bundle budget")


def query(conn, scope, request: ContextRequest = _DEFAULT_CONTEXT_REQUEST) -> ContextBundle:
    started, reads = (
        monotonic(),
        {"count": 0, "bytes": 0, "max_count": 40, "max_bytes": MAX_REFRESH_BYTES // 2},
    )
    request.limits.validate()
    if (
        len(request.query.encode()) > 4096
        or len(request.entity_refs) > 40
        or any(len(ref.encode()) > 512 for ref in request.entity_refs)
    ):
        raise ValueError("knowledge request too large")
    project = validate_scope(conn, scope)
    if scope.snapshot_id and request.as_of_snapshot not in {None, scope.snapshot_id}:
        raise PermissionError("review snapshot is pinned")
    snapshot = scope.snapshot_id or request.as_of_snapshot or project["active_snapshot_id"]
    snap = conn.execute(
        "SELECT * FROM knowledge_snapshots WHERE snapshot_id=? AND project_id=?",
        (snapshot, scope.project_id),
    ).fetchone()
    if not snap:
        raise ValueError("no authorized knowledge snapshot; refresh first")
    _partition(conn, scope, snapshot)
    exclusions = Counter()
    rows, ordered, ambiguities, references = _candidates(conn, scope, request, exclusions)
    seeds = ordered[: request.limits.assertions]
    if len(ordered) > len(seeds):
        exclusions["assertion-budget"] += len(ordered) - len(seeds)
    selected, paths, examined = _expand(conn, rows, seeds, references, request.limits, exclusions)
    immutable = json.loads(snap["observation_json"]).get("mode") == "commit"
    historical = immutable or (
        scope.snapshot_id is None
        and request.as_of_snapshot is not None
        and snapshot != project["active_snapshot_id"]
    )
    payload = {
        "schema_version": 2,
        "snapshot_id": snapshot,
        "policy_digest": policy_digest(scope),
        "purpose": scope.purpose,
        "query": request.query,
        "entity_refs": list(request.entity_refs),
        "as_of_snapshot": request.as_of_snapshot,
        "evidence_mode": "commit" if immutable else "working-tree",
        "commit": json.loads(snap["observation_json"]).get("head") if immutable else None,
        "assertions": [],
        "citations": [],
        "paths": [],
        "ambiguities": ambiguities,
        "conflicts": _integrity(rows),
        "staleness": [],
        "coverage": [
            c
            for c in json.loads(snap["coverage_json"])
            if c.get("visibility", "project") in {"project", "task:" + (scope.task_id or "")}
        ],
        "exclusions": {},
        "truncation": False,
        "budget": {
            "max_bytes": request.limits.bytes,
            "max_assertions": request.limits.assertions,
            "max_hops": request.limits.hops,
            "max_examined_edges": request.limits.examined_edges,
            "max_source_reads": 80,
            "max_source_bytes": MAX_REFRESH_BYTES,
            "examined_edges": examined,
            "serialized_bytes": 0,
        },
        "notice": "Context grants no execution or approval authority; "
        "no result proves no dependency.",
    }
    if scope.permitted_source_versions is not None:
        payload["coverage"] = [
            {"status": "packet-local", "whole_repository_coverage": "not-assessed"}
        ]
    else:
        if not historical or immutable:
            payload["staleness"].extend(_head_observation(conn, scope, snapshot))
        for row in conn.execute(
            "SELECT m.membership_state,s.locator,t.reason FROM knowledge_snapshot_sources m "
            "JOIN knowledge_sources s USING(source_id) JOIN knowledge_source_versions v "
            "USING(version_id) "
            "LEFT JOIN knowledge_snapshot_source_state t "
            "ON t.snapshot_id=m.snapshot_id AND t.source_id=m.source_id WHERE m.snapshot_id=? "
            "AND m.membership_state!='present' AND "
            "coalesce(json_extract(t.detail_json,'$.visibility'),"
            "json_extract(v.observation_json,'$.visibility'),'project') IN ('project',?) "
            "ORDER BY s.locator LIMIT 200",
            (snapshot, "task:" + (scope.task_id or "")),
        ):
            payload["coverage"].append(
                {
                    "status": "source-removed" if row[0] == "deleted" else "source-unknown",
                    "locator": row[1],
                    "reason": row[2],
                }
            )
        attempt = conn.execute(
            "SELECT coverage_json FROM knowledge_refresh_attempts WHERE project_id=? "
            "AND created_at>? AND status='incomplete' ORDER BY created_at DESC LIMIT 1",
            (scope.project_id, snap["created_at"]),
        ).fetchone()
        if attempt:
            payload["coverage"].extend(
                dict(c, since_snapshot=True)
                for c in json.loads(attempt[0])
                if c.get("visibility", "project") in {"project", "task:" + (scope.task_id or "")}
            )
    cache, emitted, visiting = {}, set(), set()

    def include(aid):
        nonlocal examined
        if aid in emitted or aid in visiting:
            return
        if len(payload["assertions"]) >= request.limits.assertions:
            exclusions["assertion-budget"] += 1
            return
        if len(visiting) >= request.limits.assertions:
            exclusions["parent-budget"] += 1
            return
        visiting.add(aid)
        parents = [
            r[0]
            for r in conn.execute(
                "SELECT d.parent_assertion_id FROM knowledge_derivations d "
                "JOIN temp.k_eligible k ON k.assertion_id=d.parent_assertion_id "
                "WHERE d.assertion_id=? ORDER BY 1 LIMIT ?",
                (aid, min(request.limits.assertions + 1, request.limits.examined_edges - examined)),
            )
        ]
        examined += len(parents)
        for parent in parents:
            if parent not in rows:
                rows[parent] = dict(
                    conn.execute(_ROW + "WHERE a.assertion_id=?", (parent,)).fetchone()
                )
            include(parent)
        visiting.remove(aid)
        if (
            not set(parents) <= emitted
            or conn.execute(
                "SELECT 1 FROM knowledge_derivations d WHERE d.assertion_id=? AND NOT EXISTS "
                "(SELECT 1 FROM temp.k_eligible e WHERE "
                "e.assertion_id=d.parent_assertion_id) LIMIT 1",
                (aid,),
            ).fetchone()
        ):
            exclusions["parent-budget"] += 1
            return
        if len(payload["assertions"]) >= request.limits.assertions:
            exclusions["assertion-budget"] += 1
            return
        all_parents = conn.execute(
            "SELECT count(*) FROM knowledge_derivations WHERE assertion_id=?", (aid,)
        ).fetchone()[0]
        if all_parents != len(parents):
            exclusions["parent-budget"] += 1
            return
        refs = _evidence(conn, aid)
        if not all(
            (historical and not immutable and ref["kind"] != "lesson")
            or _citation_current(scope, ref, conn=conn, cache=cache, reads=reads)
            for ref in refs
        ):
            payload["staleness"].append({"assertion_id": aid, "status": "withheld-stale-evidence"})
            return
        row = rows[aid]
        applicability = json.loads(row["applicability_json"])
        assertion = {
            key: row[key]
            for key in (
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
        assertion.update(
            value=json.loads(row["value_json"]),
            applicability=applicability,
            historical=historical or bool(applicability.get("historical")),
        )
        payload["assertions"].append(assertion)
        payload["citations"].extend(
            {
                key: ref[key]
                for key in (
                    "assertion_id",
                    "version_id",
                    "locator",
                    "content_sha256",
                    "start_byte",
                    "end_byte",
                    "span_sha256",
                    "observation_json",
                )
            }
            for ref in refs
        )
        if aid in paths:
            payload["paths"].append(paths[aid])
        emitted.add(aid)

    for aid in selected:
        include(aid)
    if reads.get("limited"):
        exclusions["source-read-budget"] += 1
    reads.update(max_count=80, max_bytes=MAX_REFRESH_BYTES)
    packed = {a["assertion_id"] for a in payload["assertions"]}
    payload["paths"] = [p for p in payload["paths"] if set(p["assertion_ids"]) <= packed]
    payload["budget"]["examined_edges"] = examined
    _fit(payload, request.limits.bytes, exclusions)
    # A new read pass closes source edits during candidate selection and packing.
    final_cache = {}
    if (
        not historical
        or immutable
        or any(a["applicability"].get("imported") for a in payload["assertions"])
    ):
        for aid in {c["assertion_id"] for c in payload["citations"]}:
            if not all(
                (historical and not immutable and ref["kind"] != "lesson")
                or _citation_current(scope, ref, conn=conn, cache=final_cache, reads=reads)
                for ref in _evidence(conn, aid)
            ):
                raise ValueError("selected source changed before bundle publication")
    encoded = canonical(payload)
    bundle_hash = digest(encoded.encode())
    expired = conn.execute(
        "SELECT invalidated_at FROM knowledge_bundles WHERE bundle_hash=?", (bundle_hash,)
    ).fetchone()
    if expired and expired[0]:
        # A new retrieval may issue new immutable bytes, never restore an expired row.
        payload["new_issuance"] = digest((bundle_hash + utcnow()).encode())
        _fit(payload, request.limits.bytes, exclusions)
        encoded = canonical(payload)
        bundle_hash = digest(encoded.encode())
    with transaction(conn):
        current = validate_scope(conn, scope)
        if (
            not scope.snapshot_id
            and not request.as_of_snapshot
            and current["active_snapshot_id"] != snapshot
        ):
            raise ValueError("knowledge snapshot changed before bundle publication")
        # Rebuild the partition to close revocation/purge races, including historical reads.
        _partition(conn, scope, snapshot)
        if any(
            not conn.execute(
                "SELECT 1 FROM temp.k_eligible WHERE assertion_id=?", (a["assertion_id"],)
            ).fetchone()
            for a in payload["assertions"]
        ):
            raise PermissionError("selected knowledge no longer permitted")
        existing = conn.execute(
            "SELECT invalidated_at FROM knowledge_bundles WHERE bundle_hash=?", (bundle_hash,)
        ).fetchone()
        if existing and existing[0]:
            raise PermissionError("bundle-invalidated during publication; query again")
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_bundles VALUES (?,?,?,?,?,?,?,NULL)",
            (
                bundle_hash,
                scope.project_id,
                snapshot,
                payload["policy_digest"],
                scope.purpose,
                encoded,
                utcnow(),
            ),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO knowledge_bundle_sources VALUES (?,?)",
            [(bundle_hash, v) for v in sorted({c["version_id"] for c in payload["citations"]})],
        )
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_bundle_scope VALUES (?,?,?,?,?,?)",
            (
                bundle_hash,
                scope.principal_id,
                scope.purpose,
                scope.packet_hash,
                scope.policy_generation,
                scope.execution_id,
            ),
        )
        if scope.execution_id:
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_bundle_pins VALUES (?,?)",
                (bundle_hash, f"execution:{scope.execution_id}"),
            )
        record_receipt(conn, scope, payload, bundle_hash, reads, started)
    return ContextBundle(bundle_hash, payload)


def explain(conn, scope, bundle_hash: str, assertion_id: str, *, include_evidence=False) -> dict:
    started, reads = monotonic(), {"count": 0, "bytes": 0}
    validate_scope(conn, scope)
    row = conn.execute(
        "SELECT * FROM knowledge_bundles WHERE bundle_hash=? AND project_id=?",
        (bundle_hash, scope.project_id),
    ).fetchone()
    state = conn.execute(
        "SELECT reason FROM knowledge_bundle_state WHERE bundle_hash=?", (bundle_hash,)
    ).fetchone()
    if state:
        code = {"purged": "bundle-purged", "expired": "bundle-expired"}.get(
            state[0], "bundle-invalidated"
        )
        raise PermissionError(code + ": bundle unavailable to current scope")
    if (
        not row
        or row["invalidated_at"]
        or row["policy_digest"] != policy_digest(scope)
        or not row["payload"]
    ):
        raise PermissionError("bundle unavailable to current scope")
    payload = json.loads(row["payload"])
    _partition(conn, scope, payload["snapshot_id"])
    if any(
        not conn.execute(
            "SELECT 1 FROM temp.k_eligible WHERE assertion_id=?", (a["assertion_id"],)
        ).fetchone()
        for a in payload["assertions"]
    ):
        raise PermissionError("bundle contains revoked or purged evidence")
    assertion = next((a for a in payload["assertions"] if a["assertion_id"] == assertion_id), None)
    if assertion is None:
        raise ValueError("assertion absent from bundle")
    snapshot = conn.execute(
        "SELECT active_snapshot_id FROM knowledge_projects WHERE project_id=?", (scope.project_id,)
    ).fetchone()[0]
    historical = (
        payload.get("as_of_snapshot") is not None
        and payload["snapshot_id"] != snapshot
        and scope.snapshot_id is None
    )
    cache = {}
    if (
        not historical
        or payload.get("evidence_mode") == "commit"
        or any(a["applicability"].get("imported") for a in payload["assertions"])
    ):
        for item in payload["assertions"]:
            if not all(
                (
                    historical
                    and payload.get("evidence_mode") != "commit"
                    and ref["kind"] != "lesson"
                )
                or _citation_current(scope, ref, conn=conn, cache=cache, reads=reads)
                for ref in _evidence(conn, item["assertion_id"])
            ):
                raise ValueError("bundle evidence is stale")
    citations = [dict(c) for c in payload["citations"] if c["assertion_id"] == assertion_id]
    if include_evidence:
        for ref in _evidence(conn, assertion_id):
            if not _citation_current(scope, ref, conn=conn, cache=cache, reads=reads):
                raise ValueError("selected evidence bytes are unavailable or stale")
        for citation in citations:
            raw = cache[citation["version_id"]][0]
            excerpt = raw[citation["start_byte"] : citation["end_byte"]]
            citation.update(
                text=excerpt[:4096].decode("utf-8", errors="ignore"), truncated=len(excerpt) > 4096
            )
    with transaction(conn):
        validate_scope(conn, scope)
        current_bundle = conn.execute(
            "SELECT invalidated_at,payload FROM knowledge_bundles WHERE bundle_hash=?",
            (bundle_hash,),
        ).fetchone()
        if not current_bundle or current_bundle[0] or not current_bundle[1]:
            raise PermissionError("bundle-invalidated during explanation")
        _partition(conn, scope, payload["snapshot_id"])
        if any(
            not conn.execute(
                "SELECT 1 FROM temp.k_eligible WHERE assertion_id=?", (item["assertion_id"],)
            ).fetchone()
            for item in payload["assertions"]
        ):
            raise PermissionError("bundle contains revoked or purged evidence")
        record_receipt(conn, scope, payload, bundle_hash, reads, started)
    return {
        "assertion": assertion,
        "citations": citations,
        "paths": [
            p
            for p in payload["paths"]
            if assertion_id in (p.get("assertion_ids", []) if isinstance(p, dict) else p)
        ],
        "authority": "derived context only",
    }
