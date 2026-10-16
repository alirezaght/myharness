"""`harness decision ...`: the Decision Registry on the command line, relayed by Claude Code."""

import argparse
import json
from pathlib import Path
from typing import Any

from harness.cli_commands import _controller
from harness.controller import HarnessController
from harness.db import repo_root
from harness.decision_suggest import render
from harness.decision_workflow import DecisionWorkflow
from harness.decisions import DEFAULT_DECISIONS_DIR, DecisionRecord
from harness.investigation import _decision_dict
from harness.registry import DecisionRegistry
from harness.state_decisions import ChangeStatus

VERDICTS = {
    "accept": ChangeStatus.ACCEPTED,
    "reject": ChangeStatus.REJECTED,
    "modify": ChangeStatus.MODIFIED,
}


def registry_for(
    controller: HarnessController | None, task_id: str | None, decisions: str | None
) -> DecisionRegistry:
    """Explicit path; else the investigated repository's docs/decisions; else the git top's."""
    if decisions:
        return DecisionRegistry(Path(decisions))
    if controller is not None and task_id is not None:
        checkpoint = controller.workspace(task_id).latest_checkpoint("investigation")
        root = checkpoint.payload.get("repo_root") if checkpoint else None
        if root:
            return DecisionRegistry(Path(root) / DEFAULT_DECISIONS_DIR)
    return DecisionRegistry(repo_root() / DEFAULT_DECISIONS_DIR)


def _as_dict(record: DecisionRecord) -> dict[str, Any]:
    data = _decision_dict(record)
    data |= {
        "superseded_by": record.superseded_by,
        "decision": record.decision,
        "rationale": record.rationale,
        "impact": record.impact,
    }
    return data


def _line(record: DecisionRecord) -> str:
    since = f" (supersedes v{record.version - 1})" if record.version > 1 else ""
    return f"{record.id} v{record.version} {record.status} — {record.title}{since}"


def _detail(record: DecisionRecord) -> list[str]:
    return [
        f"{record.id} v{record.version} — {record.title}",
        f"  status: {record.status} · timestamp: {record.timestamp} · scope: {record.scope}",
        f"  supersedes: {record.supersedes or 'none'}"
        + (f" · superseded by: {record.superseded_by}" if record.superseded_by else ""),
        f"  decision: {record.decision}",
        f"  rationale: {record.rationale or 'not stated'}",
        f"  impact: {record.impact or 'not stated'}",
        f"  file: {record.path}",
    ]


def _read_only(args: argparse.Namespace) -> None:
    registry = registry_for(None, None, args.decisions)
    if args.decision_action == "list":
        records = registry.records()
        if args.json:
            print(json.dumps([_as_dict(r) for r in records], indent=2, sort_keys=True))
        else:
            print("\n".join(_line(r) for r in records) if records else "no decisions")
        return
    records = registry.chain(args.decision_id) if args.history else [registry.get(args.decision_id)]
    if args.json:
        print(json.dumps([_as_dict(r) for r in records], indent=2, sort_keys=True))
    else:
        print("\n".join(line for r in records for line in _detail(r)))


def _run(flow: DecisionWorkflow, args: argparse.Namespace) -> None:
    action = args.decision_action
    if action == "record":
        record = flow.record(args.title, args.decision, args.rationale, args.scope, args.impact)
        print(f"{record.id} v{record.version} recorded -> {record.path}")
    elif action == "change":
        _, preview = flow.change(
            args.decision_id, args.decision, args.rationale, args.impact, args.title, args.scope
        )
        print(preview.render())
    elif action == "decide":
        change, record = flow.decide(args.change_id, VERDICTS[args.verdict], args.response)
        if record is None:
            print(f"{change.ref} {change.status}")
        else:
            kept = flow.registry.history(record.id)[-1].path
            print(
                f"{change.ref} {change.status} — {record.id} v{record.version} recorded"
                f" -> {record.path}; v{change.base_version} kept in {kept}"
            )
    else:
        found, notes = flow.suggest([Path(p) for p in args.sources])
        if args.json:
            data = {"suggestions": [s.to_dict() for s in found], "notes": notes, "applied": False}
            print(json.dumps(data, indent=2, sort_keys=True))
        else:
            print(render(found, notes))


def cmd_decision(args: argparse.Namespace) -> int:
    if args.decision_action in ("list", "show"):
        _read_only(args)
        return 0
    with _controller(args) as controller:
        registry = registry_for(controller, args.task_id, args.decisions)
        _run(DecisionWorkflow(controller, args.task_id, registry), args)
    return 0


def add_decision_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("decision", help="the decision registry (docs/decisions)")
    actions = parser.add_subparsers(dest="decision_action", required=True)

    def action(name: str, help_text: str, task: bool = True) -> argparse.ArgumentParser:
        sub = actions.add_parser(name, help=help_text)
        if task:
            sub.add_argument("task_id")
        sub.add_argument(
            "--decisions", metavar="PATH", help="registry (default: <repo>/docs/decisions)"
        )
        return sub

    action("list", "current version of every decision (read-only)", task=False).add_argument(
        "--json", action="store_true"
    )
    show = action("show", "one decision; --history adds every earlier version", task=False)
    show.add_argument("decision_id")
    show.add_argument("--history", action="store_true", help="the full supersede chain")
    show.add_argument("--json", action="store_true")
    record = action("record", "record a decision the developer stated")
    for flag in ("--title", "--decision", "--rationale"):
        record.add_argument(flag, required=True)
    record.add_argument("--scope")
    record.add_argument("--impact")
    change = action("change", "propose a new version; shows what it replaces, writes nothing")
    change.add_argument("decision_id")
    for flag in ("--decision", "--rationale", "--impact"):
        change.add_argument(flag, required=True)
    change.add_argument("--title")
    change.add_argument("--scope")
    decide = action("decide", "the developer's verdict on a proposed change")
    decide.add_argument("change_id", type=int)
    decide.add_argument("verdict", choices=list(VERDICTS))
    decide.add_argument("--response", help="the developer's change (required for modify)")
    suggest = action("suggest", "read-only hints from other repositories' decisions")
    suggest.add_argument(
        "--from", dest="sources", metavar="PATH", action="append", required=True,
        help="another repository or decisions directory (repeatable)",
    )  # fmt: skip
    suggest.add_argument("--json", action="store_true")
    parser.set_defaults(handler=cmd_decision)
