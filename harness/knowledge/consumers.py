"""Who consumes a module, file or endpoint: lazy resolution over the published import edges.

Extraction records import specifiers exactly as written. Resolving them needs the repository
layout, so ``harness.code_resolution.Layout`` does it here, only for the target someone asks
about, against the snapshot's own locators. Results are evidence-linked observations: an
unresolvable specifier is reported, never guessed into a block.
"""

import json
import posixpath
import re
from collections import defaultdict
from pathlib import PurePosixPath

from harness.code_resolution import (
    CODE_SUFFIXES,
    SCRIPT_EXTENSIONS,
    Layout,
    segments_match,
    strip_ext,
)
from harness.knowledge.models import MAX_SOURCE_BYTES, identity
from harness.knowledge.policy import validate_scope
from harness.knowledge.sources import read_source

DEFAULT_LIMIT = 200


def _snapshot(conn, scope, snapshot_id):
    from harness.knowledge.query import _partition

    project = validate_scope(conn, scope)
    snapshot = snapshot_id or project["active_snapshot_id"]
    if not snapshot:
        return None
    _partition(conn, scope, snapshot)
    return snapshot


_EDGE_ROWS = (
    "SELECT a.assertion_id,a.predicate,o.scoped_key AS object,a.value_json,s.locator,"
    "e.start_byte,e.end_byte FROM knowledge_assertions a "
    "JOIN temp.k_eligible k USING(assertion_id) "
    "JOIN knowledge_entities o ON o.entity_id=a.object_id "
    "JOIN knowledge_evidence e ON e.assertion_id=a.assertion_id "
    "JOIN temp.k_versions kv ON kv.version_id=e.version_id "
    "JOIN knowledge_source_versions v ON v.version_id=e.version_id "
    "JOIN knowledge_sources s ON s.source_id=v.source_id "
)


def _edges(conn, project, predicate, objects):
    rows = []
    objects = sorted(set(objects))
    for index in range(0, len(objects), 500):
        chunk = [identity(project, key) for key in objects[index : index + 500]]
        rows.extend(
            conn.execute(
                _EDGE_ROWS
                + "WHERE a.project_id=? AND a.predicate=? AND a.object_id IN ("
                + ",".join("?" * len(chunk))
                + ")",
                (project, predicate, *chunk),
            ).fetchall()
        )
    return rows


def resolve_target(layout, target):
    """Map a locator, dotted module, directory or endpoint to locators and endpoints."""
    target = target.strip()
    if target.startswith("endpoint:") or (target.startswith("/") and target not in layout.locators):
        from harness.code_resolution import endpoint_path

        path = endpoint_path(target.removeprefix("endpoint:"))
        return [], [path] if path else []
    target = target.removeprefix("file:").removeprefix("module:").strip("/")
    if target in layout.locators:
        return [target], []
    if target in layout.directories:
        prefix = target + "/"
        return sorted(
            n
            for n in layout.locators
            if n.startswith(prefix) and PurePosixPath(n).suffix in CODE_SUFFIXES
        ), []
    stems = [n for n in layout.locators if n.endswith(".py") or n.endswith(SCRIPT_EXTENSIONS)]
    matches = sorted(n for n in stems if strip_ext(n) == target)
    if matches:
        return matches, []
    if re.fullmatch(r"[A-Za-z_][\w.]*", target):
        return sorted(
            n
            for n in stems
            if n.endswith(".py") and any(name == target for _, name in layout.python_names(n))
        ), []
    return [], []


