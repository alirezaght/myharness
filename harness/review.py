"""Final Design Review gate (SPEC §3 Phase 6): no task enters approval while an item blocks."""

from harness.controller import HarnessController
from harness.design import ArchitectureDesigner
from harness.interview import InterviewEngine
from harness.phases import Phase
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
        report = build_report(self.task_id, summary, coverage, plan, pending, task.phase)
        from dataclasses import replace

        from harness.authoring_contract import digest
        from harness.authoring_store import adopted_revision, source_state
        from harness.interview_coverage import current_coverage
        from harness.planning_session_material import projection
        from harness.review_report import Check, ReviewItem, ReviewState

        material = projection(self.controller.conn, self.task_id, include_inputs=False)
        report = replace(
            report,
            signals=report.signals
            | {
                "session_material": {
                    key: material[key]
                    for key in (
                        "material_revision",
                        "material_digest",
                        "pending_material_ids",
                        "handling_digest",
                    )
                }
            },
        )
        if material["pending_material_ids"]:
            report = replace(
                report,
                items=report.items
                + (
                    ReviewItem(
                        Check.NO_BLOCKING_QUESTIONS,
                        ReviewState.BLOCKED,
                        (
                            (
                                "Pending material session inputs require a validated response or "
                                "explicit reconciliation"
                            ),
                        ),
                        Phase.INTERVIEW,
                    ),
                ),
            )
        sources = current_coverage(self.controller, self.task_id)
        if sources.enabled:
            findings = tuple(f"{row['state']}: {row['text']}" for row in sources.blockers)
            if not sources.accepted and not findings:
                findings = ("Exact source coverage acceptance is required",)
            report = replace(
                report,
                signals=report.signals | {"investigation": sources.to_dict()},
                items=report.items
                + (
                    (
                        ReviewItem(
                            Check.NO_BLOCKING_QUESTIONS,
                            ReviewState.BLOCKED,
                            findings,
                            Phase.INTERVIEW,
                        ),
                    )
                    if findings
                    else ()
                ),
            )
        adopted = adopted_revision(self.controller, self.task_id)
        if adopted is None:
            return report
        findings = list(adopted["obligations"])
        if adopted["source_digest"] != digest(source_state(self.controller, self.task_id)):
            findings.append("Adopted specification source changed; reconcile explicitly")
        if not adopted["body"].startswith("## "):
            findings.append('Adopted specification must begin with "## "')
        signals = report.signals | {
            "authoring": {
                "revision": adopted["id"],
                "body": adopted["body_hash"],
                "graph": adopted["graph_id"],
                "source": adopted["source_digest"],
            }
        }
        items = report.items
        if findings:
            items += (
                ReviewItem(
                    Check.ARCHITECTURE_CONSISTENT,
                    ReviewState.BLOCKED,
                    tuple(findings),
                    Phase.FINAL_REVIEW,
                ),
            )
        return replace(report, items=items, signals=signals)

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
