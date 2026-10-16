"""Resume (SPEC AC 12): where an interrupted task continues, derived from committed state only.

Every mutation is one SQLite transaction, so the committed rows are the last consistent
checkpoint; a killed process leaves nothing half-applied. Nothing here writes a row or a file.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.approval import ApprovalGate
from harness.controller import HarnessController, Task
from harness.design import ArchitectureDesigner
from harness.interview import InterviewEngine
from harness.phases import Phase
from harness.review import FinalReviewer
from harness.spec_store import SpecStore
from harness.state import TaskWorkspace
from harness.state_models import Checkpoint, Question
from harness.testplan import TestPlanner

__all__ = ["NOTHING_TO_RESUME", "ResumePoint", "resumable_tasks", "resume_point"]

NOTHING_TO_RESUME = "nothing to resume: approved for implementation (handoff is Phase 3)"
CURRENT = "(current)"


@dataclass(frozen=True)
class ResumePoint:
    task: Task
    checkpoint: Checkpoint | None
    pending_question: Question | None
    next_command: str | None
    approval: str | None
    notes: tuple[str, ...]
    resumable: bool

    def to_dict(self) -> dict[str, Any]:
        task, checkpoint, question = self.task, self.checkpoint, self.pending_question
        return {
            "task_id": task.id,
            "phase": str(task.phase),
            "complexity": str(task.complexity),
            "request": task.request,
            "updated_at": task.updated_at,
            "checkpoint": None
            if checkpoint is None
            else {
                "id": checkpoint.id,
                "label": checkpoint.label,
                "phase": str(checkpoint.phase),
                "created_at": checkpoint.created_at,
            },
            "pending_question": None
            if question is None
            else {
                "id": question.id,
                "topic": question.topic,
                "text": question.text,
                "kind": str(question.kind),
            },
            "next": self.next_command,
            "approval": self.approval,
            "notes": list(self.notes),
            "resumable": self.resumable,
        }

    def render_head(self) -> str:
        task, checkpoint = self.task, self.checkpoint
        lines = [
            f"task {task.id} · phase {task.phase} · complexity {task.complexity}"
            f" ({task.complexity_source})",
            f"request: {task.request}",
            "checkpoint: none in this phase"
            if checkpoint is None
            else f"checkpoint: {checkpoint.label} {checkpoint.created_at} (#{checkpoint.id})",
        ]
        return "\n".join(lines)

    def render_tail(self) -> str:
        lines = [] if self.approval is None else [f"approval: {self.approval}"]
        lines += [f"note: {note}" for note in self.notes]
        lines.append(
            NOTHING_TO_RESUME if self.next_command is None else f"next: {self.next_command}"
        )
        return "\n".join(lines)

    def render(self) -> str:
        return f"{self.render_head()}\n{self.render_tail()}"


def _checkpoint(ws: TaskWorkspace, phase: Phase) -> Checkpoint | None:
    in_phase = [c for c in ws.checkpoints() if c.phase is phase]
    return in_phase[-1] if in_phase else None


def _draft_note(ws: TaskWorkspace, store: SpecStore) -> tuple[str, ...]:
    """draft.md is the only file written inside a transaction; flag one never committed."""
    path = store.draft_path
    if not path.is_file():
        return ()
    latest = ws.latest_checkpoint("spec")
    if latest is not None and latest.payload == {"status": "draft", "path": str(path)}:
        return ()
    return (
        f"unrecorded draft {path} (the write was interrupted);"
        f" harness spec draft {ws.task_id} rewrites it",
    )


def _interview_next(controller: HarnessController, task_id: str) -> tuple[str, Question | None]:
    engine = InterviewEngine(controller, task_id)
    if engine.sync_due():
        return f"harness interview next {task_id}", None
    question = engine.upcoming()
    if question is not None:
        return f"harness interview next {task_id}", question
    if engine.checklist().blocking:
        return f"harness interview checklist {task_id}", None
    return f"harness phase {task_id} next", None


def resume_point(
    controller: HarnessController, task_id: str, store: SpecStore, repo_root: Path
) -> ResumePoint:
    task = controller.get_task(task_id)
    tid, ws = task.id, controller.workspace(task.id)
    question, approval, resumable = None, None, True
    advance = f"harness phase {tid} next"
    if task.phase is Phase.APPROVAL:
        approval = ApprovalGate(controller, tid, store, repo_root).status()
    if ws.latest_checkpoint("investigation") is None:
        command: str | None = f"harness investigate {tid}"
    elif task.phase is Phase.INVESTIGATION:
        command = advance
    elif task.phase is Phase.INTERVIEW:
        command, question = _interview_next(controller, tid)
    elif task.phase is Phase.DESIGN:
        blocked = ArchitectureDesigner(controller, tid).coverage().blocking
        command = f"harness design coverage {tid}" if blocked else advance
    elif task.phase is Phase.TEST_PLAN:
        blocked = TestPlanner(controller, tid).review().blocking
        command = f"harness testplan review {tid}" if blocked else advance
    elif task.phase is Phase.FINAL_REVIEW:
        blocked = FinalReviewer(controller, tid).report().blocking
        command = f"harness review {tid}" if blocked else advance
    elif approval is not None and approval.endswith(CURRENT):
        command, resumable = None, False
    else:
        blocked = FinalReviewer(controller, tid).report().blocking
        command = f"harness review {tid}" if blocked else f"harness approve {tid}"
    return ResumePoint(
        task=task,
        checkpoint=_checkpoint(ws, task.phase),
        pending_question=question,
        next_command=command,
        approval=approval,
        notes=_draft_note(ws, store),
        resumable=resumable,
    )


def resumable_tasks(
    controller: HarnessController,
    store_for: Callable[[str], SpecStore],
    root_for: Callable[[str], Path],
) -> list[ResumePoint]:
    """Every task that is not approved-current, most recently updated first."""
    points = [
        resume_point(controller, task.id, store_for(task.id), root_for(task.id))
        for task in controller.list_tasks()
    ]
    kept = sorted((p for p in points if p.resumable), key=lambda p: p.task.id)
    return sorted(kept, key=lambda p: p.task.updated_at, reverse=True)
