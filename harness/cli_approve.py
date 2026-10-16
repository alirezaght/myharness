"""`harness approve <task>`: Approve & Implement — seal the spec and record the approval."""

import argparse

from harness.approval import ApprovalGate
from harness.cli_commands import _controller, task_repo_root
from harness.cli_spec import approved_line, spec_store


def cmd_approve(args: argparse.Namespace) -> int:
    with _controller(args) as controller:
        store = spec_store(controller, args.task_id, args.specs)
        root = task_repo_root(controller, args.task_id)
        approval, record, previous = ApprovalGate(controller, args.task_id, store, root).approve()
    print(approved_line(record))
    if previous is not None:
        print(f"v{previous.version} superseded")
    print(
        f"approval #{approval.id} recorded: spec v{approval.spec_version} {approval.content_hash}"
        " (implementation handoff authorized; AL launch is Phase 3)"
    )
    return 0


def add_approve_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "approve",
        help="Approve & Implement: seal the spec snapshot and record the approval that"
        " authorizes the handoff to AL (approval phase only)",
    )
    parser.add_argument("task_id")
    parser.add_argument("--specs", metavar="PATH", help="specs root (default: <repo>/docs/specs)")
    parser.set_defaults(handler=cmd_approve)
