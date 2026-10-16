"""`harness resume [<task>]`: where an interrupted task continues; read-only, writes nothing."""

import argparse
import json

from harness.cli_commands import _controller, resolve_db_path, task_repo_root
from harness.cli_interview import print_question
from harness.cli_spec import spec_store
from harness.interview import InterviewEngine
from harness.resume import ResumePoint, resumable_tasks, resume_point

NO_TASKS = "no tasks to resume"


def _show(args: argparse.Namespace) -> int:
    with _controller(args) as controller:
        store = spec_store(controller, args.task_id, args.specs)
        point = resume_point(
            controller, args.task_id, store, task_repo_root(controller, args.task_id)
        )
        if args.json:
            print(json.dumps(point.to_dict(), indent=2, sort_keys=True))
            return 0
        print(point.render_head())
        if point.pending_question is not None:
            print_question(InterviewEngine(controller, point.task.id), point.pending_question)
        print(point.render_tail())
    return 0


def _line(point: ResumePoint) -> str:
    task = point.task
    head = (
        f"{task.id} · phase {task.phase} · complexity {task.complexity} · updated {task.updated_at}"
    )
    return f"{head}\n  {task.request}\n  next: {point.next_command}"


def _list(args: argparse.Namespace) -> int:
    if not resolve_db_path(args.db).exists():
        print("[]" if args.json else NO_TASKS)
        return 0
    with _controller(args) as controller:
        points = resumable_tasks(
            controller,
            lambda task_id: spec_store(controller, task_id, args.specs),
            lambda task_id: task_repo_root(controller, task_id),
        )
    if args.json:
        print(json.dumps([p.to_dict() for p in points], indent=2, sort_keys=True))
    else:
        print("\n".join(_line(p) for p in points) if points else NO_TASKS)
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    return _list(args) if args.task_id is None else _show(args)


def add_resume_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "resume",
        help="where an interrupted task continues; no id lists the resumable tasks (read-only)",
    )
    parser.add_argument("task_id", nargs="?", help="task id (omit to list resumable tasks)")
    parser.add_argument("--json", action="store_true", help="print the resume point(s) as JSON")
    parser.add_argument("--specs", metavar="PATH", help="specs root (default: <repo>/docs/specs)")
    parser.set_defaults(handler=cmd_resume)
