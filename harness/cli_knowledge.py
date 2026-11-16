import argparse
import json
from dataclasses import asdict
from pathlib import Path

from harness.knowledge.models import MAX_SOURCE_BYTES, ContextRequest, RefreshRequest, digest
from harness.knowledge.policy import operator_scope
from harness.knowledge.query import explain, query
from harness.knowledge.refresh import refresh


def repository_for_task(controller, task_id):
    from harness.cli_commands import task_repo_root

    controller.get_task(task_id)
    return Path(task_repo_root(controller, task_id)).resolve()


def inspect_knowledge(controller, repository):
    scope = operator_scope(repository)
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
        counts["source_count"] = controller.conn.execute(
            "SELECT COUNT(*) FROM knowledge_snapshot_sources WHERE snapshot_id=? "
            "AND membership_state='present'",
            (snapshot,),
        ).fetchone()[0]
        counts["entity_count"] = controller.conn.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT a.subject_id AS entity_id FROM knowledge_assertions a "
            "JOIN knowledge_snapshot_assertions m ON m.assertion_id=a.assertion_id "
            "WHERE m.snapshot_id=? UNION "
            "SELECT a.object_id AS entity_id FROM knowledge_assertions a "
            "JOIN knowledge_snapshot_assertions m ON m.assertion_id=a.assertion_id "
            "WHERE m.snapshot_id=? AND a.object_id IS NOT NULL)",
            (snapshot, snapshot),
        ).fetchone()[0]
        counts["relationship_count"] = controller.conn.execute(
            "SELECT COUNT(*) FROM knowledge_snapshot_assertions WHERE snapshot_id=?", (snapshot,)
        ).fetchone()[0]
        snapshot_coverage = json.loads(
            controller.conn.execute(
                "SELECT coverage_json FROM knowledge_snapshots WHERE snapshot_id=?",
                (snapshot,),
            ).fetchone()[0]
        )
    return {
        "status": "indexed" if snapshot else "not-indexed",
        **dict(row),
        **counts,
        "latest_refresh": latest_refresh,
        "snapshot_coverage": snapshot_coverage,
    }


def refresh_knowledge(controller, repository, *, task_id=None):
    return asdict(
        refresh(controller.conn, operator_scope(repository, task_id=task_id), RefreshRequest())
    )


def query_knowledge(controller, repository, text="", *, entity_refs=(), snapshot_id=None):
    return query(
        controller.conn,
        operator_scope(repository),
        ContextRequest(text, tuple(entity_refs), snapshot_id),
    ).to_dict()


def explain_knowledge(controller, repository, bundle_hash, assertion_id):
    return explain(controller.conn, operator_scope(repository), bundle_hash, assertion_id)


def evidence_knowledge(controller, repository, bundle_hash, assertion_id):
    """Return a bounded, current cited span after the service revalidates its bundle."""
    from harness.knowledge.sources import read_source

    scope = operator_scope(repository)
    explanation = explain(controller.conn, scope, bundle_hash, assertion_id)
    spans = []
    for citation in explanation["citations"]:
        raw = read_source(scope.repository, citation["locator"], MAX_SOURCE_BYTES)
        start, end = citation["start_byte"], citation["end_byte"]
        excerpt = raw[start:end]
        if digest(raw) != citation["content_sha256"] or digest(excerpt) != citation["span_sha256"]:
            raise ValueError("bundle evidence changed during source retrieval")
        spans.append(
            {
                "locator": citation["locator"],
                "start_byte": start,
                "end_byte": end,
                "text": excerpt[:4096].decode("utf-8", errors="replace"),
                "truncated": len(excerpt) > 4096,
            }
        )
    return {"authority": explanation["authority"], "citations": spans}


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
            result = inspect_knowledge(controller, repository)
        elif args.knowledge_action == "refresh":
            result = refresh_knowledge(controller, repository, task_id=args.task_id)
        elif args.knowledge_action == "query":
            result = query_knowledge(controller, repository, " ".join(args.text))
        else:
            result = explain_knowledge(controller, repository, args.bundle_hash, args.assertion_id)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0
