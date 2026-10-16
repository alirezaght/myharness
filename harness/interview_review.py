"""Pure interview derivations: depth sync plan, question order, checklist and summary.

The checklist is exhaustive by construction (ROADMAP Risk #1): every open question, every
unsettled answer, every pending proposal and every catalogue question still due is an item.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from harness.classification import INTERVIEW_TOPICS
from harness.interview_catalogue import CatalogueQuestion
from harness.interview_report import (
    ChecklistItem,
    ChecklistState,
    InterviewChecklist,
    InterviewSummary,
    Provenance,
    Requirement,
    SkippedQuestion,
)
from harness.state_models import (
    Proposal,
    ProposalStatus,
    Question,
    QuestionKind,
    QuestionSource,
    QuestionStatus,
    SkipSource,
)

DEPTH_SKIP_REASON = "outside interview depth"
DECIDED = (ProposalStatus.ACCEPTED, ProposalStatus.MODIFIED)


@dataclass(frozen=True)
class SyncPlan:
    seed: tuple[CatalogueQuestion, ...] = ()
    skip: tuple[Question, ...] = ()
    reopen: tuple[Question, ...] = ()


def _order(question: Question) -> tuple[bool, int, int]:
    topic = question.topic
    index = INTERVIEW_TOPICS.index(topic) if topic in INTERVIEW_TOPICS else len(INTERVIEW_TOPICS)
    return (question.source is not QuestionSource.INVESTIGATION, index, question.id)


def ordered(questions: Iterable[Question]) -> list[Question]:
    return sorted(questions, key=_order)


def _depth_skipped(question: Question) -> bool:
    return (
        question.status is QuestionStatus.SKIPPED
        and question.skip_source is SkipSource.NOT_RELEVANT
        and (question.skip_reason or "").startswith(DEPTH_SKIP_REASON)
    )


def plan_sync(questions: list[Question], entries: tuple[CatalogueQuestion, ...]) -> SyncPlan:
    """Seed missing in-depth entries, skip never-asked out-of-depth ones, reopen returning ones."""
    seeded = {q.catalogue_key: q for q in questions if q.catalogue_key is not None}
    wanted = {entry.key for entry in entries}
    return SyncPlan(
        seed=tuple(entry for entry in entries if entry.key not in seeded),
        skip=tuple(
            q
            for key, q in seeded.items()
            if key not in wanted and q.status is QuestionStatus.OPEN and q.asked_at is None
        ),
        reopen=tuple(q for key, q in seeded.items() if key in wanted and _depth_skipped(q)),
    )


@dataclass
class _Tally:
    items: list[ChecklistItem] = field(default_factory=list)
    answered: int = 0
    decided: int = 0
    skipped: int = 0


def _open_state(question: Question, proposals: list[Proposal]) -> ChecklistState:
    if any(p.status is ProposalStatus.PENDING for p in proposals):
        return ChecklistState.AWAITING_DECISION
    if proposals and all(p.status is ProposalStatus.REJECTED for p in proposals):
        return ChecklistState.RECOMMENDATION_REJECTED
    return ChecklistState.UNANSWERED if question.asked_at else ChecklistState.NOT_ASKED


def _item(q: Question, state: ChecklistState, proposal: Proposal | None = None) -> ChecklistItem:
    pid = None if proposal is None else proposal.id
    return ChecklistItem(q.id, q.topic, q.text, str(q.kind), state, pid, q.catalogue_key)


def _tally_question(tally: _Tally, q: Question, linked: list[Proposal]) -> None:
    pending = [p for p in linked if p.status is ProposalStatus.PENDING]
    settled = any(p.status in DECIDED for p in linked)
    if q.status is QuestionStatus.OPEN:
        tally.items.append(_item(q, _open_state(q, linked), pending[0] if pending else None))
        pending = pending[1:]
    elif q.status is QuestionStatus.SKIPPED:
        tally.skipped += 1
    elif q.answer is not None:
        tally.answered += 1
    elif settled:
        tally.decided += 1
    else:
        tally.items.append(_item(q, ChecklistState.UNANSWERED))
    for proposal in pending:
        tally.items.append(_item(q, ChecklistState.AWAITING_DECISION, proposal))


def build_checklist(
    questions: list[Question], proposals: list[Proposal], due: SyncPlan | None
) -> InterviewChecklist:
    tally, by_question = _Tally(), {}
    for proposal in proposals:
        by_question.setdefault(proposal.question_id, []).append(proposal)
    for question in ordered(questions):
        returning = due is not None and question in due.reopen
        if returning:
            tally.items.append(_item(question, ChecklistState.NOT_ASKED))
        else:
            _tally_question(tally, question, by_question.get(question.id, []))
    for proposal in by_question.get(None, []):
        if proposal.status is ProposalStatus.PENDING:
            tally.items.append(
                ChecklistItem(
                    None,
                    "decision",
                    f"{proposal.title}: {proposal.recommendation}",
                    str(QuestionKind.DECISION),
                    ChecklistState.AWAITING_DECISION,
                    proposal.id,
                )
            )
        elif proposal.status in DECIDED:
            tally.decided += 1
    for entry in due.seed if due is not None else ():
        tally.items.append(
            ChecklistItem(
                None,
                entry.topic,
                entry.text,
                str(entry.kind),
                ChecklistState.NOT_ASKED,
                None,
                entry.key,
            )
        )
    return InterviewChecklist(tuple(tally.items), tally.answered, tally.decided, tally.skipped)


def _requirement(proposal: Proposal, question: Question | None) -> Requirement:
    accepted = proposal.status is ProposalStatus.ACCEPTED
    return Requirement(
        proposal.question_id,
        question.topic if question else "decision",
        question.text if question else proposal.title,
        proposal.recommendation if accepted else (proposal.response or ""),
        Provenance.ACCEPTED_RECOMMENDATION if accepted else Provenance.MODIFIED_RECOMMENDATION,
    )


def build_summary(
    questions: list[Question], proposals: list[Proposal], checklist: InterviewChecklist
) -> InterviewSummary:
    requirements, elsewhere, irrelevant = [], [], []
    for q in ordered(questions):
        if q.status is QuestionStatus.ANSWERED and q.answer is not None:
            requirements.append(Requirement(q.id, q.topic, q.text, q.answer, Provenance.DEVELOPER))
        requirements += [
            _requirement(p, q) for p in proposals if p.question_id == q.id and p.status in DECIDED
        ]
        if q.status is QuestionStatus.SKIPPED:
            source = q.skip_source or SkipSource.NOT_RELEVANT
            entry = SkippedQuestion(q.id, q.topic, q.text, str(source), q.skip_reason or "")
            (irrelevant if source is SkipSource.NOT_RELEVANT else elsewhere).append(entry)
    requirements += [
        _requirement(p, None) for p in proposals if p.question_id is None and p.status in DECIDED
    ]
    return InterviewSummary(tuple(requirements), tuple(elsewhere), tuple(irrelevant), checklist)
