"""Design-entry methods of the task workspace: developer statements and resolved recommendations."""

import sqlite3

from harness.db import utcnow
from harness.design_models import (
    DECISIONS,
    EFFECTIVE,
    Binding,
    DesignEntry,
    DesignSection,
    EntryKind,
    EntryOrigin,
    EntryStatus,
    design_entry_from_row,
)
from harness.state_proposals import required


class DesignMixin:
    """Mixed into TaskWorkspace, which provides conn, task_id, _one and _all."""

    conn: sqlite3.Connection
    task_id: str

    def state_design(
        self,
        section: DesignSection,
        statement: str,
        binding: Binding = Binding.FIXED,
        kind: EntryKind = EntryKind.ELEMENT,
    ) -> DesignEntry:
        what = "not-applicable reason" if EntryKind(kind) is EntryKind.NOT_APPLICABLE else "design"
        text = required(statement, what)
        return self._insert_design(
            section, kind, EntryOrigin.DEVELOPER, binding, text, None, EntryStatus.STATED
        )

    def propose_design(
        self,
        section: DesignSection,
        statement: str,
        rationale: str,
        binding: Binding = Binding.FIXED,
    ) -> DesignEntry:
        text, why = required(statement, "recommendation"), required(rationale, "rationale")
        return self._insert_design(
            section,
            EntryKind.ELEMENT,
            EntryOrigin.RECOMMENDATION,
            binding,
            text,
            why,
            EntryStatus.PENDING,
        )

    def resolve_design(
        self, entry_id: int, status: EntryStatus, response: str | None = None
    ) -> DesignEntry:
        outcome = EntryStatus(status)
        if outcome not in DECISIONS:
            raise ValueError("a recommendation must be resolved as accepted, rejected or modified")
        if outcome is EntryStatus.MODIFIED:
            text = required(response, "modified recommendation response")
        else:
            text = (response or "").strip() or None
        cursor = self.conn.execute(
            "UPDATE design_entries SET status = ?, response = ?, resolved_at = ?"
            " WHERE id = ? AND task_id = ? AND status = ?",
            (outcome, text, utcnow(), entry_id, self.task_id, EntryStatus.PENDING),
        )
        if cursor.rowcount == 0:
            current = self.design_entry(entry_id)
            raise ValueError(f"design entry E{entry_id} is already {current.status}")
        return self.design_entry(entry_id)

    def withdraw_design(self, entry_id: int) -> DesignEntry:
        marks = ", ".join("?" for _ in EFFECTIVE)
        cursor = self.conn.execute(
            "UPDATE design_entries SET status = ?, resolved_at = ?"
            f" WHERE id = ? AND task_id = ? AND status IN ({marks})",
            (EntryStatus.WITHDRAWN, utcnow(), entry_id, self.task_id, *EFFECTIVE),
        )
        if cursor.rowcount == 0:
            current = self.design_entry(entry_id)
            raise ValueError(f"design entry E{entry_id} is {current.status}; nothing to withdraw")
        return self.design_entry(entry_id)

    def design_entry(self, entry_id: int) -> DesignEntry:
        return design_entry_from_row(self._one("design_entries", "design entry", entry_id))

    def design_entries(self, status: EntryStatus | None = None) -> list[DesignEntry]:
        status = None if status is None else EntryStatus(status)
        return [design_entry_from_row(row) for row in self._all("design_entries", status)]

    def _insert_design(
        self,
        section: DesignSection,
        kind: EntryKind,
        origin: EntryOrigin,
        binding: Binding,
        statement: str,
        rationale: str | None,
        status: EntryStatus,
    ) -> DesignEntry:
        values = (DesignSection(section), EntryKind(kind), origin, Binding(binding))
        cursor = self.conn.execute(
            "INSERT INTO design_entries (task_id, section, kind, origin, binding, statement,"
            " rationale, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.task_id, *values, statement, rationale, status, utcnow()),
        )
        return self.design_entry(cursor.lastrowid)
