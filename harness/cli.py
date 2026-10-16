"""Command-line entry point for the harness. `harness start` is the only way to create a task."""

import argparse
import sys

from harness import __version__
from harness.classification import Complexity
from harness.cli_commands import cmd_classify, cmd_phase, cmd_show, cmd_start
from harness.controller import TaskNotFound
from harness.phases import InvalidTransition, Phase

ERROR_EXIT_CODE = 1
NOT_IMPLEMENTED_EXIT_CODE = 2
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
    classify.add_argument("level", choices=LEVELS)
    classify.set_defaults(handler=cmd_classify)

    show = subparsers.add_parser("show", help="show a task, its interview depth and history")
    show.add_argument("task_id")
    show.set_defaults(handler=cmd_show)

    resume = subparsers.add_parser("resume", help="resume an existing task")
    resume.add_argument("task_id", nargs="?", help="task id (defaults to the latest task)")

    approve = subparsers.add_parser("approve", help="approve the final design of a task")
    approve.add_argument("task_id", nargs="?", help="task id (defaults to the latest task)")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        print(
            f"harness {args.command}: not implemented yet (see tasks/phase-1-core-harness.md)",
            file=sys.stderr,
        )
        return NOT_IMPLEMENTED_EXIT_CODE
    try:
        return handler(args)
    except (InvalidTransition, TaskNotFound, ValueError) as error:
        print(f"harness {args.command}: {error}", file=sys.stderr)
        return ERROR_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