def consumers(conn, scope, target, *, snapshot_id=None, limit=DEFAULT_LIMIT):
    """Static importers and runtime (HTTP endpoint) callers of `target` in the snapshot."""
    snapshot = _snapshot(conn, scope, snapshot_id)
    if not snapshot:
        return {"target": target, "status": "not-indexed", "consumers": [], "gaps": []}
    project = scope.project_id
    locators = [
        r[0]
        for r in conn.execute(
            "SELECT s.locator FROM temp.k_versions k JOIN knowledge_source_versions v "
            "USING(version_id) JOIN knowledge_sources s USING(source_id)"
        )
    ]
    layout = Layout(
        locators,
        lambda locator: read_source(scope.repository, locator, MAX_SOURCE_BYTES).decode(),
    )
    files, endpoints = resolve_target(layout, target)
    found, gaps = {}, []

    def add(row, relation, via, owner):
        importer = row["locator"]
        if importer in files:
            return
        key = (importer, relation, via)
        if key in found:
            return
        found[key] = {
            "locator": importer,
            "unit": layout.unit(importer) or ".",
            "relation": relation,
            "via": via,
            "scope": (
                "runtime"
                if not owner
                else "intra-unit"
                if layout.unit(importer) == layout.unit(owner)
                else "cross-unit"
            ),
            "target": owner,
            "assertion_id": row["assertion_id"],
            "start_byte": row["start_byte"],
            "end_byte": row["end_byte"],
        }

    for locator in files:
        if locator.endswith(".py"):
            names = layout.python_names(locator)
            wanted = defaultdict(list)
            for root, name in names:
                wanted[f"module:{name}"].append((root, name, None))
                if "." in name:
                    parent, leaf = name.rsplit(".", 1)
                    wanted[f"module:{parent}"].append((root, name, leaf))
            for row in _edges(conn, project, "imports", wanted):
                value = json.loads(row["value_json"]) if row["value_json"] else None
                imported = set((value or {}).get("names", [])) if isinstance(value, dict) else set()
                for root, name, leaf in wanted[row["object"]]:
                    if leaf is not None and leaf not in imported:
                        continue
                    owner = layout.python_owner(row["locator"], name)
                    if owner is not None and owner != root:
                        continue  # the importer's own layout resolves this name elsewhere
                    add(row, "imports", name, locator)
                    break
        elif locator.endswith(SCRIPT_EXTENSIONS):
            stem = strip_ext(locator)
            relative = {f"path:{stem}", f"path:{locator}"}
            relative.update(f"path:{stem}{extension}" for extension in SCRIPT_EXTENSIONS)
            if posixpath.basename(stem) == "index":
                relative.add(f"path:{posixpath.dirname(stem)}")
            for row in _edges(conn, project, "imports", relative):
                add(row, "imports", row["object"].removeprefix("path:"), locator)
            specifiers = layout.script_specifiers(locator)
            scopes = defaultdict(set)
            for spec, spec_scope in specifiers:
                scopes[f"import:{spec}"].add(spec_scope)
            for row in _edges(conn, project, "imports", scopes):
                allowed = scopes[row["object"]]
                if "" in allowed or any(row["locator"].startswith(s + "/") for s in allowed):
                    add(row, "imports", row["object"].removeprefix("import:"), locator)
        subject = (
            "module:"
            + ".".join(PurePosixPath(locator).with_suffix("").parts).removesuffix(".__init__")
            if locator.endswith(".py")
            else f"file:{locator}"
        )
        served = conn.execute(
            _EDGE_ROWS
            + "WHERE a.project_id=? AND a.subject_id=? AND a.predicate='serves-endpoint' "
            "AND s.locator=?",
            (project, identity(project, subject), locator),
        ).fetchall()
        endpoints.extend((row["object"].removeprefix("endpoint:"), locator) for row in served)
    if endpoints:
        calls = conn.execute(
            _EDGE_ROWS + "WHERE a.project_id=? AND a.predicate='calls-endpoint'", (project,)
        ).fetchall()
        for item in endpoints:
            path, owner = item if isinstance(item, tuple) else (item, "")
            for row in calls:
                called = row["object"].removeprefix("endpoint:")
                if called == path or segments_match(called, path):
                    add(row, "calls-endpoint", path, owner)
    if layout.unreadable:
        gaps.append(
            {
                "status": "unresolved",
                "reason": "layout-config-unreadable",
                "locators": sorted(layout.unreadable)[:20],
            }
        )
    status = _coverage_status(conn, snapshot)
    results = sorted(found.values(), key=lambda c: (c["scope"] != "cross-unit", c["locator"]))
    return {
        "target": target,
        "snapshot_id": snapshot,
        "status": status,
        "resolved": {
            "files": files[:50],
            "endpoints": sorted({e if isinstance(e, str) else e[0] for e in endpoints}),
        },
        "consumers": results[:limit],
        "truncated": len(results) > limit,
        "gaps": gaps,
    }


def _coverage_status(conn, snapshot):
    row = conn.execute(
        "SELECT unknown_count,names_complete FROM knowledge_snapshot_cursor WHERE snapshot_id=?",
        (snapshot,),
    ).fetchone()
    return "complete" if row and not row[0] and row[1] else "partial"


def coverage_summary(conn, scope):
    """One-line trust signal: how much of the eligible inventory the active snapshot covers."""
    project = conn.execute(
        "SELECT active_snapshot_id FROM knowledge_projects WHERE project_id=?", (scope.project_id,)
    ).fetchone()
    if not project or not project[0]:
        return {"status": "not-indexed"}
    rows = conn.execute(
        "SELECT s.locator,m.membership_state FROM knowledge_snapshot_sources m "
        "JOIN knowledge_sources s USING(source_id) WHERE m.snapshot_id=? "
        "AND s.locator NOT LIKE 'harness:%'",
        (project[0],),
    ).fetchall()
    present = [r[0] for r in rows if r[1] == "present"]
    known = [r[0] for r in rows if r[1] in {"present", "unknown"}]
    languages = sorted(
        {
            "python" if n.endswith(".py") else "typescript/javascript"
            for n in present
            if n.endswith(".py") or n.endswith(SCRIPT_EXTENSIONS)
        }
    )
    percent = round(100 * len(present) / len(known)) if known else 100
    status = _coverage_status(conn, project[0])
    return {
        "status": status,
        "snapshot_id": project[0],
        "indexed_files": len(present),
        "known_files": len(known),
        "percent": percent,
        "languages": languages,
        "line": f"knowledge graph: {status}, {percent}% of {len(known)} files, "
        + (", ".join(languages) or "no code")
        + " import/endpoint edges",
    }
