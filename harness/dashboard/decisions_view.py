"""The decisions view (P2-03): approved decisions and open decisions, listed separately.

Approved: every current record of the task's registry with status approved. Open: what the
engines still wait on the developer for — decision questions and pending recommendations from
the interview checklist, pending design entries, test scenarios and decision changes.
"""

from typing import Any

from harness.cli_decision import registry_for
from harness.controller import HarnessController, Task
from harness.dashboard.overview import _decide
from harness.decisions import DecisionStatus
from harness.design_models import EntryStatus
from harness.interview import InterviewEngine
from harness.interview_report import ChecklistState
from harness.investigation import _decision_dict
from harness.spec_manager import RECORDED_EVENTS
from harness.state_decisions import ChangeStatus
from harness.state_models import QuestionKind

AWAITING = str(ChecklistState.AWAITING_DECISION)


def _open(
    area: str, ref: str, state: str, text: str, command: str | None, recommendation: str | None
) -> dict[str, Any]:
    return {
        "area": area,
        "ref": ref,
        "state": state,
        "text": text,
        "recommendation": recommendation,
        "command": command,
    }


def _command(area: str, task_id: str, record_id: int) -> str:
    return _decide("", "", "", area, task_id, record_id)["command"]


def _interview_items(controller: HarnessController, task: Task) -> list[dict[str, Any]]:
    proposals = {p.id: p for p in controller.workspace(task.id).proposals()}
    items = []
    for item in InterviewEngine(controller, task.id).checklist().items:
        awaiting = item.state is ChecklistState.AWAITING_DECISION
        if not awaiting and item.kind != QuestionKind.DECISION:
            continue
        proposal = proposals.get(item.proposal_id) if item.proposal_id is not None else None
        recommendation = command = None
        if proposal is not None and item.question_id is not None:
            recommendation = f"P{proposal.id} {proposal.title}: {proposal.recommendation}"
        if proposal is not None and awaiting:
            command = _command("interview", task.id, proposal.id)
        items.append(
            _open("interview", item.ref, str(item.state), item.text, command, recommendation)
        )
    return items


def open_decisions(controller: HarnessController, task: Task) -> list[dict[str, Any]]:
    ws, tid = controller.workspace(task.id), task.id
    items = _interview_items(controller, task)
    items += [
        _open("design", e.ref, AWAITING, e.statement, _command("design", tid, e.id), None)
        for e in ws.design_entries(EntryStatus.PENDING)
    ]
    items += [
        _open("testplan", s.ref, AWAITING, s.statement, _command("testplan", tid, s.id), None)
        for s in ws.scenarios(EntryStatus.PENDING)
    ]
    items += [
        _open(
            "decision",
            c.ref,
            str(ChangeStatus.PENDING),
            f"{c.decision_id} v{c.base_version} -> v{c.base_version + 1}: {c.title}",
            _command("decision", tid, c.id),
            None,
        )
        for c in ws.decision_changes(ChangeStatus.PENDING)
    ]
    return items


def decisions_section(controller: HarnessController, task: Task) -> dict[str, Any]:
    """A malformed decision record marks the approved list unavailable; the rest still renders."""
    registry = registry_for(controller, task.id, None)
    recorded = {
        (event.payload.get("id"), event.payload.get("version"))
        for event in controller.workspace(task.id).events()
        if event.kind in RECORDED_EVENTS
    }
    section: dict[str, Any] = {
        "directory": str(registry.directory),
        "approved": [],
        "unavailable": None,
        "open": open_decisions(controller, task),
    }
    try:
        records = registry.records()
    except (OSError, ValueError) as error:
        section["unavailable"] = str(error)
        return section
    section["approved"] = [
        _decision_dict(r)
        | {"decision": r.decision, "recorded_by_task": (r.id, r.version) in recorded}
        for r in records
        if r.status is DecisionStatus.APPROVED
    ]
    return section
