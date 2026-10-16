"""Handlers for the harness subcommands; each opens the state DB only when it runs."""

import argparse
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from harness.analyzer import analyze
from harness.classification import Complexity, interview_depth
from harness.controller import SOURCE_PROPOSED, HarnessController, Task, TaskNotFound
from harness.db import connect, default_db_path, repo_root
from harness.phases import Phase


def resolve_db_path(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    env = os.environ.get("HARNESS_DB")
    return Path(env) if env else default_db_path()


@contextmanager
def _controller(args: argparse.Namespace, create: bool = False) -> Iterator[HarnessController]:
    path = resolve_db_path(args.db)
    if not create and not path.exists():
        raise TaskNotFound(args.task_id)
    conn = connect(path)
    try:
        yield HarnessController(conn)
    finally:
        conn.close()


def task_repo_root(controller: HarnessController, task_id: str) -> Path:
    """The repository the task investigated; else the git top of the current directory."""
    checkpoint = controller.workspace(task_id).latest_checkpoint("investigation")
    root = checkpoint.payload.get("repo_root") if checkpoint else None
    return Path(root) if root else repo_root()


def _summary(task: Task) -> str:
    return (
        f"task {task.id} · phase {task.phase} · complexity {task.complexity}"
        f" ({task.complexity_source})"
    )


def cmd_start(args: argparse.Namespace) -> int:
    request = " ".join(args.request).strip()
    if not request:
        raise ValueError("request must not be empty")
    complexity = Complexity(args.complexity) if args.complexity else None
    with _controller(args, create=True) as controller:
        task = controller.create_task(request, complexity)
    print(
        f"task {task.id} created · phase {task.phase} · complexity {task.complexity}"
        f" ({task.complexity_source})"
    )
    if task.complexity_source == SOURCE_PROPOSED:
        print(f"override: harness classify {task.id} <{'|'.join(Complexity)}>")
    return 0


def cmd_phase(args: argparse.Namespace) -> int:
    with _controller(args) as controller:
        if args.action == "next":
            task = controller.advance(args.task_id)
        else:
            task = controller.go_back(args.task_id, Phase(args.to_phase), args.reason or "")
    print(_summary(task))
    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    with _controller(args) as controller:
        task = controller.set_complexity(args.task_id, Complexity(args.level))
    print(_summary(task))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    with _controller(args) as controller:
        task = controller.get_task(args.task_id)
        history = controller.history(task.id)
    print(_summary(task))
    print(f"request: {task.request}")
    print(f"created: {task.created_at} · updated: {task.updated_at}")
    print(f"interview topics: {', '.join(interview_depth(task.complexity).topics)}")
    print("history:")
    for step in history:
        origin = step.from_phase or "-"
        reason = f" ({step.reason})" if step.reason else ""
        print(f"  {step.created_at} {origin} -> {step.to_phase}{reason}")
    return 0


def cmd_investigate(args: argparse.Namespace) -> int:
    with _controller(args) as controller:
        task = controller.get_task(args.task_id)
        root = Path(args.repo) if args.repo else repo_root()
        decisions = Path(args.decisions) if args.decisions else None
        report = analyze(root, task.request, decisions)
        controller.record_investigation(task.id, report)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(report.render())
    return 0
