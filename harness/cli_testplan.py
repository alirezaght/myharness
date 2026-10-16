"""`harness testplan ...`: the CLI side of the Test Planner, relayed by Claude Code."""

import argparse
import json

from harness.cli_commands import _controller
from harness.design_models import EntryStatus
from harness.state_testplan import UNSET
from harness.testplan import TestPlanError, TestPlanner
from harness.testplan_models import Scenario, TestCategory

DECISIONS = {
    "accept": EntryStatus.ACCEPTED,
    "reject": EntryStatus.REJECTED,
    "modify": EntryStatus.MODIFIED,
}
CATEGORIES = [str(category) for category in TestCategory]


def _edit(planner: TestPlanner, args: argparse.Namespace) -> Scenario:
    if args.no_mock and (args.mock is not None or args.mock_reason is not None):
        raise TestPlanError("--no-mock cannot be combined with --mock or --mock-reason")
    changes = (args.text, args.category, args.covers, args.mock, args.mock_reason)
    if not args.no_mock and all(value is None for value in changes):
        raise TestPlanError("nothing to edit: give --text, --category, --covers or a mock change")
    return planner.edit(
        args.scenario_id,
        args.text,
        args.category,
        args.covers,
        UNSET if args.mock is None else args.mock,
        UNSET if args.mock_reason is None else args.mock_reason,
        clear_mock=args.no_mock,
    )


def _run(planner: TestPlanner, args: argparse.Namespace) -> None:
    action, task = args.testplan_action, planner.task_id
    if action == "add":
        text, covers = " ".join(args.text), args.covers or ()
        scenario = planner.add(args.category, text, covers, args.mock, args.mock_reason)
        print(f"{scenario.ref} stated [{scenario.category}]")
    elif action == "propose":
        text = " ".join(args.text)
        scenario = planner.propose(
            args.category, text, args.rationale, args.covers, args.mock, args.mock_reason
        )
        print(f"{scenario.ref} pending [{scenario.category}]")
        print(f"  decide: harness testplan decide {task} {scenario.id} accept|reject|modify")
    elif action == "decide":
        scenario = planner.decide(args.scenario_id, DECISIONS[args.verdict], args.response)
        print(f"{scenario.ref} {scenario.status}")
    elif action == "edit":
        print(f"{_edit(planner, args).ref} edited")
    elif action == "withdraw":
        print(f"{planner.withdraw(args.scenario_id).ref} withdrawn")
    elif action == "flag" and args.clear:
        print(f"{planner.clear_flag(args.scenario_id, args.reason).ref} flag cleared")
    elif action == "flag":
        scenario = planner.flag(args.scenario_id, args.reason)
        print(f"{scenario.ref} flagged: {scenario.concern}")
    elif action == "approve":
        approval = planner.approve()
        print(
            f"approved test plan ({approval.scenario_count} scenario(s), {approval.content_hash})"
        )
    else:
        view = planner.document() if action == "show" else planner.review()
        print(json.dumps(view.to_dict(), indent=2, sort_keys=True) if args.json else view.render())


def cmd_testplan(args: argparse.Namespace) -> int:
    with _controller(args) as controller:
        _run(TestPlanner(controller, args.task_id), args)
    return 0


def _mock_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mock", metavar="TARGET", help="what the scenario mocks")
    parser.add_argument("--mock-reason", metavar="WHY", help="written justification of the mock")


def add_testplan_parser(subparsers: argparse._SubParsersAction) -> None:
    testplan = subparsers.add_parser("testplan", help="test planning with justified mocks")
    actions = testplan.add_subparsers(dest="testplan_action", required=True)

    def action(name: str, help_text: str) -> argparse.ArgumentParser:
        parser = actions.add_parser(name, help=help_text)
        parser.add_argument("task_id")
        return parser

    covers = {"action": "append", "metavar": "REF", "help": "requirement Q<n> or design E<n>"}
    add = action("add", "record a test scenario the developer stated")
    add.add_argument("category", choices=CATEGORIES)
    add.add_argument("text", nargs="+")
    add.add_argument("--covers", **covers)
    _mock_options(add)
    propose = action("propose", "recommend a test scenario that covers the task")
    propose.add_argument("category", choices=CATEGORIES)
    propose.add_argument("text", nargs="+")
    propose.add_argument("--rationale", required=True)
    propose.add_argument("--covers", required=True, **covers)
    _mock_options(propose)
    decide = action("decide", "record the developer's decision on a recommendation")
    decide.add_argument("scenario_id", type=int)
    decide.add_argument("verdict", choices=list(DECISIONS))
    decide.add_argument("--response", help="the developer's change (required for modify)")
    edit = action("edit", "change an effective scenario")
    edit.add_argument("scenario_id", type=int)
    edit.add_argument("--text")
    edit.add_argument("--category", choices=CATEGORIES)
    edit.add_argument("--covers", **covers)
    _mock_options(edit)
    edit.add_argument("--no-mock", action="store_true", help="remove the scenario's mock")
    action("withdraw", "withdraw an effective scenario").add_argument("scenario_id", type=int)
    flag = action("flag", "flag a mock that hides missing behaviour")
    flag.add_argument("scenario_id", type=int)
    flag.add_argument("--reason", required=True, help="the concern, or why it is cleared")
    flag.add_argument("--clear", action="store_true", help="clear the flag")
    action("approve", "approve the current test plan")
    for name, help_text in (
        ("show", "the test plan with provenance (read-only)"),
        ("review", "undecided, flagged and unapproved items (read-only)"),
    ):
        action(name, help_text).add_argument("--json", action="store_true")
    testplan.set_defaults(handler=cmd_testplan)
