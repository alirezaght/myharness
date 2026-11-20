"""Interview Engine (SPEC §3 Phase 2): question state, depth, decision gating, no invention."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from harness.classification import interview_depth
from harness.controller import HarnessController, Task
from harness.db import transaction, utcnow
from harness.decision_workflow import discard_on_failure, record_approved_proposal
from harness.decisions import DecisionRecord
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
from harness.registry import DecisionRegistry
from harness.state_models import (
    Proposal,
    ProposalStatus,
    Question,
    QuestionKind,
    QuestionSource,
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


def scanner_scope(question: Question) -> bool:
    """Only equivalent scanner material requests share one ask-next slot."""
    return (
        question.topic == "scope"
        and question.source is QuestionSource.INVESTIGATION
        and (
            question.text.startswith(
                (
                    "Ownership is unresolved;",
                    "Several packages found",
                    "No filename matches",
                    "No file matches",
                    "Request has no usable keywords;",
                    "Repository inventory reached its bound;",
                )
            )
            or "A ticket ID alone does not include its requirements." in question.text
        )
    )


class InterviewEngine:
    def __init__(
        self,
        controller: HarnessController,
        task_id: str,
        registry: DecisionRegistry | None = None,
    ):
        self.controller = controller
        self.conn = controller.conn
        self.task_id = controller.get_task(task_id).id
        self.ws = controller.workspace(task_id)
        self.registry = registry
        self.last_record: DecisionRecord | None = None

    # Read-only views

    def effective(self):
        from harness.effective_records import effective_records

        return effective_records(self.ws)

    def effective_questions(self, status=None):
        retired = set(self.coverage().retired_ids)
        return [
            q
            for q in self.effective().questions
            if q.id not in retired and (status is None or q.status == status)
        ]

    def effective_proposals(self, status=None):
        retired = set(self.coverage().retired_ids)
        return [
            p
            for p in self.effective().proposals
            if p.question_id not in retired and (status is None or p.status == status)
        ]

    def coverage(self):
        from harness.interview_coverage import current_coverage

        return current_coverage(self.controller, self.task_id)

    def checklist(self) -> InterviewChecklist:
        task = self.controller.get_task(self.task_id)
        pending = plan_sync(
            self.effective_questions(), catalogue_for(self._topics(task)), self.coverage()
        )
        return build_checklist(
            self.effective_questions(), self.effective_proposals(), pending, self.coverage()
        )

    def summary(self) -> InterviewSummary:
        return build_summary(
            self.effective_questions(),
            self.effective_proposals(),
            self.checklist(),
            self.coverage(),
        )

    def upcoming(self) -> Question | None:
        """The question `next()` asks (or re-asks) from committed state; no sync, no write."""
        askable = self._askable()
        return askable[0] if askable else None

    def question_projection(self):
        """Read-only ask-next state; unknown remains open and required in the checklist."""
        unknown = {
            event.payload.get("id") for event in self.ws.events() if event.kind == UNRESOLVED_EVENT
        }
        current = self.upcoming()
        scope = [q for q in self.effective_questions() if scanner_scope(q)]
        response = next((q for q in scope if q.answer or q.skip_source or q.id in unknown), None)
        return {
            "coverage": self.coverage().to_dict(),
            "related_scope_question_ids": [q.id for q in scope],
            "scope_response_question_id": response.id if response else None,
            "current_question_id": current.id if current else None,
            "unknown_question_ids": [
                q.id for q in self.effective_questions(QuestionStatus.OPEN) if q.id in unknown
            ],
        }

    def sync_due(self) -> bool:
        """True when `next()` would first seed, skip or reopen catalogue questions."""
        task = self.controller.get_task(self.task_id)
        plan = plan_sync(
            self.effective_questions(), catalogue_for(self._topics(task)), self.coverage()
        )
        return bool(plan.seed or plan.skip or plan.reopen)

    def pending_proposals(self, question_id: int) -> list[Proposal]:
        return [
            p
            for p in self.effective_proposals(ProposalStatus.PENDING)
            if p.question_id == question_id
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
            self.effective().require_current("question", question_id)
            self._require_source_current(question_id)
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
        self,
        question_id: int,
        title: str,
        recommendation: str,
        rationale: str,
        impact: str | None = None,
    ) -> Proposal:
        with self._mutation():
            self._open(question_id)
            self.ws.mark_asked(question_id)
            proposal = self.ws.propose(title, recommendation, rationale, question_id, impact)
            self.ws.record_event("proposal.created", {"id": proposal.id, "question": question_id})
        return proposal

    def decide(self, proposal_id: int, status: ProposalStatus, response: str | None) -> Proposal:
        """Resolve a recommendation; an approved decision question is recorded (last_record)."""
        self.last_record = None
        with discard_on_failure() as written, self._mutation():
            effective = self.effective()
            effective.require_current("proposal", proposal_id)
            current = next(
                (p for p in effective.proposals if p.id == proposal_id),
                self.ws.proposal(proposal_id),
            )
            proposal = self.ws.resolve(proposal_id, status, response)
            qid = current.question_id
            self._require_source_current(qid)
            proposal = replace(proposal, question_id=qid)
            if proposal.status in DECIDED and qid is not None:
                question = next(
                    (q for q in effective.questions if q.id == qid), self.ws.question(qid)
                )
                if question.status is QuestionStatus.OPEN:
                    self.ws.mark_decided(qid)
                if self.registry is not None:
                    record = record_approved_proposal(self.ws, self.registry, proposal, question)
                    written.extend([record] if record else [])
            self.ws.record_event(
                "proposal.decided", {"id": proposal_id, "status": proposal.status, "question": qid}
            )
        self.last_record = written[0] if written else None
        return proposal

    # Internals

    @staticmethod
    def _topics(task: Task) -> tuple[str, ...]:
        return interview_depth(task.complexity).topics

    @contextmanager
    def _mutation(self) -> Iterator[tuple[int, int]]:
        with transaction(self.conn), self.mutation_in_transaction() as result:
            yield result

    @contextmanager
    def mutation_in_transaction(self) -> Iterator[tuple[int, int]]:
        if not self.conn.in_transaction:
            raise RuntimeError("existing transaction required")
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
        self.conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (utcnow(), self.task_id))

    def _approved_decision_ids(self) -> set[str]:
        checkpoint = self.ws.latest_checkpoint("investigation")
        if checkpoint is None:
            raise InterviewError(f"run harness investigate {self.task_id} first")
        from harness.current_decisions import current_decisions

        decisions: list[dict[str, Any]] = current_decisions(self.ws, self.registry)
        return {d["id"] for d in decisions if d.get("status") == "approved"}

    def _require_source_current(self, question_id):
        if question_id in self.coverage().retired_ids:
            raise InterviewError(
                "This decision has a changed source premise; use its current successor"
            )

    def _open(self, question_id: int) -> Question:
        self._require_source_current(question_id)
        effective = self.effective()
        effective.require_current("question", question_id)
        question = next(
            (q for q in effective.questions if q.id == question_id), self.ws.question(question_id)
        )
        if question.status is not QuestionStatus.OPEN:
            raise InterviewError(f"question {question_id} is already {question.status}")
        return question

    def _sync(self, task: Task) -> tuple[int, int]:
        plan = plan_sync(
            self.effective_questions(), catalogue_for(self._topics(task)), self.coverage()
        )
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
        scope = [q for q in self.effective_questions() if scanner_scope(q)]
        responded = any(q.answer or q.skip_source or q.id in deferred for q in scope)
        first = next((q.id for q in ordered(scope) if q.status is QuestionStatus.OPEN), None)
        coverage = self.coverage()
        from harness.planning_session_events import native_binding

        owner = native_binding(self.controller.conn, self.task_id)
        attached = owner is not None and owner["mode"] == "attached"
        return [
            q
            for q in ordered(self.effective_questions(QuestionStatus.OPEN))
            if not coverage.suppress(q)
            and (
                not attached
                or (
                    not q.catalogue_key
                    and q.source is not QuestionSource.INVESTIGATION
                    and not scanner_scope(q)
                )
            )
            and q.id not in deferred
            and (q not in scope or (not responded and q.id == first))
        ]

    def _checkpoint_payload(self) -> dict[str, Any]:
        upcoming = self._askable()
        checklist = build_checklist(
            self.effective_questions(), self.effective_proposals(), None, self.coverage()
        )
        return {
            "next_question_id": upcoming[0].id if upcoming else None,
            "unresolved": [item.ref for item in checklist.items],
            "blocking": checklist.blocking,
        }
