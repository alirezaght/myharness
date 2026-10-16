"""Design entries of SPEC §3 Phase 3: the eight design sections and their provenance."""

import sqlite3
from dataclasses import dataclass
from enum import StrEnum

from harness.interview_report import Provenance


class DesignSection(StrEnum):
    COMPONENTS = "components"
    RESPONSIBILITIES = "responsibilities"
    INTERACTIONS = "interactions"
    DATA_FLOW = "data_flow"
    INTERFACES = "interfaces"
    CONFIGURATION = "configuration"
    FAILURE_HANDLING = "failure_handling"
    REQUIRED_BEHAVIOUR = "required_behaviour"

    @property
    def heading(self) -> str:
        return SECTION_TITLES[self]


SECTION_TITLES = {
    DesignSection.COMPONENTS: "Components",
    DesignSection.RESPONSIBILITIES: "Responsibilities",
    DesignSection.INTERACTIONS: "Interactions",
    DesignSection.DATA_FLOW: "Data flow",
    DesignSection.INTERFACES: "Interfaces and contracts",
    DesignSection.CONFIGURATION: "Configuration",
    DesignSection.FAILURE_HANDLING: "Failure handling",
    DesignSection.REQUIRED_BEHAVIOUR: "Required behaviour",
}


class EntryKind(StrEnum):
    ELEMENT = "element"
    NOT_APPLICABLE = "not_applicable"


class EntryOrigin(StrEnum):
    DEVELOPER = "developer"
    RECOMMENDATION = "recommendation"


class Binding(StrEnum):
    FIXED = "fixed"
    DELEGATED = "delegated"


class EntryStatus(StrEnum):
    STATED = "stated"
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MODIFIED = "modified"
    WITHDRAWN = "withdrawn"


EFFECTIVE = (EntryStatus.STATED, EntryStatus.ACCEPTED, EntryStatus.MODIFIED)
DECISIONS = (EntryStatus.ACCEPTED, EntryStatus.REJECTED, EntryStatus.MODIFIED)
_PROVENANCE = {
    EntryStatus.STATED: Provenance.DEVELOPER,
    EntryStatus.ACCEPTED: Provenance.ACCEPTED_RECOMMENDATION,
    EntryStatus.MODIFIED: Provenance.MODIFIED_RECOMMENDATION,
}


@dataclass(frozen=True)
class DesignEntry:
    id: int
    task_id: str
    section: DesignSection
    kind: EntryKind
    origin: EntryOrigin
    binding: Binding
    statement: str
    rationale: str | None
    status: EntryStatus
    response: str | None
    created_at: str
    resolved_at: str | None

    @property
    def effective(self) -> bool:
        return self.status in EFFECTIVE

    @property
    def text(self) -> str:
        if self.status is EntryStatus.MODIFIED and self.response:
            return self.response
        return self.statement

    @property
    def provenance(self) -> Provenance | None:
        return _PROVENANCE.get(self.status)

    @property
    def ref(self) -> str:
        return f"E{self.id}"


def design_entry_from_row(row: sqlite3.Row) -> DesignEntry:
    return DesignEntry(
        id=row["id"],
        task_id=row["task_id"],
        section=DesignSection(row["section"]),
        kind=EntryKind(row["kind"]),
        origin=EntryOrigin(row["origin"]),
        binding=Binding(row["binding"]),
        statement=row["statement"],
        rationale=row["rationale"],
        status=EntryStatus(row["status"]),
        response=row["response"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
    )
