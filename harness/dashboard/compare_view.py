"""The comparison of two stored diagram versions (P2-06): both sides' diagrams and their diff.

Read-only: no sync, one read snapshot, and the DB is never created. Stored versions are
immutable, so the same pair always gives the same diagrams and changes ("current" may move).
"""

from pathlib import Path
from typing import Any

from harness.dashboard.diagrams_view import version_meta
from harness.dashboard.view import _snapshot, open_state
from harness.diagram_diff import diff_models
from harness.diagrams import DiagramService
from harness.errors import TaskNotFound
from harness.state_diagrams import DiagramVersion


def _side(
    service: DiagramService, version: DiagramVersion, latest: DiagramVersion | None
) -> tuple[dict[str, Any], dict[str, str]]:
    """The version's meta with its diagrams, and {element id: label} (first one wins)."""
    scenes = service.scenes(version)
    labels: dict[str, str] = {}
    for scene in scenes:
        for item in scene["items"]:
            labels.setdefault(item["id"], item["label"])
    return version_meta(version, latest) | {"diagrams": scenes}, labels


def compare_view(db_path: Path, task_id: str, older: int, newer: int) -> dict[str, Any]:
    """{"from", "to", "changes", "marks", "counts"} for versions older < newer of the task."""
    if older >= newer:
        raise ValueError("from must be an earlier version than to")
    if not Path(db_path).exists():
        raise TaskNotFound(task_id)
    with open_state(db_path) as controller, _snapshot(controller.conn):
        service = DiagramService(controller, task_id)
        first, second = service.version(older), service.version(newer)
        latest = service.latest()
        meta_a, labels_a = _side(service, first, latest)
        meta_b, labels_b = _side(service, second, latest)
        diff = diff_models(first.model, second.model, labels_a, labels_b)
        return {"from": meta_a, "to": meta_b} | diff
