"""Explicit separate browser approval and execution commands; read-only reporting."""

import json
import sys
from pathlib import Path

from harness.browser_capability import probe_browser
from harness.browser_plan import propose_browser
from harness.browser_store import browser_view
from harness.browser_validation import approve_browser, reconcile_browser, run_browser
from harness.cli_commands import _controller
from harness.execution_contract import loads
from harness.execution_logs import redact, redact_value
from harness.execution_store import get_execution
from harness.reporting import completion_report


def cmd_browser(args):
    try:
        with _controller(args) as controller:
            if get_execution(controller.conn, args.execution_id)["task_id"] != args.task_id:
                raise ValueError("execution belongs to another task")
            if args.command == "report":
                value = completion_report(controller.conn, args.execution_id)
            elif args.action == "capability":
                value = probe_browser(controller, args.execution_id)
            elif args.action == "propose":
                mapping = None
                if args.file:
                    with Path(args.file).open("rb") as stream:
                        mapping = loads(stream.read(262145))
                value = propose_browser(controller, args.execution_id, mapping)
            elif args.action == "approve":
                value = approve_browser(
                    controller,
                    args.execution_id,
                    args.plan,
                    request_id=args.request_id,
                    attempt=args.attempt,
                )
            elif args.action == "run":
                value = run_browser(controller, args.execution_id, args.approval)
            elif args.action == "reconcile":
                value = reconcile_browser(controller, args.execution_id, args.run)
            else:
                value = browser_view(controller.conn, args.execution_id)
        print(json.dumps(redact_value(value), indent=2, sort_keys=True))
        return 0
    except (ValueError, OSError, LookupError) as error:
        print("harness browser: " + redact(str(error)), file=sys.stderr)
        return 1


def cmd_report(args):
    return cmd_browser(args)


def add_browser_parser(subparsers):
    actions = subparsers.add_parser("browser").add_subparsers(dest="action", required=True)
    for name in ("capability", "propose", "approve", "run", "status", "reconcile"):
        parser = actions.add_parser(name)
        parser.add_argument("task_id")
        parser.add_argument("execution_id")
        if name == "propose":
            parser.add_argument("--file")
        elif name == "approve":
            parser.add_argument("--plan", required=True)
            parser.add_argument("--request-id", required=True)
            parser.add_argument("--attempt", required=True, type=int)
        elif name == "run":
            parser.add_argument("--approval", required=True)
        elif name == "reconcile":
            parser.add_argument("--run", required=True)
        parser.set_defaults(handler=cmd_browser)
    report = subparsers.add_parser("report")
    report.add_argument("task_id")
    report.add_argument("execution_id")
    report.set_defaults(handler=cmd_report)
