"""Architecture Designer (SPEC §3 Phase 3): the developer-controlled logical design of a task."""

from collections.abc import Iterator
from contextlib import contextmanager

from harness.controller import HarnessController
from harness.db import transaction, utcnow
from harness.design_models import Binding, DesignEntry, DesignSection, EntryKind, EntryStatus
from harness.design_report import DesignCoverage, DesignDocument, build_coverage, build_document
from harness.phases import PHASE_ORDER, Phase

__all__ = ["ArchitectureDesigner", "DesignError"]


class DesignError(ValueError):
    pass


def _binding(delegated: bool) -> Binding:
    return Binding.DELEGATED if delegated else Binding.FIXED


class ArchitectureDesigner:
    def __init__(self, controller: HarnessController, task_id: str):
        self.controller = controller
        self.conn = controller.conn
        self.task_id = controller.get_task(task_id).id
        self.ws = controller.workspace(task_id)

    # Read-only views

    def coverage(self) -> DesignCoverage:
        return build_coverage(self.ws.design_entries())

    def document(self) -> DesignDocument:
        return build_document(self.ws.design_entries())

    def entry(self, entry_id: int) -> DesignEntry:
        return self.ws.design_entry(entry_id)

    # Mutations: each one transaction, one event and one `design` checkpoint

    def add(self, section: DesignSection, text: str, delegated: bool = False) -> DesignEntry:
        section = DesignSection(section)
        with self._mutation():
            self._refuse(section, EntryKind.NOT_APPLICABLE)
            entry = self.ws.state_design(section, text, _binding(delegated))
            payload = {"id": entry.id, "section": section, "binding": entry.binding}
            self.ws.record_event("design.stated", payload)
        return entry

    def none(self, section: DesignSection, reason: str) -> DesignEntry:
        section = DesignSection(section)
        with self._mutation():
            self._refuse(section, EntryKind.ELEMENT)
            entry = self.ws.state_design(section, reason, Binding.FIXED, EntryKind.NOT_APPLICABLE)
            self.ws.record_event("design.not_applicable", {"id": entry.id, "section": section})
        return entry

    def propose(
        self, section: DesignSection, text: str, rationale: str, delegated: bool = False
    ) -> DesignEntry:
        section = DesignSection(section)
        with self._mutation():
            self._refuse(section, EntryKind.NOT_APPLICABLE)
            entry = self.ws.propose_design(section, text, rationale, _binding(delegated))
            payload = {"id": entry.id, "section": section, "binding": entry.binding}
            self.ws.record_event("design.proposed", payload)
        return entry

    def decide(self, entry_id: int, status: EntryStatus, response: str | None) -> DesignEntry:
        with self._mutation():
            entry = self.ws.resolve_design(entry_id, status, response)
            self.ws.record_event("design.decided", {"id": entry.id, "status": entry.status})
        return entry

    def withdraw(self, entry_id: int) -> DesignEntry:
        with self._mutation():
            entry = self.ws.withdraw_design(entry_id)
            self.ws.record_event("design.withdrawn", {"id": entry.id, "section": entry.section})
        return entry

    # Internals

    def _refuse(self, section: DesignSection, kind: EntryKind) -> None:
        """A section is either not applicable or has elements, never both by direct statement."""
        clash = [
            e
            for e in self.ws.design_entries()
            if e.section is section and e.effective and e.kind is kind
        ]
        if not clash:
            return
        if kind is EntryKind.NOT_APPLICABLE:
            raise DesignError(
                f"section {section} is marked not applicable ({clash[0].ref});"
                f" withdraw {clash[0].ref} first: harness design withdraw"
                f" {self.task_id} {clash[0].id}"
            )
        refs = ", ".join(e.ref for e in clash)
        raise DesignError(f"section {section} already has design elements ({refs})")

    @contextmanager
    def _mutation(self) -> Iterator[None]:
        with transaction(self.conn):
            task = self.controller.get_task(self.task_id)
            if task.phase is not Phase.DESIGN:
                before = PHASE_ORDER.index(task.phase) < PHASE_ORDER.index(Phase.DESIGN)
                hint = (
                    f"harness phase {task.id} next"
                    if before
                    else f'harness phase {task.id} back design --reason "<why>"'
                )
                raise DesignError(f"task {task.id} is in phase {task.phase}; run: {hint}")
            yield
            coverage = self.coverage()
            self.ws.checkpoint(
                "design",
                {
                    "uncovered": [str(s) for s in DesignSection if s not in coverage.covered],
                    "unresolved": [i.ref for i in coverage.items],
                    "blocking": coverage.blocking,
                },
            )
            self.conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?", (utcnow(), self.task_id)
            )
