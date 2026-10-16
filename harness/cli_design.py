"""`harness design ...`: the CLI side of the Architecture Designer, relayed by Claude Code."""

import argparse
import json

from harness.cli_commands import _controller
from harness.design import ArchitectureDesigner
from harness.design_models import Binding, DesignSection, EntryStatus

DECISIONS = {
    "accept": EntryStatus.ACCEPTED,
    "reject": EntryStatus.REJECTED,
    "modify": EntryStatus.MODIFIED,
}
SECTIONS = [str(section) for section in DesignSection]


def _run(designer: ArchitectureDesigner, args: argparse.Namespace) -> None:
    action, task = args.design_action, designer.task_id
    if action == "add":
        entry = designer.add(args.section, " ".join(args.text), delegated=args.delegated)
        print(f"{entry.ref} stated [{entry.section}] ({entry.binding})")
    elif action == "none":
        entry = designer.none(args.section, args.reason)
        print(f"{entry.ref} [{entry.section}] not applicable")
    elif action == "propose":
        text = " ".join(args.text)
        entry = designer.propose(args.section, text, args.rationale, delegated=args.delegated)
        tag = " (delegated)" if entry.binding is Binding.DELEGATED else ""
        print(f"{entry.ref} pending [{entry.section}]{tag}")
        print(f"  decide: harness design decide {task} {entry.id} accept|reject|modify")
    elif action == "decide":
        entry = designer.decide(args.entry_id, DECISIONS[args.verdict], args.response)
        print(f"{entry.ref} {entry.status}")
    elif action == "withdraw":
        print(f"{designer.withdraw(args.entry_id).ref} withdrawn")
    else:
        view = designer.document() if action == "show" else designer.coverage()
        print(json.dumps(view.to_dict(), indent=2, sort_keys=True) if args.json else view.render())


def cmd_design(args: argparse.Namespace) -> int:
    with _controller(args) as controller:
        _run(ArchitectureDesigner(controller, args.task_id), args)
    return 0


def add_design_parser(subparsers: argparse._SubParsersAction) -> None:
    design = subparsers.add_parser("design", help="architecture design with delegation marks")
    actions = design.add_subparsers(dest="design_action", required=True)

    def action(name: str, help_text: str) -> argparse.ArgumentParser:
        parser = actions.add_parser(name, help=help_text)
        parser.add_argument("task_id")
        return parser

    add = action("add", "record a design element the developer stated")
    add.add_argument("section", choices=SECTIONS)
    add.add_argument("text", nargs="+")
    add.add_argument("--delegated", action="store_true", help="detail delegated to AL")
    none = action("none", "the developer states a section does not apply")
    none.add_argument("section", choices=SECTIONS)
    none.add_argument("--reason", required=True)
    propose = action("propose", "recommend a design element with rationale")
    propose.add_argument("section", choices=SECTIONS)
    propose.add_argument("text", nargs="+")
    propose.add_argument("--rationale", required=True)
    propose.add_argument("--delegated", action="store_true", help="detail delegated to AL")
    decide = action("decide", "record the developer's decision on a recommendation")
    decide.add_argument("entry_id", type=int)
    decide.add_argument("verdict", choices=list(DECISIONS))
    decide.add_argument("--response", help="the developer's change (required for modify)")
    action("withdraw", "withdraw an effective design entry").add_argument("entry_id", type=int)
    for name, help_text in (
        ("show", "the design section with provenance (read-only)"),
        ("coverage", "uncovered and unresolved design items (read-only)"),
    ):
        action(name, help_text).add_argument("--json", action="store_true")
    design.set_defaults(handler=cmd_design)
