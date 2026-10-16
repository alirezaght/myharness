"""Interview outputs: the unresolved-decision checklist and the developer-provenanced summary."""

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class ChecklistState(StrEnum):
    NOT_ASKED = "not_asked"
    UNANSWERED = "unanswered"
    AWAITING_DECISION = "awaiting_decision"
    RECOMMENDATION_REJECTED = "recommendation_rejected"


class Provenance(StrEnum):
    DEVELOPER = "developer"
    ACCEPTED_RECOMMENDATION = "accepted_recommendation"
    MODIFIED_RECOMMENDATION = "modified_recommendation"


@dataclass(frozen=True)
class ChecklistItem:
    question_id: int | None
    topic: str
    text: str
    kind: str
    state: ChecklistState
    proposal_id: int | None = None
    catalogue_key: str | None = None

    @property
    def ref(self) -> str:
        if self.question_id is not None:
            return f"Q{self.question_id}"
        if self.proposal_id is not None:
            return f"P{self.proposal_id}"
        return f"catalogue:{self.catalogue_key}"

    def render(self) -> list[str]:
        lines = [f"{self.ref} [{self.topic}] {self.state} — {self.text}"]
        if self.question_id is not None and self.state is ChecklistState.AWAITING_DECISION:
            lines.append(f"  → proposal P{self.proposal_id} pending")
        return lines


@dataclass(frozen=True)
class InterviewChecklist:
    items: tuple[ChecklistItem, ...]
    answered: int
    decided: int
    skipped: int

    @property
    def blocking(self) -> bool:
        return bool(self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocking": self.blocking,
            "unresolved": [
                {**asdict(item), "ref": item.ref, "state": str(item.state), "kind": str(item.kind)}
                for item in self.items
            ],
            "counts": {
                "unresolved": len(self.items),
                "answered": self.answered,
                "decided": self.decided,
                "skipped": self.skipped,
            },
        }

    def render(self) -> str:
        lines = [f"unresolved decisions ({len(self.items)}):"]
        body = [line for item in self.items for line in item.render()] or ["(none)"]
        lines += [f"  {line}" for line in body]
        lines.append(f"blocking: {'yes' if self.blocking else 'no'}")
        return "\n".join(lines)


@dataclass(frozen=True)
class Requirement:
    question_id: int | None
    topic: str
    text: str
    statement: str
    provenance: Provenance


@dataclass(frozen=True)
class SkippedQuestion:
    question_id: int
    topic: str
    text: str
    skip_source: str
    skip_reason: str


def _section(title: str, lines: list[str]) -> list[str]:
    return [f"{title} ({len(lines)}):", *(f"  {line}" for line in lines or ["(none)"])]


def _ref(question_id: int | None) -> str:
    return f"Q{question_id}" if question_id is not None else "-"


@dataclass(frozen=True)
class InterviewSummary:
    requirements: tuple[Requirement, ...]
    answered_elsewhere: tuple[SkippedQuestion, ...]
    not_relevant: tuple[SkippedQuestion, ...]
    checklist: InterviewChecklist

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirements": [
                {**asdict(r), "provenance": str(r.provenance)} for r in self.requirements
            ],
            "answered_elsewhere": [asdict(s) for s in self.answered_elsewhere],
            "not_relevant": [asdict(s) for s in self.not_relevant],
            "checklist": self.checklist.to_dict(),
        }

    def render(self) -> str:
        requirements = [
            f"{_ref(r.question_id)} [{r.topic}] {r.statement} ({r.provenance})"
            for r in self.requirements
        ]
        elsewhere = [
            f"Q{s.question_id} [{s.topic}] {s.text} — {s.skip_source}: {s.skip_reason}"
            for s in self.answered_elsewhere
        ]
        irrelevant = [
            f"Q{s.question_id} [{s.topic}] {s.text} — {s.skip_reason}" for s in self.not_relevant
        ]
        lines = [
            *_section("requirements", requirements),
            *_section("answered elsewhere", elsewhere),
            *_section("not relevant", irrelevant),
            self.checklist.render(),
        ]
        return "\n".join(lines)
