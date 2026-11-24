"""Extract outside SQLite locks, then revalidate and publish atomically."""

import json
import sqlite3
from dataclasses import replace

from harness.db import transaction
from harness.knowledge.extract import extract_source, with_code_edges
from harness.knowledge.models import (
    CODE_FACTS_EXTRACTOR,
    EXTRACTOR_VERSION,
    EXTRACTORS,
    MAX_REFRESH_BYTES,
    Extraction,
    PreparedSnapshot,
    RefreshRequest,
    RefreshResult,
    digest,
    version_identity,
)
from harness.knowledge.policy import validate_scope
from harness.knowledge.sources import (
    ExcludedSource,
    _git,
    enumerate_sources,
    inventory_names,
    read_source,
    stat_identity,
)
from harness.knowledge.store import (
    SnapshotConflict,
    ensure_project,
    prepare_rows,
    publish_snapshot,
    record_refresh_attempt,
)

_DEFAULT_REFRESH_REQUEST = RefreshRequest()
MAX_PUBLISH_SOURCES = 2000
# Up to this many changed code files are named to AL; more ask for every file's edges.
MAX_NAMED_CODE_FILES = 200


def _blob_id(raw, length):
    import hashlib

    algorithm = hashlib.sha256 if length == 64 else hashlib.sha1
    return algorithm(b"blob %d\0" % len(raw) + raw).hexdigest()


def _code_facts(facts, sources, cached, commit, coverage):
    """{locator: (blob id, edges)} from AL's code facts (#34) for the code sources this refresh
    extracts with them."""
    from harness.knowledge.al_facts import FactsUnavailable

    wanted = [
        s.locator
        for s in sources
        if s.extractor == CODE_FACTS_EXTRACTOR and s.raw_retained and s.version_id not in cached
    ]
    if not wanted or facts is None:
        return {}
    named = sorted(wanted) if len(wanted) <= MAX_NAMED_CODE_FILES else []
    try:
        answer = facts.query("edges", *named, *(["--commit", commit] if commit else []))
    except FactsUnavailable as error:
        coverage.append(
            {"status": "unresolved", "reason": "al-facts-unavailable", "detail": str(error)}
        )
        return {}
    found = {locator: (oid, []) for locator, oid in answer.get("files", {}).items()}
    for edge in answer["edges"]:
        if edge["locator"] in found:
            found[edge["locator"]][1].append(edge)
    return found


def _with_code_facts(source, found):
    """The source as extracted: its AL code-facts version when AL's edges describe exactly its
    bytes, else (AL unavailable, or the file changed in between) its text-and-symbols version."""
    if source.extractor != CODE_FACTS_EXTRACTOR or not source.raw_retained:
        return source, None
    oid, edges = found.get(source.locator, (None, None))
    if oid is not None and _blob_id(source.raw, len(oid)) == oid:
        return source, edges
    version = version_identity(
        source.source_id, source.content_sha256, EXTRACTOR_VERSION, source.kind, source.visibility
    )
    return replace(source, version_id=version, extractor=EXTRACTOR_VERSION), None


def _record_incomplete(conn, scope, coverage):
    with transaction(conn):
        project = validate_scope(conn, scope)
        record_refresh_attempt(conn, scope.project_id, "incomplete", coverage)
        return RefreshResult(project["active_snapshot_id"], "incomplete", tuple(coverage))


def _cursor(conn, scope, expected, observation):
    parent = conn.execute(
        "SELECT observation_json FROM knowledge_snapshots WHERE snapshot_id=?", (expected,)
    ).fetchone()
    parent_head = json.loads(parent[0]).get("head") if parent else None
    head = observation.get("head")
    ancestry = "unknown"
    if not parent:
        ancestry = "initial"
    elif head and parent_head:
        if head == parent_head:
            ancestry = "same"
        else:
            result = _git(scope.repository, "merge-base", "--is-ancestor", parent_head, head)
            ancestry = {0: "descendant", 1: "diverged"}.get(result.returncode, "unknown")
    return {
        "mode": observation.get("mode", "working-tree"),
        "parent_head": parent_head,
        "ancestry": ancestry,
    }


