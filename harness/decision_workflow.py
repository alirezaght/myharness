"""Decision workflow: record approved decisions, preview and approve changes, read-only hints.

Every write to the registry happens inside the task's DB transaction, after the developer's
approval, so a failed file write rolls the approval back and nothing is half-recorded.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from harness.controller import HarnessController
from harness.db import transaction, utcnow
from harness.decision_suggest import Suggestion, suggest
from harness.decisions import NOT_STATED_IMPACT, DecisionRecord, DecisionStatus
from harness.registry import DecisionRegistry
from harness.state import TaskWorkspace
from harness.state_decisions import ChangePreview, ChangeStatus, DecisionChange
from harness.state_models import (
    Proposal,
    ProposalStatus,
    Question,
    QuestionKind,
    QuestionStatus,
    SkipSource,
)

RECORDED_EVENT = "decision.recorded"


@contextmanager
def discard_on_failure() -> Iterator[list[DecisionRecord]]:
    """Delete the record files written inside a block whose transaction did not commit."""
    written: list[DecisionRecord] = []
    try:
        yield written
    except BaseException:
        for record in written:
            record.path.unlink(missing_ok=True)
        raise


def _modified_rationale(rationale: str, original: str, what: str) -> str:
    return f"{rationale}\n\nModified by the developer from the {what}: {original}"


def record_approved_proposal(
    ws: TaskWorkspace, registry: DecisionRegistry, proposal: Proposal, question: Question
) -> DecisionRecord | None:
    """Record an accepted/modified recommendation on a decision question; nothing otherwise."""
    if question.kind is not QuestionKind.DECISION:
        return None
    if proposal.status is ProposalStatus.ACCEPTED:
        content, rationale = proposal.recommendation, proposal.rationale
    elif proposal.status is ProposalStatus.MODIFIED:
        content = proposal.response or ""
        rationale = _modified_rationale(
            proposal.rationale, proposal.recommendation, "recommendation"
        )
    else:
        return None
    impact = proposal.impact or NOT_STATED_IMPACT
    record = registry.record(proposal.title, content, rationale, question.topic, impact)
    ws.record_event(
        RECORDED_EVENT,
        {
            "id": record.id,
            "version": record.version,
            "path": str(record.path),
            "source": "interview",
            "proposal": proposal.id,
        },
    )
    return record


class DecisionWorkflow:
    def __init__(self, controller: HarnessController, task_id: str, registry: DecisionRegistry):
        self.controller = controller
        self.conn = controller.conn
        self.task_id = controller.get_task(task_id).id
        self.ws = controller.workspace(task_id)
        self.registry = registry

    def record(
        self,
        title: str,
        content: str,
        rationale: str,
        scope: str | None = None,
        impact: str | None = None,
        source: str = "developer",
        proposal_id: int | None = None,
    ) -> DecisionRecord:
        with discard_on_failure() as written, self._mutation():
            record = self.registry.record(title, content, rationale, scope, impact)
            written.append(record)
            payload = {"id": record.id, "version": record.version, "path": str(record.path)}
            payload |= {"source": source, "proposal": proposal_id}
            self.ws.record_event(RECORDED_EVENT, payload)
        return record

    def change(
        self,
        decision_id: str,
        content: str,
        rationale: str,
        impact: str,
        title: str | None = None,
        scope: str | None = None,
    ) -> tuple[DecisionChange, ChangePreview]:
        """Store a proposed new version and show what it replaces; writes no record file."""
        with self._mutation():
            current = self._approved(decision_id)
            for pending in self.ws.decision_changes(ChangeStatus.PENDING):
                if pending.decision_id == current.id:
                    raise ValueError(
                        f"{current.id} already has a pending change {pending.ref}; decide it first"
                    )
            change = self.ws.propose_change(
                current.id,
                current.version,
                (title or "").strip() or current.title,
                content,
                rationale,
                (scope or "").strip() or current.scope,
                impact,
            )
            self.ws.record_event(
                "decision.change_proposed",
                {"change": change.id, "id": current.id, "base_version": current.version},
            )
        return change, self.preview(change)

    def preview(self, change: DecisionChange) -> ChangePreview:
        current = self.registry.get(change.decision_id)
        skipped = tuple(
            q.id
            for q in self.ws.questions(QuestionStatus.SKIPPED)
            if q.skip_source is SkipSource.DECISION and q.skip_reason == current.id
        )
        return ChangePreview(current, change, skipped, tuple(self.registry.references(current.id)))

    def decide(
        self, change_id: int, status: ChangeStatus, response: str | None = None
    ) -> tuple[DecisionChange, DecisionRecord | None]:
        """The developer's verdict; only accept/modify supersede the record on disk."""
        with self._mutation():
            change = self.ws.resolve_change(change_id, status, response)
            if change.status is ChangeStatus.REJECTED:
                self.ws.record_event(
                    "decision.change_rejected", {"change": change.id, "id": change.decision_id}
                )
                return change, None
            rationale = change.rationale
            if change.status is ChangeStatus.MODIFIED:
                rationale = _modified_rationale(rationale, change.content, "proposed change")
            record = self.registry.supersede(
                change.decision_id,
                change.title,
                change.text,
                rationale,
                change.scope,
                change.impact,
                expected_version=change.base_version,
            )
            change = self.ws.set_change_result(change.id, str(record.path))
            self.ws.record_event(
                "decision.superseded",
                {
                    "change": change.id,
                    "id": record.id,
                    "from_version": change.base_version,
                    "version": record.version,
                    "path": str(record.path),
                },
            )
        return change, record

    def suggest(self, sources: list[Path]) -> tuple[list[Suggestion], list[str]]:
        """Read-only: no DB row, no event, no file."""
        task = self.controller.get_task(self.task_id)
        open_decisions = [
            q.text for q in self.ws.open_questions() if q.kind is QuestionKind.DECISION
        ]
        return suggest(task.request, open_decisions, sources, exclude=self.registry.directory)

    def _approved(self, decision_id: str) -> DecisionRecord:
        current = self.registry.get(decision_id)
        if current.status is not DecisionStatus.APPROVED:
            raise ValueError(
                f"{current.id} is {current.status}; only approved decisions can be changed"
            )
        return current

    @contextmanager
    def _mutation(self) -> Iterator[None]:
        with transaction(self.conn):
            self.controller.get_task(self.task_id)
            yield
            self.conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?", (utcnow(), self.task_id)
            )
