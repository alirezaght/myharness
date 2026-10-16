"""`harness interview ...`: the CLI side of the Interview Engine; Claude Code relays its output."""

import argparse
import json

from harness.cli_commands import _controller
from harness.interview import InterviewEngine
from harness.state_models import ProposalStatus, Question, QuestionKind, SkipSource

DECISIONS = {
    "accept": ProposalStatus.ACCEPTED,
    "reject": ProposalStatus.REJECTED,
    "modify": ProposalStatus.MODIFIED,
}


def _print_question(engine: InterviewEngine, question: Question) -> None:
    tag = " (decision)" if question.kind is QuestionKind.DECISION else ""
    print(f"Q{question.id} [{question.topic}]{tag} {question.text}")
    pending = engine.pending_proposals(question.id)
    task = engine.task_id
    for proposal in pending:
        print(f"  P{proposal.id} {proposal.title}: {proposal.recommendation}")
        print(f"  rationale: {proposal.rationale}")
        print(f"  decide: harness interview decide {task} {proposal.id} accept|reject|modify")
    if question.kind is QuestionKind.DECISION and not pending:
        print(
            f"  recommendation needed: harness interview propose {task} {question.id}"
            " --title ... --recommendation ... --rationale ..."
        )


def cmd_interview(args: argparse.Namespace) -> int:
    with _controller(args) as controller:
        engine = InterviewEngine(controller, args.task_id)
        action = args.interview_action
        if action == "next":
            asked = engine.next(group=args.group)
            for question in asked:
                _print_question(engine, question)
            unresolved = len(engine.ws.open_questions())
            if not asked and unresolved:
                print(
                    f"no questions left to ask; {unresolved} open question(s) stay unresolved"
                    f" (see: harness interview checklist {args.task_id})"
                )
            elif not asked:
                print("no open questions")
        elif action == "answer":
            print(f"Q{engine.answer(args.question_id, ' '.join(args.text)).id} answered")
        elif action == "unknown":
            question = engine.unknown(args.question_id)
            print(f"Q{question.id} unresolved (developer does not know)")
        elif action == "skip":
            question = engine.skip(args.question_id, SkipSource(args.source), args.ref)
            print(f"Q{question.id} skipped ({question.skip_source} {question.skip_reason})")
        elif action == "ask":
            question = engine.ask(args.topic, " ".join(args.text), decision=args.decision)
            print(f"Q{question.id} added [{question.topic}]")
        elif action == "propose":
            proposal = engine.propose(
                args.question_id, args.title, args.recommendation, args.rationale
            )
            print(f"P{proposal.id} pending for Q{args.question_id}")
        elif action == "decide":
            proposal = engine.decide(args.proposal_id, DECISIONS[args.verdict], args.response)
            print(f"P{proposal.id} {proposal.status}")
        else:
            view = engine.checklist() if action == "checklist" else engine.summary()
            print(
                json.dumps(view.to_dict(), indent=2, sort_keys=True) if args.json else view.render()
            )
    return 0


def add_interview_parser(subparsers: argparse._SubParsersAction) -> None:
    interview = subparsers.add_parser("interview", help="adaptive interview and decision checklist")
    actions = interview.add_subparsers(dest="interview_action", required=True)

    def action(name: str, help_text: str) -> argparse.ArgumentParser:
        parser = actions.add_parser(name, help=help_text)
        parser.add_argument("task_id")
        return parser

    action("next", "ask the next open question").add_argument(
        "--group", action="store_true", help="every open question of the same topic"
    )
    answer = action("answer", "record the developer's answer")
    answer.add_argument("question_id", type=int)
    answer.add_argument("text", nargs="+")
    action("unknown", "the developer does not know; keep the question open").add_argument(
        "question_id", type=int
    )
    skip = action("skip", "skip a question already answered elsewhere or not relevant")
    skip.add_argument("question_id", type=int)
    skip.add_argument("--source", required=True, choices=[str(s) for s in SkipSource])
    skip.add_argument("--ref", required=True, help="decision id, evidence or reason")
    ask = action("ask", "add a follow-up question")
    ask.add_argument("topic")
    ask.add_argument("text", nargs="+")
    ask.add_argument("--decision", action="store_true", help="needs a recommendation")
    propose = action("propose", "attach a recommendation with rationale to a question")
    propose.add_argument("question_id", type=int)
    for flag in ("--title", "--recommendation", "--rationale"):
        propose.add_argument(flag, required=True)
    decide = action("decide", "record the developer's decision on a recommendation")
    decide.add_argument("proposal_id", type=int)
    decide.add_argument("verdict", choices=list(DECISIONS))
    decide.add_argument("--response", help="the developer's change (required for modify)")
    for name, help_text in (
        ("checklist", "unresolved decision checklist (read-only)"),
        ("summary", "requirements with provenance (read-only)"),
    ):
        action(name, help_text).add_argument("--json", action="store_true")
    interview.set_defaults(handler=cmd_interview)
