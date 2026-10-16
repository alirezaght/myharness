"""Design outputs: per-section coverage of SPEC §3 Phase 3 and the rendered design section.

Only effective entries (stated, accepted, modified) are design content; nothing is invented.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from harness.design_models import Binding, DesignEntry, DesignSection, EntryKind, EntryStatus

TITLE = "## Architecture and Scaffolding Design"


class CoverageState(StrEnum):
    UNCOVERED = "uncovered"
    AWAITING_DECISION = "awaiting_decision"
    RECOMMENDATION_REJECTED = "recommendation_rejected"
    CONFLICTING = "conflicting"


@dataclass(frozen=True)
class CoverageItem:
    section: DesignSection
    state: CoverageState
    entry_id: int | None = None
    text: str | None = None

    @property
    def ref(self) -> str:
        return f"E{self.entry_id}" if self.entry_id is not None else f"section:{self.section}"

    def render(self) -> str:
        detail = f" — {self.text}" if self.text else ""
        return f"{self.ref} [{self.section}] {self.state}{detail}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "section": str(self.section),
            "state": str(self.state),
            "entry_id": self.entry_id,
            "text": self.text,
        }


@dataclass(frozen=True)
class DesignCoverage:
    items: tuple[CoverageItem, ...]
    covered: tuple[DesignSection, ...]
    counts: dict[str, int]

    @property
    def blocking(self) -> bool:
        return bool(self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocking": self.blocking,
            "unresolved": [item.to_dict() for item in self.items],
            "covered": [str(section) for section in self.covered],
            "counts": {"unresolved": len(self.items), **self.counts},
        }

    def render(self) -> str:
        lines = [f"uncovered/unresolved design items ({len(self.items)}):"]
        lines += [f"  {item.render()}" for item in self.items] or ["  (none)"]
        lines.append(f"blocking: {'yes' if self.blocking else 'no'}")
        return "\n".join(lines)


def _section_item(section: DesignSection, entries: list[DesignEntry]) -> CoverageItem | None:
    effective = [e for e in entries if e.effective]
    if not effective:
        rejected = any(e.status is EntryStatus.REJECTED for e in entries)
        state = CoverageState.RECOMMENDATION_REJECTED if rejected else CoverageState.UNCOVERED
        return CoverageItem(section, state)
    if len({e.kind for e in effective}) > 1:
        return CoverageItem(section, CoverageState.CONFLICTING, text="not applicable and elements")
    return None


def build_coverage(entries: Iterable[DesignEntry]) -> DesignCoverage:
    entries = list(entries)
    items: list[CoverageItem] = []
    covered: list[DesignSection] = []
    for section in DesignSection:
        item = _section_item(section, [e for e in entries if e.section is section])
        items += [item] if item else []
        covered += [] if item else [section]
    items += [
        CoverageItem(e.section, CoverageState.AWAITING_DECISION, e.id, e.statement)
        for e in entries
        if e.status is EntryStatus.PENDING
    ]
    effective = [e for e in entries if e.effective]
    counts = {
        "stated": sum(e.status is EntryStatus.STATED for e in effective),
        "decided": sum(e.status is not EntryStatus.STATED for e in effective),
        "not_applicable": sum(e.kind is EntryKind.NOT_APPLICABLE for e in effective),
        "delegated": sum(e.binding is Binding.DELEGATED for e in effective),
    }
    return DesignCoverage(tuple(items), tuple(covered), counts)


def _entry_dict(entry: DesignEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "ref": entry.ref,
        "text": entry.text,
        "binding": str(entry.binding),
        "kind": str(entry.kind),
        "provenance": str(entry.provenance),
    }


def entry_line(entry: DesignEntry) -> str:
    if entry.kind is EntryKind.NOT_APPLICABLE:
        return f"- not applicable: {entry.text} ({entry.provenance})"
    binding = "delegated to AL" if entry.binding is Binding.DELEGATED else "fixed"
    return f"- {entry.text} ({binding}; {entry.provenance})"


@dataclass(frozen=True)
class DesignDocument:
    entries: tuple[DesignEntry, ...]
    coverage: DesignCoverage

    def section(self, section: DesignSection) -> list[DesignEntry]:
        return [e for e in self.entries if e.section is section]

    @property
    def delegated(self) -> list[DesignEntry]:
        return [e for e in self.entries if e.binding is Binding.DELEGATED]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sections": {str(s): [_entry_dict(e) for e in self.section(s)] for s in DesignSection},
            "delegated": [{**_entry_dict(e), "section": str(e.section)} for e in self.delegated],
            "coverage": self.coverage.to_dict(),
        }

    def section_lines(self, section: DesignSection) -> list[str]:
        return [entry_line(e) for e in self.section(section)] or ["- (uncovered)"]

    def delegated_lines(self) -> list[str]:
        lines = [f"- [{e.section.heading}] {e.text} ({e.provenance})" for e in self.delegated]
        return lines or ["- (none)"]

    def render(self) -> str:
        lines = [TITLE]
        for section in DesignSection:
            lines += ["", f"### {section.heading}", *self.section_lines(section)]
        lines += ["", "### Delegated to AL", *self.delegated_lines()]
        lines += ["", "### Coverage", self.coverage.render()]
        return "\n".join(lines)


def build_document(entries: Iterable[DesignEntry]) -> DesignDocument:
    all_entries = tuple(entries)
    effective = tuple(e for e in all_entries if e.effective)
    return DesignDocument(effective, build_coverage(all_entries))
