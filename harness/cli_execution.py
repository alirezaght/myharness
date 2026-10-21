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
        if args.action == "start":
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
        "status",
        "events",
        "logs",
        "reconnect",
        "resume",
        "reconcile",
        "decision",
    ):
        p = actions.add_parser(action)
        p.add_argument("task_id")
        p.set_defaults(handler=cmd_execution)
        if action == "start":
            p.add_argument("--host", choices=["claude", "codex"], default="claude")
            p.add_argument("--specs")
            continue
        p.add_argument("execution_id")
        if action == "events":
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
