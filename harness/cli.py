"""Command-line entry point for the harness. `harness start` is the only way to create a task."""

import argparse
import sys

from harness import __version__
from harness.classification import Complexity
from harness.cli_approve import add_approve_parser
from harness.cli_browser import add_browser_parser
from harness.cli_commands import cmd_classify, cmd_investigate, cmd_phase, cmd_show, cmd_start
from harness.cli_dashboard import add_dashboard_parser
from harness.cli_decision import add_decision_parser
from harness.cli_delivery import add_delivery_parser
from harness.cli_design import add_design_parser
from harness.cli_diagnostics import add_diagnostics_parser
from harness.cli_execution import add_execution_parser
from harness.cli_interview import add_interview_parser
from harness.cli_knowledge import add_knowledge_parser
from harness.cli_resume import add_resume_parser
from harness.cli_review import add_review_parser
from harness.cli_spec import add_spec_parser
from harness.cli_storage import add_storage_parser
from harness.cli_testplan import add_testplan_parser
from harness.controller import TaskNotFound
from harness.errors import RecordNotFound
from harness.phases import InvalidTransition, Phase

ERROR_EXIT_CODE = 1
LEVELS = [str(level) for level in Complexity]
PHASES = [str(phase) for phase in Phase]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="AL Engineering Harness: spec-driven design approval before implementation.",
    )
    parser.add_argument("--version", action="version", version=f"harness {__version__}")
    parser.add_argument("--db", metavar="PATH", help="state DB (default: $HARNESS_DB or per-repo)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="start a new harness task from a request")
    start.add_argument("request", nargs="*", help="the request text")
    start.add_argument("--complexity", choices=LEVELS, help="set complexity instead of a proposal")
    start.set_defaults(handler=cmd_start)

    phase = subparsers.add_parser("phase", help="move a task between phases")
    phase.add_argument("task_id")
    actions = phase.add_subparsers(dest="action", required=True)
    actions.add_parser("next", help="advance to the next phase")
    back = actions.add_parser("back", help="return to an earlier phase")
    back.add_argument("to_phase", choices=PHASES)
    back.add_argument("--reason", help="why the task goes back (required)")
    phase.set_defaults(handler=cmd_phase)

    classify = subparsers.add_parser("classify", help="override a task's complexity")
    classify.add_argument("task_id")
    classify.add_argument("level", choices=LEVELS, nargs="?")
    classify.add_argument(
        "--assessment",
        metavar="JSON",
        help="reassess remaining work from inspected risk evidence "
        "(format: harness classify --assessment-schema)",
    )
    from harness.input_formats import PrintFormat

    classify.add_argument(
        "--assessment-schema",
        action=PrintFormat,
        format_name="assessment",
        help="print the closed --assessment JSON format (schema and example) and exit",
    )
    classify.add_argument("--repo", metavar="PATH", help="repository for assessment evidence")
    classify.set_defaults(handler=cmd_classify)

    show = subparsers.add_parser("show", help="show a task, its interview depth and history")
    show.add_argument("task_id")
    show.set_defaults(handler=cmd_show)

    investigate = subparsers.add_parser(
        "investigate", help="read-only repository investigation (facts / assumptions / questions)"
    )
    investigate.add_argument("task_id")
    investigate.add_argument("--repo", metavar="PATH", help="target repository (default: git top)")
    investigate.add_argument(
        "--decisions", metavar="PATH", help="decision records (default: <repo>/docs/decisions)"
    )
    investigate.add_argument("--json", action="store_true", help="print the report as JSON")
    investigate.add_argument(
        "--full",
        action="store_true",
        help="print every fact, including the repository inventory (default: compact report)",
    )
    investigate.set_defaults(handler=cmd_investigate)

    from harness.cli_maintenance import register

    register(subparsers)
    add_dashboard_parser(subparsers)
    add_interview_parser(subparsers)
    add_design_parser(subparsers)
    add_decision_parser(subparsers)
    add_testplan_parser(subparsers)
    add_spec_parser(subparsers)
    add_review_parser(subparsers)

    add_resume_parser(subparsers)
    add_approve_parser(subparsers)
    add_execution_parser(subparsers)
    add_knowledge_parser(subparsers)
    add_delivery_parser(subparsers)
    add_browser_parser(subparsers)
    add_diagnostics_parser(subparsers)
    from harness.cli_session import add_session_parser

    add_session_parser(subparsers)
    add_storage_parser(subparsers)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (
        InvalidTransition,
        TaskNotFound,
        RecordNotFound,
        LookupError,
        ValueError,
        OSError,
    ) as error:
        print(f"harness {args.command}: {error}", file=sys.stderr)
        return ERROR_EXIT_CODE
    except __import__("sqlite3").Error as error:
        print(state_error_message(args.command, error), file=sys.stderr)
        return ERROR_EXIT_CODE


# Only these mean the database was written by a newer session protocol than this Harness knows.
_UPGRADE_MARKERS = (
    "SESSION_UPGRADE_REQUIRED",
    "harness_session_protocol_version",
    "harness_session_write_allowed",
    "Incomplete planning session schema",
)


def state_error_message(command: str, error: Exception) -> str:
    """One line for a state-DB failure: a session code only when it really is one."""
    from harness.db import BUSY_MESSAGE, is_busy
    from harness.planning_session_contract import SessionError

    text = str(error)
    if "SESSION_OPERATION_INCOMPATIBLE" in text:
        return str(SessionError("SESSION_OPERATION_INCOMPATIBLE"))
    if any(marker in text for marker in _UPGRADE_MARKERS):
        return str(SessionError("SESSION_UPGRADE_REQUIRED"))
    if is_busy(error):
        return f"harness {command}: {BUSY_MESSAGE}"
    return f"harness {command}: state database error: {text}"


if __name__ == "__main__":
    raise SystemExit(main())
