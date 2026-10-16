"""Investigation report: verified facts, assumptions and unresolved questions kept apart."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from harness.decisions import DecisionRecord


class FactCategory(StrEnum):
    REPOSITORY = "repository"
    COMPONENTS = "components"
    FILES = "files"
    DEPENDENCIES = "dependencies"
    INTERFACES = "interfaces"
    SCHEMAS = "schemas"
    CONFIG = "config"
    TESTS = "tests"
    CONVENTIONS = "conventions"
    CONSTRAINTS = "constraints"
    DECISIONS = "decisions"


@dataclass(frozen=True)
class Fact:
    category: FactCategory
    text: str
    evidence: str


@dataclass(frozen=True)
class Assumption:
    text: str
    basis: str


@dataclass(frozen=True)
class OpenQuestion:
    topic: str
    text: str


def _decision_dict(record: DecisionRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "title": record.title,
        "status": str(record.status),
        "version": record.version,
        "timestamp": record.timestamp,
        "scope": record.scope,
        "supersedes": record.supersedes,
        "path": str(record.path),
    }


def _section(title: str, lines: list[str]) -> list[str]:
    return [f"{title} ({len(lines)}):", *(f"  {line}" for line in lines or ["(none)"])]


@dataclass(frozen=True)
class InvestigationReport:
    repo_root: str
    revision: str | None
    request: str
    decisions: tuple[DecisionRecord, ...]
    facts: tuple[Fact, ...]
    assumptions: tuple[Assumption, ...]
    questions: tuple[OpenQuestion, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_root": self.repo_root,
            "revision": self.revision,
            "request": self.request,
            "decisions": [_decision_dict(record) for record in self.decisions],
            "facts": [
                {"category": str(f.category), "text": f.text, "evidence": f.evidence}
                for f in self.facts
            ],
            "assumptions": [{"text": a.text, "basis": a.basis} for a in self.assumptions],
            "questions": [{"topic": q.topic, "text": q.text} for q in self.questions],
            "counts": {
                "facts": len(self.facts),
                "assumptions": len(self.assumptions),
                "questions": len(self.questions),
                "decisions": len(self.decisions),
            },
        }

    def render(self) -> str:
        revision = self.revision or "no git revision"
        decisions = [f"{d.id} v{d.version} — {d.title}" for d in self.decisions]
        facts = [f"[{f.category}] {f.text} — {f.evidence}" for f in self.facts]
        assumptions = [f"{a.text} — basis: {a.basis}" for a in self.assumptions]
        questions = [f"[{q.topic}] {q.text}" for q in self.questions]
        lines = [
            f"investigation of {self.repo_root} (revision {revision})",
            f"request: {self.request}",
            *_section("approved decisions", decisions),
            *_section("verified facts", facts),
            *_section("assumptions", assumptions),
            *_section("unresolved questions", questions),
        ]
        return "\n".join(lines)
