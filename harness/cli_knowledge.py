import argparse
import json
from dataclasses import asdict
from pathlib import Path

from harness.knowledge.integration import refresh_until_settled
from harness.knowledge.models import ContextRequest, RefreshRequest
from harness.knowledge.policy import current_operator_scope
from harness.knowledge.query import explain, query, visible_coverage


def repository_for_task(controller, task_id):
    from harness.cli_commands import task_repo_root

    controller.get_task(task_id)
    return Path(task_repo_root(controller, task_id)).resolve()


def inspect_knowledge(controller, repository, *, task_id=None):
    scope = current_operator_scope(controller.conn, repository, task_id=task_id)
    row = controller.conn.execute(
        "SELECT * FROM knowledge_projects WHERE project_id=?", (scope.project_id,)
    ).fetchone()
    if not row:
        return {
            "status": "not-indexed",
            "active_snapshot_id": None,
            "latest_refresh": None,
            "snapshot_coverage": [],
            "source_count": 0,
            "entity_count": 0,
            "relationship_count": 0,
        }
    snapshot = row["active_snapshot_id"]
    latest = controller.conn.execute(
        "SELECT status,coverage_json,created_at FROM knowledge_refresh_attempts "
        "WHERE project_id=? ORDER BY rowid DESC LIMIT 1",
        (scope.project_id,),
    ).fetchone()
    latest_refresh = (
        {
            "status": latest["status"],
            "coverage": json.loads(latest["coverage_json"]),
            "created_at": latest["created_at"],
        }
        if latest
        else None
    )
    snapshot_coverage = []
    counts = {"source_count": 0, "entity_count": 0, "relationship_count": 0}
    if snapshot:
        from harness.knowledge.query import _partition

        _partition(controller.conn, scope, snapshot)
        counts["source_count"] = controller.conn.execute(
            "SELECT count(*) FROM temp.k_versions"
        ).fetchone()[0]
        counts["entity_count"] = controller.conn.execute(
            "SELECT count(*) FROM (SELECT a.subject_id FROM knowledge_assertions a JOIN "
            "temp.k_eligible e USING(assertion_id) "
            "UNION SELECT a.object_id FROM knowledge_assertions a JOIN temp.k_eligible e "
            "USING(assertion_id) WHERE a.object_id IS NOT NULL)"
        ).fetchone()[0]
        counts["relationship_count"] = controller.conn.execute(
            "SELECT count(*) FROM temp.k_eligible"
        ).fetchone()[0]
        snapshot_coverage = json.loads(
            controller.conn.execute(
                "SELECT coverage_json FROM knowledge_snapshots WHERE snapshot_id=?", (snapshot,)
            ).fetchone()[0]
        )
    snapshot_coverage = visible_coverage(controller.conn, scope, snapshot_coverage, snapshot)
    if latest_refresh:
        latest_refresh["coverage"] = visible_coverage(
            controller.conn, scope, latest_refresh["coverage"], snapshot
        )
    return {
        "status": "indexed" if snapshot else "not-indexed",
        **dict(row),
        **counts,
        "latest_refresh": latest_refresh,
        "snapshot_coverage": snapshot_coverage,
    }


def refresh_knowledge(
    controller, repository, *, task_id=None, commit=None, included_paths=(), rounds=None
):
    """Explicit indexing opts the repository in; it settles incremental batches by default."""
    from harness.knowledge.consumers import coverage_summary

    scope = current_operator_scope(controller.conn, repository, task_id=task_id)
    result = asdict(
        refresh_until_settled(
            controller,
            scope,
            RefreshRequest(included_paths=tuple(sorted(set(included_paths))), commit=commit),
            rounds=rounds,
        )
    )
    result["summary"] = coverage_summary(controller.conn, scope)
    return result


def consumers_knowledge(
    controller, repository, target, *, task_id=None, limit=200, al_session=None, al_platform=None
):
    from harness.knowledge.al_facts import resolve
    from harness.knowledge.consumers import consumers

    return consumers(
        controller.conn,
        current_operator_scope(controller.conn, repository, task_id=task_id),
        target,
        limit=limit,
        facts=resolve(repository, platform=al_platform, session=al_session),
    )


def edges_knowledge(
    controller, repository, paths=(), *, task_id=None, al_session=None, al_platform=None
):
    from harness.knowledge.al_facts import resolve
    from harness.knowledge.consumers import edges

    return edges(
        controller.conn,
        current_operator_scope(controller.conn, repository, task_id=task_id),
        tuple(paths),
        facts=resolve(repository, platform=al_platform, session=al_session),
    )


def query_knowledge(
    controller, repository, text="", *, entity_refs=(), snapshot_id=None, task_id=None
):
    return query(
        controller.conn,
        current_operator_scope(controller.conn, repository, task_id=task_id),
        ContextRequest(text, tuple(entity_refs), snapshot_id),
    ).to_dict()


def explain_knowledge(controller, repository, bundle_hash, assertion_id, *, task_id=None):
    return explain(
        controller.conn,
        current_operator_scope(controller.conn, repository, task_id=task_id),
        bundle_hash,
        assertion_id,
    )


def evidence_knowledge(controller, repository, bundle_hash, assertion_id, *, task_id=None):
    """Use the service's kind-aware, authorized resolver for bounded cited excerpts."""
    scope = current_operator_scope(controller.conn, repository, task_id=task_id)
    explanation = explain(controller.conn, scope, bundle_hash, assertion_id, include_evidence=True)
    keys = ("locator", "start_byte", "end_byte", "text", "truncated")
    return {
        "authority": explanation["authority"],
        "citations": [{k: ref[k] for k in keys} for ref in explanation["citations"]],
    }


