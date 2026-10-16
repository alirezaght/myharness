"""Final Design Review gate (SPEC §3 Phase 6): no task enters approval while an item blocks."""

from harness.controller import HarnessController
from harness.design import ArchitectureDesigner
from harness.interview import InterviewEngine
from harness.review_report import ReviewReport, build_report
from harness.state_decisions import ChangeStatus
from harness.testplan import TestPlanner

__all__ = ["PASSED_EVENT", "FinalReviewer", "ReviewBlocked", "pass_gate"]

PASSED_EVENT = "final_review.passed"
CHECKPOINT_LABEL = "final_review"


class ReviewBlocked(ValueError):
    def __init__(self, report: ReviewReport):
        self.report = report
        count = len(report.blocked)
        super().__init__(
            f"task {report.task_id} cannot enter approval: {count} item(s) block the final"
            f" design review\n{report.render()}"
        )


class FinalReviewer:
    def __init__(self, controller: HarnessController, task_id: str):
        self.controller = controller
        self.task_id = controller.get_task(task_id).id
        self.ws = controller.workspace(task_id)

    def report(self) -> ReviewReport:
        """Read-only: derived from the interview, design, test plan and decision changes."""
        task = self.controller.get_task(self.task_id)
        summary = InterviewEngine(self.controller, self.task_id).summary()
        coverage = ArchitectureDesigner(self.controller, self.task_id).coverage()
        plan = TestPlanner(self.controller, self.task_id).document()
        pending = self.ws.decision_changes(ChangeStatus.PENDING)
        return build_report(self.task_id, summary, coverage, plan, pending, task.phase)

    def record_pass(self, report: ReviewReport) -> None:
        """Event and checkpoint inside the caller's transaction (before the phase change)."""
        payload = report.checkpoint_payload()
        self.ws.record_event(
            PASSED_EVENT, {"state_hash": report.state_hash, "items": payload["items"]}
        )
        self.ws.checkpoint(CHECKPOINT_LABEL, payload)


def pass_gate(controller: HarnessController, task_id: str) -> ReviewReport:
    reviewer = FinalReviewer(controller, task_id)
    report = reviewer.report()
    if report.blocking:
        raise ReviewBlocked(report)
    reviewer.record_pass(report)
    return report
