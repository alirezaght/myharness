"""Interview Engine (SPEC §3 Phase 2): question state, depth, decision gating, no invention."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from harness.classification import interview_depth
from harness.controller import HarnessController, Task
from harness.db import transaction, utcnow
from harness.interview_catalogue import catalogue_for
from harness.interview_report import InterviewChecklist, InterviewSummary
from harness.interview_review import (
    DECIDED,
    DEPTH_SKIP_REASON,
    build_checklist,
    build_summary,
    ordered,
    plan_sync,
)
from harness.phases import Phase
from harness.state_models import (
    Proposal,
    ProposalStatus,
    Question,
    QuestionKind,
    QuestionStatus,
    SkipSource,
)

__all__ = ["DEPTH_SKIP_REASON", "NON_ANSWERS", "InterviewEngine", "InterviewError"]

NON_ANSWERS = frozenset(
    {"i don't know", "i dont know", "idk", "dunno", "no idea", "not sure", "unknown", "?"}
)
UNRESOLVED_EVENT = "question.unresolved"


class InterviewError(ValueError):
    pass


def _is_non_answer(text: str) -> bool:
    normal = " ".join(text.replace("’", "'").lower().split())
    return normal in NON_ANSWERS or normal.rstrip(".!") in NON_ANSWERS


class InterviewEngine:
    def __init__(self, controller: HarnessController, task_id: str):
        self.controller = controller
        self.conn = controller.conn
        self.task_id = controller.get_task(task_id).id
        self.ws = controller.workspace(task_id)

    # Read-only views

    def checklist(self) -> InterviewChecklist:
        task = self.controller.get_task(self.task_id)
        pending = plan_sync(self.ws.questions(), catalogue_for(self._topics(task)))
        return build_checklist(self.ws.questions(), self.ws.proposals(), pending)

    def summary(self) -> InterviewSummary:
        return build_summary(self.ws.questions(), self.ws.proposals(), self.checklist())

    def pending_proposals(self, question_id: int) -> list[Proposal]:
        return [
            p for p in self.ws.proposals(ProposalStatus.PENDING) if p.question_id == question_id
        ]

    # Mutations: each one transaction, one event and one `interview` checkpoint

    def next(self, group: bool = False) -> list[Question]:
        """Ask the first askable question (or its topic group); deferred ones stay open."""
        with self._mutation() as (seeded, skipped):
            askable = self._askable()
            chosen = askable[:1]
            if group and chosen:
                chosen = [q for q in askable if q.topic == chosen[0].topic]
            asked = [self.ws.mark_asked(q.id) for q in chosen]
            ids = [q.id for q in asked]
            payload = {"ids": ids, "group": group, "seeded": seeded, "skipped": skipped}
            self.ws.record_event("question.asked", payload)
        return asked

    def answer(self, question_id: int, text: str) -> Question:
        given = (text or "").strip()
        if not given or _is_non_answer(given):
            raise InterviewError(
                f"'{given}' is not an answer; if the developer does not know, record it with:"
                f" harness interview unknown {self.task_id} {question_id}"
            )
        with self._mutation():
            question = self.ws.answer(question_id, text)
            self.ws.record_event("question.answered", {"id": question_id})
        return question

    def unknown(self, question_id: int) -> Question:
        with self._mutation():
            self._open(question_id)
            question = self.ws.mark_asked(question_id)
            self.ws.record_event(UNRESOLVED_EVENT, {"id": question_id})
        return question

    def skip(self, question_id: int, source: SkipSource, ref: str) -> Question:
        origin = SkipSource(source)
        with self._mutation():
            self._open(question_id)
            reason = (ref or "").strip()
            if origin is SkipSource.DECISION:
                approved = self._approved_decision_ids()
                if reason not in approved:
                    known = ", ".join(sorted(approved)) or "none"
                    raise InterviewError(
                        f"{reason or '(empty)'} is not an approved decision (approved: {known})"
                    )
            question = self.ws.skip(question_id, reason, origin)
            self.ws.record_event("question.skipped", {"id": question_id, "source": origin})
        return question

    def ask(self, topic: str, text: str, decision: bool = False) -> Question:
        kind = QuestionKind.DECISION if decision else QuestionKind.QUESTION
        with self._mutation():
            question = self.ws.ask(topic, text, kind=kind)
            self.ws.record_event("question.added", {"id": question.id, "kind": kind})
        return question

    def propose(
        self, question_id: int, title: str, recommendation: str, rationale: str
    ) -> Proposal:
        with self._mutation():
            self._open(question_id)
            self.ws.mark_asked(question_id)
            proposal = self.ws.propose(title, recommendation, rationale, question_id)
            self.ws.record_event("proposal.created", {"id": proposal.id, "question": question_id})
        return proposal

    def decide(self, proposal_id: int, status: ProposalStatus, response: str | None) -> Proposal:
        with self._mutation():
            proposal = self.ws.resolve(proposal_id, status, response)
            qid = proposal.question_id
            if proposal.status in DECIDED and qid is not None:
                if self.ws.question(qid).status is QuestionStatus.OPEN:
                    self.ws.mark_decided(qid)
            self.ws.record_event(
                "proposal.decided", {"id": proposal_id, "status": proposal.status, "question": qid}
            )
        return proposal

    # Internals

    @staticmethod
    def _topics(task: Task) -> tuple[str, ...]:
        return interview_depth(task.complexity).topics

    @contextmanager
    def _mutation(self) -> Iterator[tuple[int, int]]:
        with transaction(self.conn):
            task = self.controller.get_task(self.task_id)
            self._approved_decision_ids()
            if task.phase is not Phase.INTERVIEW:
                hint = (
                    f"harness phase {task.id} next"
                    if task.phase is Phase.INVESTIGATION
                    else f'harness phase {task.id} back interview --reason "<why>"'
                )
                raise InterviewError(f"task {task.id} is in phase {task.phase}; run: {hint}")
            yield self._sync(task)
            self.ws.checkpoint("interview", self._checkpoint_payload())
            self.conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?", (utcnow(), self.task_id)
            )

    def _approved_decision_ids(self) -> set[str]:
        checkpoint = self.ws.latest_checkpoint("investigation")
        if checkpoint is None:
            raise InterviewError(f"run harness investigate {self.task_id} first")
        decisions: list[dict[str, Any]] = checkpoint.payload.get("decisions", [])
        return {d["id"] for d in decisions if d.get("status") == "approved"}

    def _open(self, question_id: int) -> Question:
        question = self.ws.question(question_id)
        if question.status is not QuestionStatus.OPEN:
            raise InterviewError(f"question {question_id} is already {question.status}")
        return question

    def _sync(self, task: Task) -> tuple[int, int]:
        plan = plan_sync(self.ws.questions(), catalogue_for(self._topics(task)))
        for entry in plan.seed:
            self.ws.ask(entry.topic, entry.text, kind=entry.kind, catalogue_key=entry.key)
        for question in plan.skip:
            reason = f"{DEPTH_SKIP_REASON} for {task.complexity}"
            self.ws.skip(question.id, reason, SkipSource.NOT_RELEVANT)
        for question in plan.reopen:
            self.ws.reopen(question.id)
        return len(plan.seed) + len(plan.reopen), len(plan.skip)

    def _askable(self) -> list[Question]:
        deferred = {e.payload.get("id") for e in self.ws.events() if e.kind == UNRESOLVED_EVENT}
        return [q for q in ordered(self.ws.open_questions()) if q.id not in deferred]

    def _checkpoint_payload(self) -> dict[str, Any]:
        upcoming = self._askable()
        checklist = build_checklist(self.ws.questions(), self.ws.proposals(), None)
        return {
            "next_question_id": upcoming[0].id if upcoming else None,
            "unresolved": [item.ref for item in checklist.items],
            "blocking": checklist.blocking,
        }
