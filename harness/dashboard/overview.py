"""The task overview (SPEC §8.1, §8.9): status, pending approvals, latest activity, resume point.

Built only from the engines the CLI uses, so every value matches the CLI's own output.
"""

import sqlite3
from typing import Any

from harness.cli_commands import task_repo_root
from harness.cli_spec import spec_store
from harness.controller import HarnessController, Task
from harness.design_models import EntryStatus
from harness.phases import Phase
from harness.resume import CURRENT, ResumePoint, resume_point
from harness.spec_store import SpecStore
from harness.state_decisions import ChangeStatus
from harness.state_models import ProposalStatus
from harness.testplan import TestPlanner
from harness.testplan_report import PLAN_REF

APPROVED = "approved"
AWAITING_APPROVAL = "awaiting approval"
AWAITING_ANSWER = "awaiting answer"
IN_PROGRESS = "in progress"
UNAVAILABLE = "unavailable"
ROW_ERRORS = (sqlite3.Error, OSError, ValueError, LookupError)
VERDICTS = "accept|reject|modify"


def resume_for(controller: HarnessController, task: Task, store: SpecStore) -> ResumePoint:
    return resume_point(controller, task.id, store, task_repo_root(controller, task.id))


def _item(kind: str, ref: str, title: str, command: str) -> dict[str, str]:
    return {"kind": kind, "ref": ref, "title": title, "command": command}


def _decide(
    kind: str, ref: str, title: str, area: str, task_id: str, record_id: int
) -> dict[str, str]:
    return _item(kind, ref, title, f"harness {area} decide {task_id} {record_id} {VERDICTS}")


def pending_approvals(
    controller: HarnessController, task: Task, point: ResumePoint
) -> list[dict[str, str]]:
    """Everything waiting on the developer, with the CLI command that decides it (read-only)."""
    tid, ws = task.id, controller.workspace(task.id)
    items = [
        _decide("interview recommendation", f"P{p.id}", p.title, "interview", tid, p.id)
        for p in ws.proposals(ProposalStatus.PENDING)
    ]
    items += [
        _decide("design recommendation", e.ref, e.statement, "design", tid, e.id)
        for e in ws.design_entries(EntryStatus.PENDING)
    ]
    items += [
        _decide("test scenario", s.ref, s.statement, "testplan", tid, s.id)
        for s in ws.scenarios(EntryStatus.PENDING)
    ]
    items += [
        _decide("decision change", c.ref, f"{c.decision_id}: {c.title}", "decision", tid, c.id)
        for c in ws.decision_changes(ChangeStatus.PENDING)
    ]
    if task.phase is Phase.TEST_PLAN:
        items += [
            _item("test plan", PLAN_REF, str(i.state), f"harness testplan approve {tid}")
            for i in TestPlanner(controller, tid).review().items
            if i.ref == PLAN_REF
        ]
    if task.phase is Phase.APPROVAL and not (point.approval or "").endswith(CURRENT):
        items.append(
            _item("final approval", "spec", f"approval: {point.approval}", f"harness approve {tid}")
        )
    return items


def task_status(point: ResumePoint, pending: list[dict[str, str]]) -> str:
    if not point.resumable:
        return APPROVED
    if pending:
        return AWAITING_APPROVAL
    if point.pending_question is not None:
        return AWAITING_ANSWER
    return IN_PROGRESS


def latest_activity(controller: HarnessController, task: Task) -> dict[str, str] | None:
    event = controller.workspace(task.id).latest_event()
    return None if event is None else {"kind": event.kind, "at": event.created_at}


def overview(controller: HarnessController, task: Task, store: SpecStore) -> dict[str, Any]:
    point = resume_for(controller, task, store)
    pending = pending_approvals(controller, task, point)
    return {
        "created_at": task.created_at,
        "status": task_status(point, pending),
        "pending_approvals": pending,
        "latest_activity": latest_activity(controller, task),
        "resume": point.to_dict(),
    }


def task_row(controller: HarnessController, task: Task, specs: str | None) -> dict[str, Any]:
    """One task-list row; a task whose state cannot be read is marked, never fails the list."""
    row: dict[str, Any] = {
        "id": task.id,
        "request": task.request,
        "phase": str(task.phase),
        "updated_at": task.updated_at,
    }
    try:
        point = resume_for(controller, task, spec_store(controller, task.id, specs))
        status = task_status(point, pending_approvals(controller, task, point))
    except ROW_ERRORS as error:
        return row | {"status": f"{UNAVAILABLE}: {error}", "next": None}
    return row | {"status": status, "next": point.next_command}
