"""Command-line entry point for the harness (Phase 1 stubs)."""

import argparse
import sys

from harness import __version__

NOT_IMPLEMENTED_EXIT_CODE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="AL Engineering Harness: spec-driven design approval before implementation.",
    )
    parser.add_argument("--version", action="version", version=f"harness {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="start a new harness task from a request")
    start.add_argument("request", nargs="*", help="the request text")

    resume = subparsers.add_parser("resume", help="resume an existing task")
    resume.add_argument("task_id", nargs="?", help="task id (defaults to the latest task)")

    approve = subparsers.add_parser("approve", help="approve the final design of a task")
    approve.add_argument("task_id", nargs="?", help="task id (defaults to the latest task)")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(
        f"harness {args.command}: not implemented yet (see tasks/phase-1-core-harness.md)",
        file=sys.stderr,
    )
    return NOT_IMPLEMENTED_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
