"""Explicit execution operations; approval sealing remains a separate command."""

import json

from harness.cli_commands import _controller
from harness.execution import start_execution
from harness.execution_decisions import record_execution_decision
from harness.execution_logs import read_log
from harness.execution_projection import event_page, execution_view
from harness.execution_recovery import reconcile_execution, reconnect_execution, resume_execution
from harness.execution_store import get_execution


def cmd_execution(args):
    with _controller(args) as controller:
        if args.action == "readiness":
            from harness.cli_commands import task_repo_root
            from harness.launch_readiness import readiness

            result = readiness(args.repo or task_repo_root(controller, args.task_id), args.host)
        elif args.action == "start":
            result = start_execution(controller, args.task_id, host=args.host, specs=args.specs)
        else:
            execution = get_execution(controller.conn, args.execution_id)
            if execution["task_id"] != args.task_id:
                raise ValueError("execution belongs to a different harness task")
            if args.action == "status":
                result = execution_view(controller.conn, args.execution_id)
            elif args.action == "events":
                result = event_page(controller.conn, args.execution_id, args.after, args.limit)
            elif args.action == "logs":
                result = read_log(controller.conn, args.execution_id, args.log_id)
            elif args.action == "upgrade-bridge":
                from harness.execution_bridge import upgrade_bridge

                result = upgrade_bridge(controller, args.execution_id, reason=args.reason)
            elif args.action == "adopt-planning":
                from pathlib import Path

                from harness.execution_adoption import adopt_planning
                from harness.execution_contract import loads

                request = loads(Path(args.file).read_bytes(), max_bytes=4 * 1024 * 1024)
                result = adopt_planning(
                    controller,
                    args.execution_id,
                    request,
                    args.sha256,
                    approval_source=args.source,
                )
            elif args.action == "reconnect":
                result = reconnect_execution(controller, args.execution_id)
            elif args.action == "resume":
                result = resume_execution(controller, args.execution_id)
            elif args.action == "reconcile":
                result = reconcile_execution(
                    controller,
                    args.execution_id,
                    outcome=args.outcome,
                    evidence=args.evidence,
                    native_id=args.native_id,
                )
            elif args.action == "decision":
                result = record_execution_decision(
                    controller,
                    args.execution_id,
                    args.blocker_id,
                    blocker_revision=args.revision,
                    request_id=args.request_id,
                    verdict=args.verdict,
                    decision_text=args.text,
                    tasks=args.tasks.split(","),
                    criteria=args.criteria.split(","),
                    amendment=args.amendment,
                )
            if args.action == "decision" and result["state"] == "Delivered":
                reconnect_execution(controller, args.execution_id)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def add_execution_parser(subparsers):
    actions = subparsers.add_parser(
        "execution", help="start and monitor an approval-bound AL execution"
    ).add_subparsers(dest="action", required=True)
    for action in (
        "start",
        "readiness",
        "status",
        "events",
        "logs",
        "reconnect",
        "resume",
        "reconcile",
        "decision",
        "upgrade-bridge",
        "adopt-planning",
    ):
        p = actions.add_parser(action)
        p.add_argument("task_id")
        p.set_defaults(handler=cmd_execution)
        if action == "readiness":
            p.add_argument("--host", choices=["claude", "codex"], default="claude")
            p.add_argument("--repo")
            continue
        if action == "start":
            p.add_argument("--host", choices=["claude", "codex"], default="claude")
            p.add_argument("--specs")
            continue
        p.add_argument("execution_id")
        if action == "upgrade-bridge":
            p.add_argument("--reason", required=True)
        elif action == "adopt-planning":
            p.add_argument("--file", required=True)
            p.add_argument("--sha256", required=True)
            p.add_argument("--source", required=True, help="explicit user approval provenance")
        elif action == "events":
            p.add_argument("--after", type=int, default=0)
            p.add_argument("--limit", type=int, default=100)
        elif action == "logs":
            p.add_argument("log_id")
        elif action == "reconcile":
            p.add_argument("--outcome", choices=["not-started", "exited"], required=True)
            p.add_argument("--evidence", required=True)
            p.add_argument("--native-id")
        elif action == "decision":
            p.add_argument("blocker_id")
            p.add_argument("--revision", required=True, type=int)
            p.add_argument("--request-id", required=True)
            p.add_argument(
                "--verdict", choices=["accept", "reject", "modify", "alternative"], required=True
            )
            p.add_argument("--text", required=True)
            p.add_argument("--tasks", required=True)
            p.add_argument("--criteria", required=True)
            p.add_argument("--amendment", action="store_true")
