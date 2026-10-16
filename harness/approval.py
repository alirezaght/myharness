"""Approve & Implement (SPEC §3 Phase 7): the approval record is the only handoff authorization.

`harness approve` seals a new spec version and, in the same transaction, records an immutable
approval bound to that version, its file, revision, content hash and the final review state.
Validity is derived, never stored: `require()` re-checks every binding each time it is called.
"""

from pathlib import Path
from typing import Any, NoReturn

from harness.controller import HarnessController
from harness.phases import Phase
from harness.review import FinalReviewer
from harness.review_report import ReviewReport
from harness.spec_manager import SpecificationManager
from harness.spec_record import SpecRecord, SpecStatus, content_hash, parse_spec
from harness.spec_store import SpecStore
from harness.state_approvals import ApprovalRecord

__all__ = ["GRANTED_EVENT", "ApprovalGate", "NotApproved", "require_approval"]

GRANTED_EVENT = "approval.granted"
CHECKPOINT_LABEL = "approval"
PHASE_EVENT = "phase.changed"


class NotApproved(ValueError):
    def __init__(self, task_id: str, reason: str):
        self.task_id = task_id
        self.reason = reason
        super().__init__(f"task {task_id} is not approved for implementation: {reason}")


class ApprovalGate:
    def __init__(
        self, controller: HarnessController, task_id: str, store: SpecStore, repo_root: Path
    ):
        self.controller = controller
        self.manager = SpecificationManager(controller, task_id, store, repo_root)
        self.task_id = self.manager.task_id
        self.ws = self.manager.ws
        self._granted: ApprovalRecord | None = None

    def approve(self) -> tuple[ApprovalRecord, SpecRecord, SpecRecord | None]:
        """Seal v<N>.md and record the approval in one transaction; nothing is kept on failure."""
        self._granted = None
        record, previous = self.manager.approve(on_sealed=self._record)
        if self._granted is None:
            raise RuntimeError("the specification was sealed without an approval record")
        return self._granted, record, previous

    def _record(self, payload: dict[str, Any], report: ReviewReport) -> None:
        approval = self.ws.record_approval(
            payload["version"],
            payload["path"],
            payload["revision"],
            payload["content_hash"],
            report.state_hash,
        )
        data = {
            "approval_id": approval.id,
            "spec_version": approval.spec_version,
            "path": approval.path,
            "revision": approval.revision,
            "content_hash": approval.content_hash,
            "review_state_hash": approval.review_state_hash,
        }
        self.ws.record_event(GRANTED_EVENT, data)
        self.ws.checkpoint(CHECKPOINT_LABEL, data)
        self._granted = approval

    def require(self) -> ApprovalRecord:
        """The one handoff guard: the latest approval, or NotApproved with the first reason."""
        record = self.ws.latest_approval()
        if record is None:
            self._refuse(f"no approval record; run: harness approve {self.task_id}")
        self._check_phase()
        self._check_spec(record)
        self._check_state(record)
        return record

    def status(self) -> str:
        record = self.ws.latest_approval()
        if record is None:
            return "none"
        label = f"spec v{record.spec_version} {record.content_hash}"
        try:
            self.require()
        except NotApproved as error:
            return f"{label} (stale: {error.reason})"
        except (ValueError, OSError) as error:
            return f"{label} (stale: {error})"
        return f"{label} (current)"

    def _refuse(self, reason: str) -> NoReturn:
        raise NotApproved(self.task_id, reason)

    def _check_phase(self) -> None:
        phase = self.controller.get_task(self.task_id).phase
        again = f"run: harness approve {self.task_id} again"
        if phase is not Phase.APPROVAL:
            self._refuse(
                f"the task is in phase {phase}, not approval; {again} once it is back in approval"
            )
        granted = [e.id for e in self.ws.events() if e.kind == GRANTED_EVENT]
        last = granted[-1] if granted else None
        moved = [e for e in self.ws.events() if e.kind == PHASE_EVENT]
        if last is None or any(e.id > last for e in moved):
            self._refuse(
                f"the task left the approval phase after the approval was granted; {again}"
            )

    def _check_spec(self, record: ApprovalRecord) -> None:
        number, again = record.spec_version, f"run: harness approve {self.task_id} again"
        row = self.ws.spec_version(number)
        if row is None:
            self._refuse(f"spec v{number} is not recorded as approved; {again}")
        if row.status is not SpecStatus.APPROVED:
            self._refuse(f"spec v{number} was superseded by v{row.superseded_by}; {again}")
        try:
            sealed = parse_spec(Path(record.path))
        except (ValueError, OSError) as error:
            self._refuse(f"the sealed specification {record.path} cannot be read: {error}")
        intact = (
            sealed.version == number
            and sealed.status is SpecStatus.APPROVED
            and sealed.content_hash == record.content_hash
            and content_hash(sealed.body) == record.content_hash
            and sealed.revision == record.revision
        )
        if not intact:
            self._refuse(
                f"the sealed specification {record.path} was modified after approval"
                f" (run: harness spec verify {self.task_id})"
            )

    def _check_state(self, record: ApprovalRecord) -> None:
        if content_hash(self.manager.body()) != record.content_hash:
            self._refuse(
                "the specification content changed since approval;"
                f" run: harness approve {self.task_id} again"
            )
        if FinalReviewer(self.controller, self.task_id).report().state_hash != (
            record.review_state_hash
        ):
            self._refuse(
                "the final design review state changed since approval"
                f" (e.g. a pending decision change); run: harness review {self.task_id}"
            )


def require_approval(
    controller: HarnessController, task_id: str, store: SpecStore, repo_root: Path
) -> ApprovalRecord:
    """Every implementation handoff (the Phase 3 AL adapter, P3-04) must obtain the spec path,
    version and hash from this function and nothing else; it raises NotApproved otherwise."""
    return ApprovalGate(controller, task_id, store, repo_root).require()
