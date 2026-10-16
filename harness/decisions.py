"""Reader for the hand-written decision records in docs/decisions/*.md (SPEC §6.4)."""

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

DEFAULT_DECISIONS_DIR = Path("docs") / "decisions"
_FIELD = re.compile(r"^- \*\*(\w[\w ]*):\*\*\s*(.*)$")
_TITLE_PREFIX = re.compile(r"^DEC-\d+\s*[—–-]\s*")
_SECTIONS = ("Decision", "Rationale", "Impact")


class DecisionStatus(StrEnum):
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    DRAFT = "draft"
    REJECTED = "rejected"


@dataclass(frozen=True)
class DecisionRecord:
    id: str
    title: str
    status: DecisionStatus
    version: int
    timestamp: str
    scope: str
    supersedes: str | None
    decision: str
    rationale: str
    impact: str
    path: Path


class DecisionParseError(ValueError):
    def __init__(self, path: Path, problem: str):
        self.path = path
        super().__init__(f"{path}: {problem}")


def decision_files(directory: Path) -> list[Path]:
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("DEC-*.md") if path.is_file())


def _scan(lines: list[str]) -> tuple[str, dict[str, str], dict[str, str]]:
    title, fields, sections, current = "", {}, {}, None
    for line in lines:
        if not title and line.startswith("# "):
            title = _TITLE_PREFIX.sub("", line[2:].strip())
            continue
        if line.startswith("## "):
            current = line[3:].strip()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
            continue
        match = _FIELD.match(line.strip())
        if match:
            fields[match.group(1).strip().lower()] = match.group(2).strip()
    return title, fields, {name: "\n".join(body).strip() for name, body in sections.items()}


def parse_decision(path: Path) -> DecisionRecord:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise DecisionParseError(path, f"cannot be read ({error})") from error
    title, fields, sections = _scan(text.splitlines())
    for name in ("id", "status", "version", "timestamp"):
        if not fields.get(name):
            raise DecisionParseError(path, f"missing field {name.title()}")
    if not sections.get("Decision"):
        raise DecisionParseError(path, "missing ## Decision section")
    try:
        status = DecisionStatus(fields["status"].lower())
    except ValueError as error:
        raise DecisionParseError(path, f"unknown status {fields['status']!r}") from error
    try:
        version = int(fields["version"])
    except ValueError as error:
        raise DecisionParseError(path, f"invalid version {fields['version']!r}") from error
    supersedes = fields.get("supersedes", "")
    return DecisionRecord(
        id=fields["id"],
        title=title or fields["id"],
        status=status,
        version=version,
        timestamp=fields["timestamp"],
        scope=fields.get("scope", ""),
        supersedes=None if supersedes.lower() in ("", "none", "-") else supersedes,
        decision=sections["Decision"],
        rationale=sections.get("Rationale", ""),
        impact=sections.get("Impact", ""),
        path=path,
    )


def load_decisions(directory: Path) -> list[DecisionRecord]:
    return [parse_decision(path) for path in decision_files(directory)]
