import argparse
import json
from dataclasses import asdict
from pathlib import Path

from harness.knowledge.integration import refresh_with_records
from harness.knowledge.models import ContextRequest, RefreshRequest
from harness.knowledge.policy import current_operator_scope
from harness.knowledge.query import explain, query


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
    permitted_visibility = {"project", "task:" + (task_id or "")}
    snapshot_coverage = [
        c for c in snapshot_coverage if c.get("visibility", "project") in permitted_visibility
    ]
    if latest_refresh:
        latest_refresh["coverage"] = [
            c
            for c in latest_refresh["coverage"]
            if c.get("visibility", "project") in permitted_visibility
        ]
    return {
        "status": "indexed" if snapshot else "not-indexed",
        **dict(row),
        **counts,
        "latest_refresh": latest_refresh,
        "snapshot_coverage": snapshot_coverage,
    }


def refresh_knowledge(controller, repository, *, task_id=None):
    return asdict(
        refresh_with_records(
            controller,
            current_operator_scope(controller.conn, repository, task_id=task_id),
            RefreshRequest(),
        )
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
    for name in ("inspect", "refresh", "query", "explain"):
        action = actions.add_parser(name)
        action.add_argument(
            "task_id", help="Harness task whose investigated repository is selected"
        )
        if name == "query":
            action.add_argument("text", nargs="*", help="search the current knowledge snapshot")
        elif name == "explain":
            action.add_argument("bundle_hash")
            action.add_argument("assertion_id")
        action.set_defaults(handler=cmd_knowledge)


def cmd_knowledge(args: argparse.Namespace) -> int:
    from harness.cli_commands import _controller

    with _controller(args) as controller:
        repository = repository_for_task(controller, args.task_id)
        if args.knowledge_action == "inspect":
            result = inspect_knowledge(controller, repository, task_id=args.task_id)
        elif args.knowledge_action == "refresh":
            result = refresh_knowledge(controller, repository, task_id=args.task_id)
        elif args.knowledge_action == "query":
            result = query_knowledge(
                controller, repository, " ".join(args.text), task_id=args.task_id
            )
        else:
            result = explain_knowledge(
                controller, repository, args.bundle_hash, args.assertion_id, task_id=args.task_id
            )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0
