"""Diagram Service (SPEC §13, P2-04): the task's diagram model, its versions and their scenes.

The model is built from the task's effective design entries (harness.diagram_model). A version
is an immutable snapshot of the whole model, numbered 1, 2, 3… per task; sync() records a new
one only when the model differs from the latest version (or there is none and the model is not
empty). sync() writes nothing else: no event, checkpoint or task timestamp.
"""

import sqlite3
from typing import Any

from harness.controller import HarnessController
from harness.db import transaction
from harness.diagram_model import DiagramModel, build_model
from harness.diagram_scenes import render_scenes
from harness.state_diagrams import DiagramVersion


class DiagramService:
    def __init__(self, controller: HarnessController, task_id: str):
        self.controller = controller
        self.conn = controller.conn
        self.task_id = controller.get_task(task_id).id
        self.ws = controller.workspace(self.task_id)

    def model(self) -> DiagramModel:
        return build_model(self.ws.design_entries())

    def versions(self) -> list[DiagramVersion]:
        return self.ws.diagram_versions()

    def version(self, number: int) -> DiagramVersion:
        return self.ws.diagram_version(number)

    def latest(self) -> DiagramVersion | None:
        return self.ws.latest_diagram_version()

    @staticmethod
    def needs(model: DiagramModel, latest: DiagramVersion | None) -> bool:
        """Whether the model is not yet recorded as the latest version."""
        if latest is None:
            return not model.empty
        return model.content_hash != latest.content_hash

    def sync(self) -> DiagramVersion | None:
        """Record the current model as the next version if needed; the new version or None.

        Unchanged state is checked without a write lock and writes nothing. Otherwise the check
        is repeated under BEGIN IMMEDIATE, so concurrent syncs record one version only.
        """
        if self.conn.in_transaction:
            raise sqlite3.OperationalError("diagram sync must run outside a transaction")
        if not self.needs(self.model(), self.latest()):
            return None
        with transaction(self.conn):
            model = self.model()
            if not self.needs(model, self.latest()):
                return None
            return self.ws.record_diagram_version(model.canonical_json, model.content_hash)

    def scenes(self, version: DiagramVersion) -> list[dict[str, Any]]:
        """The version's diagrams, rendered fresh from its stored model (no caching)."""
        return render_scenes(version.model)

    def elements(self, version: DiagramVersion) -> dict[str, tuple[str, str]]:
        """Every addressable element id of the version: {id: (kind, label)}, first one wins."""
        found: dict[str, tuple[str, str]] = {}
        for scene in self.scenes(version):
            for item in scene["items"]:
                found.setdefault(item["id"], (item["kind"], item["label"]))
        return found
