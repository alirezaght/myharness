"""Cross-project decision suggestions (SPEC §6.3): read other repositories' records, apply nothing.

Sources are only the directories the developer names explicitly; nothing is written anywhere.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.decisions import (
    DEFAULT_DECISIONS_DIR,
    DecisionParseError,
    DecisionStatus,
    decision_files,
    parse_decision,
)
from harness.repo_relevance import keywords

HEADER = "suggestions only — nothing is applied:"
FOOTER = (
    "to adopt one, propose it in the interview (harness interview propose ...)"
    " and let the developer decide"
)


@dataclass(frozen=True)
class Suggestion:
    id: str
    version: int
    title: str
    summary: str
    source: str
    matched: tuple[str, ...]

    @property
    def score(self) -> int:
        return len(self.matched)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "summary": self.summary,
            "source": self.source,
            "matched": list(self.matched),
            "score": self.score,
        }


def source_dir(path: Path) -> Path:
    """A repository root resolves to its docs/decisions; anything else is taken as given."""
    path = Path(path)
    nested = path / DEFAULT_DECISIONS_DIR
    return nested if nested.is_dir() else path


def _same(a: Path, b: Path | None) -> bool:
    return b is not None and a.resolve() == Path(b).resolve()


def suggest(
    request: str,
    questions: list[str],
    sources: list[Path],
    exclude: Path | None = None,
    limit: int = 10,
) -> tuple[list[Suggestion], list[str]]:
    words = keywords(" ".join([request, *questions]))
    found: list[Suggestion] = []
    notes: list[str] = []
    seen: set[Path] = set()
    for given in sources:
        directory = source_dir(given)
        if _same(directory, exclude):
            notes.append(f"skipped {directory}: it is this task's own registry")
            continue
        if directory.resolve() in seen:
            continue
        seen.add(directory.resolve())
        if not directory.is_dir():
            notes.append(f"skipped {directory}: not a directory")
            continue
        for path in decision_files(directory):
            try:
                record = parse_decision(path)
            except DecisionParseError as error:
                notes.append(f"skipped {path}: {error}")
                continue
            if record.status is not DecisionStatus.APPROVED:
                continue
            text = " ".join([record.title, record.decision, record.scope]).lower()
            matched = tuple(w for w in words if w in text)
            if matched:
                summary = record.decision.strip().splitlines()[0]
                found.append(
                    Suggestion(
                        record.id, record.version, record.title, summary, str(directory), matched
                    )
                )
    found.sort(key=lambda s: (-s.score, s.source, s.id))
    return found[:limit], notes


def render(suggestions: list[Suggestion], notes: list[str]) -> str:
    if not suggestions and not notes:
        return "none"
    lines = [HEADER] if suggestions else ["none"]
    for s in suggestions:
        lines.append(
            f"  {s.id} v{s.version} (from {s.source}) — {s.title}: {s.summary}"
            f" [keywords: {', '.join(s.matched)}]"
        )
    lines.extend(f"note: {note}" for note in notes)
    if suggestions:
        lines.append(FOOTER)
    return "\n".join(lines)
