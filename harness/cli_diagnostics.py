"""Structured diagnostic and exact command approval interface."""

import json
from pathlib import Path

from harness.cli_commands import _controller
from harness.command_runner import approve_command, run_command
from harness.diagnostics import run_diagnostic


def cmd_diagnostics(args):
    request = json.loads(Path(args.file).read_bytes())
    with _controller(args) as controller:
        if args.action == "read":
            result = run_diagnostic(controller, request)
        elif args.action == "approve-command":
            result = {"approval_id": approve_command(controller, request)}
        else:
            result = run_command(controller, request, approval_id=args.approval)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def add_diagnostics_parser(subparsers):
    actions = subparsers.add_parser("diagnostics").add_subparsers(dest="action", required=True)
    for action in ("read", "approve-command", "run-command"):
        parser = actions.add_parser(action)
        parser.add_argument("--file", required=True)
        if action == "run-command":
            parser.add_argument("--approval")
        parser.set_defaults(handler=cmd_diagnostics)
