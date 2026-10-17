"""The Approval Center (SPEC §8.7, P2-07): every pending approval in one place, read-only.

The groups are the overview's pending approvals split into decisions, test plan and final spec.
`final` describes the Approve & Implement button: the tokens the page sends back (diagram
version, final-review state hash, hash of the specification body to be sealed) and the reasons
it is disabled. The server re-checks every one of them inside the approval transaction.
"""

from typing import Any

from harness.cli_commands import task_repo_root
from harness.controller import HarnessController, Task
from harness.dashboard.markdown import parse_markdown
from harness.dashboard.overview import ROW_ERRORS
from harness.diagrams import DiagramService
from harness.phases import Phase
from harness.resume import CURRENT
from harness.review import FinalReviewer
from harness.review_report import Check, ReviewReport, ReviewState
from harness.spec_manager import SpecificationManager
from harness.spec_record import content_hash
from harness.spec_store import SpecStore
from harness.state_comments import CommentStatus

GROUPS = {
    "decisions": frozenset(
        {"interview recommendation", "design recommendation", "decision change"}
    ),
    "test_plan": frozenset({"test scenario", "test plan"}),
    "final_spec": frozenset({"final approval"}),
}


def approval_center(
    controller: HarnessController,
    task: Task,
    store: SpecStore,
    pending: list[dict[str, str]],
    approval: str | None,
) -> dict[str, Any]:
    groups = {name: [i for i in pending if i["kind"] in kinds] for name, kinds in GROUPS.items()}
    return {"groups": groups, "final": _final(controller, task, store, approval)}


def _final(
    controller: HarnessController, task: Task, store: SpecStore, approval: str | None
) -> dict[str, Any]:
    base = {
        "phase": str(task.phase),
        "status": approval,
        "command": f"harness approve {task.id}",
        "version": None,
        "review_hash": None,
        "spec_hash": None,
        "preview": None,
        "comments": {"open": 0, "addressed": 0},
    }
    try:
        service = DiagramService(controller, task.id)
        latest = service.latest()
        comments = service.ws.comments()
        counts = {
            "open": sum(c.status == CommentStatus.OPEN for c in comments),
            "addressed": sum(c.status == CommentStatus.ADDRESSED for c in comments),
        }
        report = FinalReviewer(controller, task.id).report()
        spec_hash = preview = None
        if task.phase is Phase.APPROVAL:
            root = task_repo_root(controller, task.id)
            body = SpecificationManager(controller, task.id, store, root).body()
            spec_hash, preview = content_hash(body), parse_markdown(body)
        stale = DiagramService.needs(service.model(), latest)
        blockers = _blockers(task, approval, stale, counts, report)
    except ROW_ERRORS as error:
        text = f"approval state unavailable: {error}"
        return base | {"enabled": False, "blockers": [{"code": "unavailable", "text": text}]}
    return base | {
        "version": latest.version if latest else None,
        "review_hash": report.state_hash,
        "spec_hash": spec_hash,
        "preview": preview,
        "comments": counts,
        "blockers": blockers,
        "enabled": not blockers,
    }


def _blockers(
    task: Task, approval: str | None, stale: bool, counts: dict[str, int], report: ReviewReport
) -> list[dict[str, str]]:
    found: list[tuple[str, str]] = []
    if task.phase is not Phase.APPROVAL:
        found.append(
            (
                "phase",
                f"the task is in phase {task.phase}; Approve & Implement opens in the approval"
                " phase",
            )
        )
    if (approval or "").endswith(CURRENT):
        found.append(("approved", f"already approved: {approval}"))
    if stale:
        found.append(
            ("stale", "the design changed and its new diagram version is not recorded yet; reload")
        )
    unresolved = counts["open"] + counts["addressed"]
    if unresolved:
        found.append(
            (
                "comments",
                f"{unresolved} diagram comment(s) not resolved"
                f" (open {counts['open']} · addressed {counts['addressed']})",
            )
        )
    for item in report.items:
        if item.check is Check.NO_BLOCKING_QUESTIONS and item.state is ReviewState.BLOCKED:
            found.append(("questions", "blocking questions remain: " + "; ".join(item.findings)))
    if report.blocking:
        titles = "; ".join(item.title for item in report.blocked)
        found.append(
            ("review", f"the final design review fails (harness review {task.id}): {titles}")
        )
    return [{"code": code, "text": text} for code, text in found]