def add_knowledge_parser(subparsers):
    root = subparsers.add_parser("knowledge", help="inspect and query the sourced project graph")
    actions = root.add_subparsers(dest="knowledge_action", required=True)
    for name in (
        "inspect",
        "refresh",
        "query",
        "explain",
        "export",
        "grant",
        "import",
        "revoke",
        "purge",
        "retain",
        "consumers",
        "edges",
    ):
        action = actions.add_parser(name, allow_abbrev=False)
        action.add_argument(
            "task_id", help="Harness task whose investigated repository is selected"
        )
        if name == "query":
            action.add_argument("text", nargs="*", help="search the selected knowledge snapshot")
            action.add_argument("--entity", action="append", default=[])
            action.add_argument("--as-of", dest="snapshot_id")
        elif name == "refresh":
            action.add_argument("--commit")
            action.add_argument("--include", action="append", default=[])
            action.add_argument(
                "--rounds",
                type=int,
                default=None,
                help="incremental publication batches to run (default: until settled)",
            )
        elif name == "consumers":
            action.add_argument(
                "target",
                help="repository path, directory, dotted Python module or HTTP endpoint path",
            )
            action.add_argument("--limit", type=int, default=200)
        elif name == "edges":
            action.add_argument("paths", nargs="*", help="files or directories (all code if none)")
        if name in {"consumers", "edges"}:
            # Import edges are AL's code facts (#34): name the AL run to ask, or run inside a
            # Claude conversation with an active AL run; otherwise the answer is unavailable.
            action.add_argument("--al-session", help="AL run id whose launcher answers")
            action.add_argument("--al-platform", choices=("claude", "codex"), default=None)
        if name == "explain":
            action.add_argument("bundle_hash")
            action.add_argument("assertion_id")
        elif name == "export":
            action.add_argument(
                "--body-file", required=True, help="repository-relative exact portable Markdown"
            )
            action.add_argument(
                "--decision", required=True, help="current repository-relative approval record"
            )
        elif name in {"grant", "revoke"}:
            action.add_argument("--destination-db", required=True, type=Path)
            action.add_argument("--destination-task", required=True)
            action.add_argument(
                "--grant-id", required=True, help="explicit action identity; reuse for retries"
            )
            if name == "grant":
                action.add_argument("--lesson", required=True)
                action.add_argument("--version", type=int, required=True)
        elif name == "import":
            action.add_argument("locator", help="tracked docs/knowledge/imports/ portable Markdown")
        elif name == "purge":
            action.add_argument("--version", action="append", required=True, dest="version_ids")
        elif name == "retain":
            action.add_argument("--limit", type=int, default=500)
        action.set_defaults(handler=cmd_knowledge)


def _mutation(controller, repository, args):
    from harness.knowledge.lessons import export_lesson, grant, import_lesson, revoke
    from harness.knowledge.retention import purge, retain
    from harness.knowledge.sources import read_source

    scope = current_operator_scope(controller.conn, repository, task_id=args.task_id)
    name = args.knowledge_action
    if name == "export":
        from harness.knowledge.models import MAX_SOURCE_BYTES

        return export_lesson(
            controller.conn,
            scope,
            read_source(repository, args.body_file, MAX_SOURCE_BYTES),
            args.decision,
        )
    if name == "import":
        return import_lesson(controller.conn, scope, args.locator)
    if name == "purge":
        return purge(controller.conn, scope, args.version_ids)
    if name == "retain":
        return retain(controller.conn, scope, limit=args.limit)
    from harness.controller import HarnessController
    from harness.db import connect

    # Both databases are explicitly selected local existing files; never scan neighbors.
    destination = args.destination_db.expanduser()
    if not destination.is_file() or destination.is_symlink():
        raise ValueError("explicit existing local destination database required")
    conn = connect(destination)
    try:
        other = HarnessController(conn)
        root = repository_for_task(other, args.destination_task)
        target = current_operator_scope(conn, root, task_id=args.destination_task)
        if name == "grant":
            return grant(
                controller.conn,
                scope,
                conn,
                target,
                args.lesson,
                args.version,
                grant_id=args.grant_id,
            )
        return revoke(controller.conn, scope, conn, target, args.grant_id)
    finally:
        conn.close()


def cmd_knowledge(args: argparse.Namespace) -> int:
    from harness.cli_commands import _controller

    with _controller(args) as controller:
        repository = repository_for_task(controller, args.task_id)
        if args.knowledge_action == "inspect":
            result = inspect_knowledge(controller, repository, task_id=args.task_id)
        elif args.knowledge_action == "refresh":
            result = refresh_knowledge(
                controller,
                repository,
                task_id=args.task_id,
                commit=args.commit,
                included_paths=args.include,
                rounds=args.rounds,
            )
        elif args.knowledge_action == "consumers":
            result = consumers_knowledge(
                controller,
                repository,
                args.target,
                task_id=args.task_id,
                limit=args.limit,
                al_session=args.al_session,
                al_platform=args.al_platform,
            )
        elif args.knowledge_action == "edges":
            result = edges_knowledge(
                controller,
                repository,
                args.paths,
                task_id=args.task_id,
                al_session=args.al_session,
                al_platform=args.al_platform,
            )
        elif args.knowledge_action == "query":
            result = query_knowledge(
                controller,
                repository,
                " ".join(args.text),
                task_id=args.task_id,
                entity_refs=args.entity,
                snapshot_id=args.snapshot_id,
            )
        elif args.knowledge_action == "explain":
            result = explain_knowledge(
                controller, repository, args.bundle_hash, args.assertion_id, task_id=args.task_id
            )
        else:
            result = _mutation(controller, repository, args)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return (
        1
        if result.get("status")
        in {"source-only", "revocation-not-enforced", "revoked-destination-only"}
        else 0
    )
