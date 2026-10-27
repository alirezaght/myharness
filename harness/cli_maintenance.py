"""Explicit context/review/dependency maintenance with bounded, auditable inputs."""

import json
from pathlib import Path

from harness.cli_commands import _controller, task_repo_root
from harness.execution_contract import loads


def cmd_maintenance(args):
    with _controller(args) as controller:
        root = (
            Path(args.repo)
            if getattr(args, "repo", None)
            else task_repo_root(controller, args.task_id)
        )
        if args.command == "context":
            from harness.context_import import import_context

            value = loads(Path(args.file).read_bytes(), max_bytes=4 * 1024 * 1024)
            result = import_context(controller, args.task_id, value)
        elif args.command == "review-package":
            from harness.review_package import accept, package

            if args.action == "export":
                result = package(controller, args.task_id)
            else:
                from harness.cli_decision import registry_for

                result = accept(
                    controller,
                    args.task_id,
                    args.sha256,
                    source=args.source,
                    registry=registry_for(controller, args.task_id, None),
                )
        else:
            from harness.dependency_layout import inspect, reconcile

            if args.action == "inspect":
                scopes = args.scopes.split(",") if args.scopes else None
                result = inspect(root, scopes)
                from harness.execution_contract import digest

                result = {"sha256": digest(result), "plan": result}
            else:
                from harness.execution_store import process_alive

                attempts = controller.conn.execute(
                    "SELECT child_pid,status FROM execution_attempts WHERE execution_id IN "
                    "(SELECT id FROM executions WHERE task_id=?)",
                    (args.task_id,),
                ).fetchall()
                if any(
                    row["status"] not in {"Exited", "Reconciled", "PreSpawnFailed"}
                    or process_alive(row["child_pid"])
                    for row in attempts
                ):
                    raise ValueError(
                        "dependency reconciliation requires settled execution attempts"
                    )
                value = loads(Path(args.file).read_bytes(), max_bytes=4 * 1024 * 1024)
                plan = value.get("plan", value)
                if Path(plan["repository"]).resolve() != root.resolve():
                    raise ValueError("dependency plan belongs to another repository")
                result = reconcile(plan, args.sha256)
                controller.workspace(args.task_id).record_event("dependencies.reconciled", result)
    raw = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if getattr(args, "output", None):
        Path(args.output).write_text(raw)
    print(raw, end="")
    return 0


def register(subparsers):
    for command, actions in [
        ("context", ["import"]),
        ("review-package", ["export", "accept"]),
        ("dependencies", ["inspect", "reconcile"]),
    ]:
        children = subparsers.add_parser(
            command, help="source-bound " + command + " workflow"
        ).add_subparsers(dest="action", required=True)
        for action in actions:
            p = children.add_parser(action)
            p.add_argument("task_id")
            p.set_defaults(handler=cmd_maintenance)
            if command == "context" or action == "reconcile":
                p.add_argument("--file", required=True)
            if action in {"reconcile", "accept"}:
                p.add_argument("--sha256", required=True)
            if action == "accept":
                p.add_argument("--source", required=True, help="explicit user approval provenance")
            if command == "dependencies":
                p.add_argument("--repo")
            if action == "inspect":
                p.add_argument("--scopes", help="comma-separated ignored dependency paths")
            if action in {"export", "inspect"}:
                p.add_argument("--output")
