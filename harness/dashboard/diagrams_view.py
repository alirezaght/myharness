"""The diagrams view (P2-04): the task's current diagram version, its history and any version.

The current version is always the stored latest one; `outdated` says the design has changed
since (the next view records it). Notation problems are listed, never guessed around.
"""

from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from harness.controller import HarnessController, Task
from harness.design_models import EntryKind, EntryStatus
from harness.diagram_model import build_model, relevant_types
from harness.diagram_scenes import render_scenes
from harness.diagrams import DiagramService
from harness.errors import TaskNotFound
from harness.state_diagrams import DiagramVersion

NO_DIAGRAMS = "no diagrams yet"
NO_NOTATION_HINT = (
    "the design has no diagram notation yet: lines such as 'component <name>' or"
    " 'connect <from> -> <to>' in stated or accepted design entries become diagrams"
)


def version_meta(version: DiagramVersion, latest: DiagramVersion | None) -> dict[str, Any]:
    return {
        "version": version.version,
        "content_hash": version.content_hash,
        "created_at": version.created_at,
        "types": relevant_types(version.model),
        "current": latest is not None and version.version == latest.version,
    }


def diagrams_section(
    controller: HarnessController, task: Task, unavailable: str | None, execution=None
) -> dict[str, Any]:
    service = DiagramService(controller, task.id)
    model, versions = service.model(), service.versions()
    latest = versions[-1] if versions else None
    current = None
    if latest is not None:
        current = version_meta(latest, latest) | {"diagrams": service.scenes(latest)}
    no_diagrams = current is None or not current["diagrams"]
    has_prose = any(
        e.effective and e.kind == EntryKind.ELEMENT for e in service.ws.design_entries()
    )
    proposals = []
    for entry in service.ws.design_entries():
        if entry.status != EntryStatus.PENDING or entry.kind != EntryKind.ELEMENT:
            continue
        proposed = build_model([replace(entry, status=EntryStatus.STATED)])
        if not proposed.empty or proposed.issues:
            proposals.append(
                {
                    "entry_id": entry.id,
                    "ref": entry.ref,
                    "status": "proposed",
                    "diagrams": render_scenes(proposed.data),
                    "issues": [asdict(issue) for issue in proposed.issues],
                }
            )
    state = (
        "effective"
        if not model.empty
        else ("proposed" if proposals else ("prose_only" if has_prose else "absent"))
    )
    design_phase = str(task.phase) == "design"
    guidance = (
        "Propose structured notation with harness design propose, "
        "then explicitly accept it with harness design decide."
    )
    if not design_phase:
        guidance = (
            "An explicit design revision is required: return this task to the design phase "
            "with a reason, propose notation, then obtain acceptance and repeat the review and "
            "approval workflow. The sealed specification is not changed by viewing this panel."
        )
    from harness.change_diagrams import reference_view

    return {
        "implementation": reference_view(controller, task, execution),
        "state": state,
        "provenance": (
            "Diagrams come from stated or accepted structured design notation, "
            "independently of AL implementation planning."
        ),
        "proposals": proposals,
        "guidance": guidance,
        "design_revision_required": not design_phase,
        "unavailable": unavailable,
        "outdated": DiagramService.needs(model, latest),
        "issues": [asdict(issue) for issue in model.issues],
        "notice": NO_DIAGRAMS if no_diagrams else None,
        "hint": NO_NOTATION_HINT if no_diagrams and model.empty and has_prose else None,
        "current": current,
        "history": [version_meta(v, latest) for v in versions],
    }


def diagram_version_view(db_path: Path, task_id: str, version: int) -> dict[str, Any]:
    """One stored version with its diagrams; read-only (no sync) and never creates the DB."""
    if not Path(db_path).exists():
        raise TaskNotFound(task_id)
    from harness.dashboard.view import _snapshot, open_state  # lazy: view imports this module

    with open_state(db_path) as controller, _snapshot(controller.conn):
        service = DiagramService(controller, task_id)
        shown = service.version(version)
        return version_meta(shown, service.latest()) | {"diagrams": service.scenes(shown)}
