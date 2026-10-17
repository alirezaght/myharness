"""The harness's redesign loop entry point (P2-06): `python -m harness.redesign`.

    python -m harness.redesign <task id>                         list the open comments
    python -m harness.redesign address <task id> <comment id>...  mark them addressed

The agent running the harness lists the developer's open diagram comments, changes the design
only through the existing `harness` commands (the developer decides), then addresses exactly
the comments the accepted change handles. `address` first records the changed design as a new
diagram version, then moves every named comment open -> addressed in one transaction, each with
a comment.addressed event carrying that version. It lives outside the `harness` CLI so that
the CLI's commands, output and help stay unchanged (amendment A1, DEC-0006/DEC-0012). The
developer resolves or re-opens the comments in the dashboard; listing writes nothing.
"""

import argparse
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from harness.cli_commands import resolve_db_path
from harness.comments import CommentService, busy, element_labels
from harness.controller import HarnessController
from harness.db import connect
from harness.diagrams import DiagramService
from harness.errors import TaskNotFound
from harness.state_comments import CommentStatus

PROG = "python -m harness.redesign"
DB_HELP = "state DB (default: $HARNESS_DB or per-repo)"


def build_list_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="list a task's open diagram comments; 'address' marks them addressed",
        epilog=f"to address comments: {PROG} address <task_id> <comment_id>...",
    )
    parser.add_argument("task_id", help="the task")
    parser.add_argument("--db", metavar="PATH", help=DB_HELP)
    return parser


def build_address_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{PROG} address",
        description="record the current design as a diagram version, then mark the comments"
        " addressed in it (all or nothing)",
    )
    parser.add_argument("task_id", help="the task")
    parser.add_argument("comment_ids", type=int, nargs="+", metavar="comment_id")
    parser.add_argument("--db", metavar="PATH", help=DB_HELP)
    return parser


@contextmanager
def _read(conn: sqlite3.Connection) -> Iterator[None]:
    """One deferred read transaction, always rolled back: listing writes nothing."""
    conn.execute("BEGIN")
    try:
        yield
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")


def list_open(controller: HarnessController, task_id: str) -> str:
    service = DiagramService(controller, task_id)
    with _read(controller.conn):
        latest = service.latest()
        outdated = DiagramService.needs(service.model(), latest)
        comments = service.ws.comments()
        open_ = [c for c in comments if c.status == CommentStatus.OPEN]
        labels = element_labels(service, open_)
    count = {s: sum(c.status == s for c in comments) for s in CommentStatus}
    shown = f"diagram version {latest.version} (current)" if latest else "no diagrams yet"
    lines = [
        f"task {service.task_id} · {shown} · comments: open {count[CommentStatus.OPEN]}"
        f" · addressed {count[CommentStatus.ADDRESSED]} · resolved {count[CommentStatus.RESOLVED]}"
    ]
    if latest and outdated:
        lines.append(
            f"the design changed since diagram version {latest.version};"
            " address records the new version"
        )
    for comment in open_:
        kind, label = labels[comment.id]
        element = f"{kind} {label} ({comment.element_id})" if kind else comment.element_id
        current = latest is not None and comment.diagram_version == latest.version
        stale = "" if current else " (not current)"
        lines.append(
            f"#{comment.id} · {comment.kind} · {element} · version {comment.diagram_version}{stale}"
        )
        lines += ["    " + line for line in comment.text.splitlines() or [""]]
    if open_:
        lines.append(f"address: uv run {PROG} address {service.task_id} <comment id>...")
    else:
        lines.append("no open comments")
    return "\n".join(lines)


def address(controller: HarnessController, task_id: str, ids: list[int]) -> str:
    service = CommentService(controller, task_id)
    recorded = service.diagrams.sync()
    version, results = service.address_all(ids)
    lines = [f"recorded diagram version {recorded.version}"] if recorded else []
    for comment, moved in results:
        if not moved:
            lines.append(f"#{comment.id} already addressed")
            continue
        same = comment.diagram_version == version
        note = f" (diagrams unchanged since version {version})" if same else ""
        lines.append(f"#{comment.id} addressed in diagram version {version}{note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["address"]:
        args = build_address_parser().parse_args(argv[1:])

        def run(controller: HarnessController) -> str:
            return address(controller, args.task_id, args.comment_ids)
    else:
        args = build_list_parser().parse_args(argv)

        def run(controller: HarnessController) -> str:
            return list_open(controller, args.task_id)

    try:
        path = resolve_db_path(args.db)
        if not path.exists():
            raise TaskNotFound(args.task_id)
        conn = connect(path)
        try:
            out = run(HarnessController(conn))
        finally:
            conn.close()
    except sqlite3.OperationalError as error:
        retry = "; retry" if busy(error) else ""
        print(f"harness redesign: {error}{retry}", file=sys.stderr)
        return 1
    except (LookupError, ValueError, sqlite3.Error, OSError) as error:
        print(f"harness redesign: {error}", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
