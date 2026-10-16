"""`harness review <task>`: the read-only final design review, relayed by Claude Code."""

import argparse
import json

from harness.cli_commands import _controller
from harness.review import FinalReviewer

BLOCKED_EXIT_CODE = 1


def cmd_review(args: argparse.Namespace) -> int:
    with _controller(args) as controller:
        report = FinalReviewer(controller, args.task_id).report()
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True) if args.json else report.render())
    return BLOCKED_EXIT_CODE if report.blocking else 0


def add_review_parser(subparsers: argparse._SubParsersAction) -> None:
    review = subparsers.add_parser(
        "review", help="final design review: SPEC §3 step 6 completeness check (read-only)"
    )
    review.add_argument("task_id")
    review.add_argument("--json", action="store_true", help="print the review as JSON")
    review.set_defaults(handler=cmd_review)