def refresh(conn, scope, request: RefreshRequest = _DEFAULT_REFRESH_REQUEST) -> RefreshResult:
    ensure_project(conn, scope)
    project = validate_scope(conn, scope)
    if scope.purpose not in {"operator", "investigation"}:
        raise PermissionError("refresh requires an operator or investigation scope")
    from harness.knowledge.commit_sources import pin_request
    from harness.knowledge.retention import version_purged

    request = pin_request(scope.repository, request)
    expected = project["active_snapshot_id"]
    try:
        cached = frozenset(
            row[0]
            for row in conn.execute(
                "SELECT version_id FROM knowledge_extraction_cache "
                "WHERE extractor_version IN (?,?)",
                EXTRACTORS,
            )
        )
        from harness.knowledge import al_facts

        facts = al_facts.resolve(scope.repository)
        inventory = enumerate_sources(scope, request, cached, facts is not None)
    except (OSError, ValueError):
        _record_incomplete(conn, scope, [{"status": "unreadable", "kind": "inventory"}])
        raise
    coverage = list(inventory.coverage)
    found = _code_facts(facts, inventory.sources, cached, request.commit, coverage)
    code_edges, adjusted = {}, []
    for source in inventory.sources:
        source, edges = _with_code_facts(source, found)
        if edges is not None:
            code_edges[source.version_id] = edges
        adjusted.append(source)
    inventory = replace(inventory, sources=tuple(adjusted))
    unknown = [dict(c, reason=c.get("reason", c["status"])) for c in coverage if c.get("locator")]
    stable, extractions, reused = [], [], set()
    previous = {
        r["source_id"]: dict(r)
        for r in conn.execute(
            "SELECT m.*,s.locator,c.material_class FROM knowledge_snapshot_sources m "
            "JOIN knowledge_sources s USING(source_id) "
            "LEFT JOIN knowledge_source_class c USING(source_id) "
            "WHERE snapshot_id=?",
            (expected,),
        )
    }
    changed_count = 0
    import_changed = False
    # Processing is bounded by source count, retained bytes and changed-source publication count.
    for source in inventory.sources:
        if source.locator.startswith("docs/knowledge/imports/"):
            from harness.knowledge.lessons import admit_import

            try:
                source = admit_import(conn, scope, source)
                mapping = conn.execute(
                    "SELECT grant_id FROM knowledge_import_sources WHERE version_id=?",
                    (source.version_id,),
                ).fetchone()
                import_changed = (
                    import_changed or not mapping or mapping[0] != source.observation["grant_id"]
                )
            except (ValueError, OSError, KeyError):
                gap = {
                    "locator": source.locator,
                    "status": "withheld",
                    "reason": "ungranted-lesson",
                    "visibility": source.visibility,
                }
                coverage.append(gap)
                unknown.append(gap)
                continue
        if version_purged(conn, source.version_id):
            gap = {
                "locator": source.locator,
                "status": "purged",
                "reason": "purged",
                "visibility": source.visibility,
                "version_id": source.version_id,
            }
            coverage.append(gap)
            unknown.append(gap)
            continue
        old = previous.get(source.source_id)
        changed = (
            not old
            or old["version_id"] != source.version_id
            or old["membership_state"] != "present"
        )
        if changed and changed_count >= MAX_PUBLISH_SOURCES:
            gap = {
                "locator": source.locator,
                "status": "deferred",
                "reason": "publish-batch-deferred",
                "visibility": source.visibility,
            }
            coverage.append(gap)
            unknown.append(gap)
            continue
        cache = conn.execute(
            "SELECT coverage_json FROM knowledge_extraction_cache "
            "WHERE version_id=? AND extractor_version=?",
            (source.version_id, source.extractor),
        ).fetchone()
        if not cache and not source.raw_retained:
            # The cache entry disappeared after inventory (purge): extract it next refresh.
            gap = {
                "locator": source.locator,
                "status": "deferred",
                "reason": "publish-batch-deferred",
                "visibility": source.visibility,
            }
            coverage.append(gap)
            unknown.append(gap)
            continue
        try:
            if source.kind == "lesson":
                from harness.knowledge.lessons import extract_import

                extraction = (
                    Extraction((), tuple(json.loads(cache[0]))) if cache else extract_import(source)
                )
            else:
                extraction = (
                    Extraction((), tuple(json.loads(cache[0])))
                    if cache
                    else with_code_edges(
                        extract_source(source), source, code_edges.get(source.version_id)
                    )
                )
        except (OSError, ValueError, SyntaxError):
            gap = {
                "locator": source.locator,
                "status": "failed",
                "reason": "extraction-failed",
                "visibility": source.visibility,
            }
            coverage.append(gap)
            unknown.append(gap)
            continue
        extraction = replace(
            extraction,
            coverage=tuple(
                dict(gap, visibility=source.visibility, version_id=source.version_id)
                for gap in extraction.coverage
            ),
        )
        if cache:
            reused.add(source.version_id)
        stable.append(source)
        extractions.append(extraction)
        changed_count += int(changed)
    try:
        names, observation = inventory_names(scope.repository, request)
    except (OSError, ValueError):
        return _record_incomplete(
            conn, scope, [*coverage, {"status": "unstable", "kind": "inventory"}]
        )
    if observation != inventory.observation or (
        inventory.names is not None and frozenset(names) != inventory.names
    ):
        return _record_incomplete(
            conn, scope, [*coverage, {"status": "unstable", "kind": "inventory"}]
        )
    valid_sources, valid_extractions, retained_bytes = [], [], 0
    for source, extraction in zip(stable, extractions, strict=True):
        if (
            not source.raw_retained
            and source.stat is not None
            and stat_identity(scope.repository.resolve(), source.locator) == source.stat
        ):
            # Hash-only and untouched since it was hashed: membership stays exactly as read.
            valid_sources.append(source)
            valid_extractions.append(extraction)
            continue
        try:
            # Hash-only sources were never retained and do not consume the byte budget.
            limit = (
                min(request.max_file_bytes, MAX_REFRESH_BYTES - retained_bytes)
                if source.raw_retained
                else request.max_file_bytes
            )
            if request.commit:
                from harness.knowledge.commit_sources import read_blob

                raw = read_blob(
                    scope.repository, inventory.observation["git_entries"][source.locator], limit
                )
            else:
                raw = read_source(scope.repository, source.locator, limit)
            retained_bytes += len(raw) if source.raw_retained else 0
            if digest(raw) != source.content_sha256:
                raise ValueError("source changed")
        except (OSError, UnicodeError, ValueError) as error:
            reason = "excluded-sensitive" if isinstance(error, ExcludedSource) else "unstable"
            gap = {
                "locator": source.locator,
                "status": reason,
                "reason": reason,
                "visibility": source.visibility,
            }
            coverage.append(gap)
            unknown.append(gap)
            reused.discard(source.version_id)
            continue
        valid_sources.append(source)
        valid_extractions.append(extraction)
    names = inventory.names if inventory.names is not None else frozenset(names)
    changed = import_changed or any(
        not previous.get(s.source_id)
        or previous[s.source_id]["version_id"] != s.version_id
        or previous[s.source_id]["membership_state"] != "present"
        for s in valid_sources
    )
    deleted = inventory.names_complete and any(
        r["material_class"] in {None, "repository"}
        and r["membership_state"] != "deleted"
        and r["locator"] not in names
        for r in previous.values()
    )
    withheld = any(
        g["reason"] == "excluded-sensitive"
        and any(
            r["locator"] == g["locator"] and r["membership_state"] == "present"
            for r in previous.values()
        )
        for g in unknown
    )
    parent = conn.execute(
        "SELECT observation_json FROM knowledge_snapshots WHERE snapshot_id=?", (expected,)
    ).fetchone()
    mode_changed = parent and json.loads(parent[0]).get(
        "mode", "working-tree"
    ) != inventory.observation.get("mode", "working-tree")
    if expected and not changed and not deleted and not withheld and not mode_changed:
        if unknown:
            return _record_incomplete(conn, scope, coverage)
        if parent and json.loads(parent[0]) == inventory.observation:
            with transaction(conn):
                current = validate_scope(conn, scope)
                if current["active_snapshot_id"] != expected:
                    raise SnapshotConflict("active snapshot changed during refresh")
                record_refresh_attempt(conn, scope.project_id, "unchanged", coverage)
            return RefreshResult(expected, "unchanged", tuple(coverage))
    if not valid_sources and unknown and not deleted and not withheld:
        return _record_incomplete(conn, scope, coverage)
    inventory = replace(inventory, sources=tuple(valid_sources), coverage=tuple(coverage))
    prepared = PreparedSnapshot(
        scope,
        inventory,
        tuple(valid_extractions),
        project["policy_generation"],
        unknown=tuple(unknown),
        names=names,
        names_complete=inventory.names_complete,
        cursor=_cursor(conn, scope, expected, inventory.observation),
        reused=frozenset(reused),
    )
    try:
        prepared = replace(prepared, rows=prepare_rows(prepared))
        snapshot = publish_snapshot(conn, prepared, expected_active=expected)
    except (OSError, ValueError, sqlite3.Error):
        try:
            _record_incomplete(conn, scope, [{"status": "failed", "kind": "publication"}])
        except PermissionError:
            # A concurrent policy revocation must not mask the original CAS failure.
            pass
        raise
    published = conn.execute(
        "SELECT coverage_json FROM knowledge_snapshots WHERE snapshot_id=?", (snapshot,)
    ).fetchone()
    cursor = conn.execute(
        "SELECT unknown_count FROM knowledge_snapshot_cursor WHERE snapshot_id=?", (snapshot,)
    ).fetchone()
    return RefreshResult(
        snapshot,
        "published-partial" if cursor[0] or not inventory.names_complete else "published",
        tuple(json.loads(published[0])),
    )
