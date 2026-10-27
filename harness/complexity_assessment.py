"""Persist and re-evaluate a source-bound routing assessment after clarification."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from harness.classification import Complexity
from harness.db import transaction, utcnow
from harness.state_models import ProposalStatus, QuestionStatus
from harness.task_risk import assess

LEVELS = {
    "SMALL": Complexity.SIMPLE,
    "STANDARD": Complexity.STANDARD,
    "COMPLEX": Complexity.COMPLEX,
}


def clarification_binding(controller, task_id):
    ws = controller.workspace(task_id)
    payload = {
        "request": controller.get_task(task_id).request,
        "questions": [asdict(q) for q in ws.questions()],
        "proposals": [asdict(p) for p in ws.proposals()],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def record(controller, task_id, value, root):
    ws = controller.workspace(task_id)
    task = controller.get_task(task_id)
    classification = assess(value, root)
    # Read-only preview: the larger provisional interview must not prevent a
    # justified smaller route, but every already-raised material question stays.
    decided = {
        p.question_id
        for p in ws.proposals()
        if p.status in {ProposalStatus.ACCEPTED, ProposalStatus.MODIFIED}
    }
    unresolved = [
        q for q in ws.questions() if q.status is QuestionStatus.OPEN and q.id not in decided
    ]
    if unresolved or ws.proposals(ProposalStatus.PENDING):
        raise ValueError("resolve raised questions and decisions before final routing")
    payload = {
        "schema_version": 1,
        "assessment": value,
        "repo_root": str(Path(root).resolve()),
        "clarification_hash": clarification_binding(controller, task_id),
        "al_classification": classification,
        "complexity": str(LEVELS[classification]),
        "developer_override": task.complexity_source == "developer",
    }
    with transaction(controller.conn):
        if task.complexity_source != "developer":
            controller.conn.execute(
                "UPDATE tasks SET complexity = ?, updated_at = ? WHERE id = ?",
                (LEVELS[classification], utcnow(), task_id),
            )
        ws.checkpoint("implementation_risk", payload)
        ws.record_event("complexity.assessed", payload)
    return controller.get_task(task_id)


def current(controller, task_id):
    checkpoint = controller.workspace(task_id).latest_checkpoint("implementation_risk")
    if checkpoint is None:
        return None
    value = checkpoint.payload
    if value["clarification_hash"] != clarification_binding(controller, task_id):
        return dict(
            value,
            current=False,
            reason="Clarification changed; reassess the remaining implementation work",
        )
    try:
        classification = assess(value["assessment"], Path(value["repo_root"]))
    except (ValueError, OSError) as exc:
        return dict(value, current=False, reason=str(exc))
    return dict(value, current=classification == value["al_classification"])
