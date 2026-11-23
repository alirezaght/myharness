"""Investigation report: verified facts, assumptions and unresolved questions kept apart."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from harness.decisions import DecisionRecord
from harness.ticket_refs import ticket_ids, ticket_only


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


CONTEXT_IMPORT = "harness context import {task} --file <context.json>"


def ticket_only_question(ids: list[str]) -> str:
    return (
        f"The request names only ticket IDs ({', '.join(ids)}); the harness does not fetch "
        "ticket content, so it has no requirements to investigate. Fetch each ticket's text "
        f"and import it with `{CONTEXT_IMPORT.format(task='<task-id>')}` as sources with "
        '`kind: "ticket"`, then investigate again.'
    )


# The default report keeps repository state, request matches and scoped ownership.
# Every other fact is generic repository inventory, rendered only with `full`.
_RELEVANT = frozenset({FactCategory.REPOSITORY, FactCategory.FILES})


def _relevant(fact: Fact) -> bool:
    return fact.category in _RELEVANT or fact.text.startswith("scoped ")


def _fact_line(fact: Fact) -> str:
    return f"[{fact.category}] {fact.text} — {fact.evidence}"


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

    def render(self, *, full: bool = False, task_id: str | None = None) -> str:
        """Compact by default: questions and request-relevant facts first.

        `full` renders every fact, including the generic repository inventory. The stored
        report and `to_dict` are identical either way; only this text differs.
        """
        task = task_id or "<task-id>"
        revision = self.revision or "no git revision"
        decisions = [f"{d.id} v{d.version} — {d.title}" for d in self.decisions]
        assumptions = [f"{a.text} — basis: {a.basis}" for a in self.assumptions]
        questions = [f"[{q.topic}] {q.text}" for q in self.questions]
        lines = [
            f"investigation of {self.repo_root} (revision {revision})",
            f"request: {self.request}",
        ]
        if ticket_only(self.request):
            lines.append(
                f"NOTE: the request is only ticket IDs ({', '.join(ticket_ids(self.request))}) "
                "and the harness does not fetch tickets, so nothing below reflects their "
                "requirements. Fetch the ticket text and run "
                f'`{CONTEXT_IMPORT.format(task=task)}` with `kind: "ticket"` sources, '
                "then investigate again."
            )
        if full:
            return "\n".join(
                [
                    *lines,
                    *_section("approved decisions", decisions),
                    *_section("verified facts", [_fact_line(f) for f in self.facts]),
                    *_section("assumptions", assumptions),
                    *_section("unresolved questions", questions),
                ]
            )
        omitted: dict[str, int] = {}
        for fact in self.facts:
            if not _relevant(fact):
                omitted[str(fact.category)] = omitted.get(str(fact.category), 0) + 1
        lines += [
            *_section("unresolved questions", questions),
            *_section("approved decisions", decisions),
            *_section(
                "request-relevant facts", [_fact_line(f) for f in self.facts if _relevant(f)]
            ),
            *_section("assumptions", assumptions),
        ]
        if omitted:
            counts = ", ".join(f"{name} {count}" for name, count in sorted(omitted.items()))
            lines.append(
                f"repository inventory not shown: {sum(omitted.values())} facts ({counts})"
            )
        lines.append(f"every fact: `harness investigate {task} --full` (or --json)")
        return "\n".join(lines)
