"""`harness spec ...`: the Specification Manager on the command line, relayed by Claude Code."""

import argparse
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from harness.cli_commands import _controller, resolve_db_path, task_repo_root
from harness.controller import HarnessController
from harness.db import connect, repo_root
from harness.errors import TaskNotFound
from harness.spec_manager import SpecificationManager, verify_store
from harness.spec_record import DEFAULT_SPECS_DIR, SpecRecord
from harness.spec_store import SpecStore

MODIFIED = "MODIFIED"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def spec_store(controller: HarnessController | None, task_id: str, specs: str | None) -> SpecStore:
    """Explicit --specs; else the investigated repository's docs/specs; else the git top's."""
    if not _SAFE_ID.match(task_id):
        raise ValueError(f"invalid task id {task_id!r}")
    if specs:
        root = Path(specs).resolve()
    elif controller is not None:
        root = task_repo_root(controller, task_id) / DEFAULT_SPECS_DIR
    else:
        root = repo_root() / DEFAULT_SPECS_DIR
    return SpecStore(root / task_id)


@contextmanager
def _known_task(args: argparse.Namespace) -> Iterator[HarnessController | None]:
    """The controller when the local DB knows the task; None (file-level only) otherwise."""
    path = resolve_db_path(args.db)
    if not path.exists():
        yield None
        return
    conn = connect(path)
    try:
        controller = HarnessController(conn)
        try:
            controller.get_task(args.task_id)
        except TaskNotFound:
            controller = None
        yield controller
    finally:
        conn.close()


def _as_dict(record: SpecRecord) -> dict[str, Any]:
    return {
        "version": record.version,
        "status": str(record.status),
        "timestamp": record.timestamp,
        "revision": record.revision,
        "working_tree": record.working_tree,
        "supersedes": record.supersedes,
        "superseded_by": record.superseded_by,
        "content_hash": record.content_hash,
        "path": str(record.path),
    }


def _list(store: SpecStore, as_json: bool) -> None:
    records = store.records()
    draft = store.draft()
    everything = [*records, *([draft] if draft else [])]
    if as_json:
        print(json.dumps([_as_dict(r) for r in everything], indent=2, sort_keys=True))
        return
    lines = [f"v{r.version} {r.status} {r.timestamp} revision {r.revision}" for r in records]
    lines += [f"draft {draft.timestamp}"] if draft else []
    print("\n".join(lines) if lines else "no specification files")


def _read_only(args: argparse.Namespace) -> int:
    with _known_task(args) as controller:
        store = spec_store(controller, args.task_id, args.specs)
        if args.spec_action == "list":
            _list(store, args.json)
            return 0
        rows = controller.workspace(args.task_id).spec_versions() if controller else None
        results = verify_store(store, rows)
    if not results:
        print("no specification files")
        return 0
    print("\n".join(f"{label} {result}" for label, result in results))
    return 1 if any(result.startswith(MODIFIED) for _, result in results) else 0


def approved_line(record: SpecRecord) -> str:
    return (
        f"approved v{record.version} -> {record.path}"
        f" (revision {record.revision}, {record.working_tree})"
    )


def _run(manager: SpecificationManager, action: str) -> None:
    if action == "show":
        print(manager.preview(), end="")
    elif action == "draft":
        print(f"draft -> {manager.draft()}")
    else:
        record, previous = manager.approve()
        print(approved_line(record))
        if previous is not None:
            print(f"v{previous.version} superseded")


def cmd_spec(args: argparse.Namespace) -> int:
    if args.spec_action in ("list", "verify"):
        return _read_only(args)
    with _controller(args) as controller:
        store = spec_store(controller, args.task_id, args.specs)
        root = task_repo_root(controller, args.task_id)
        _run(SpecificationManager(controller, args.task_id, store, root), args.spec_action)
    return 0


def add_spec_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("spec", help="the task's specification (docs/specs/<task>/)")
    actions = parser.add_subparsers(dest="spec_action", required=True)
    for name, help_text in (
        ("show", "preview the specification from the approved state (writes nothing)"),
        ("draft", "write the preview to docs/specs/<task>/draft.md"),
        ("approve", "seal v<N>.md tied to the repository revision (approval phase only)"),
        ("list", "every specification version of the task (read-only)"),
        ("verify", "check every approved file against its content hash (read-only)"),
    ):
        sub = actions.add_parser(name, help=help_text)
        sub.add_argument("task_id")
        sub.add_argument("--specs", metavar="PATH", help="specs root (default: <repo>/docs/specs)")
        if name == "list":
            sub.add_argument("--json", action="store_true")
    parser.set_defaults(handler=cmd_spec)
