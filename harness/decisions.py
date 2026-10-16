"""Format of the decision records in docs/decisions/*.md (SPEC §6.4): reader and writer."""

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

DEFAULT_DECISIONS_DIR = Path("docs") / "decisions"
HISTORY_DIR = "history"
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
    superseded_by: str | None = None


class DecisionParseError(ValueError):
    def __init__(self, path: Path, problem: str):
        self.path = path
        super().__init__(f"{path}: {problem}")


def decision_files(directory: Path) -> list[Path]:
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("DEC-*.md") if path.is_file())


def history_files(directory: Path) -> list[Path]:
    """Earlier versions kept under <directory>/history/ after a decision was superseded."""
    return decision_files(Path(directory) / HISTORY_DIR)


def _optional(value: str) -> str | None:
    return None if value.lower() in ("", "none", "-") else value


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
    return DecisionRecord(
        id=fields["id"],
        title=title or fields["id"],
        status=status,
        version=version,
        timestamp=fields["timestamp"],
        scope=fields.get("scope", ""),
        supersedes=_optional(fields.get("supersedes", "")),
        decision=sections["Decision"],
        rationale=sections.get("Rationale", ""),
        impact=sections.get("Impact", ""),
        path=path,
        superseded_by=_optional(fields.get("superseded by", "")),
    )


def load_decisions(directory: Path) -> list[DecisionRecord]:
    return [parse_decision(path) for path in decision_files(directory)]


def load_history(directory: Path, decision_id: str | None = None) -> list[DecisionRecord]:
    records = [parse_decision(path) for path in history_files(directory)]
    if decision_id is not None:
        records = [r for r in records if r.id == decision_id]
    return sorted(records, key=lambda r: (r.id, r.version))


# Writer: the layout of the existing hand-written records

NOT_STATED_SCOPE = "not stated"
NOT_STATED_IMPACT = "Not stated."
_SLUG_MAX = 40


def slug(title: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return text[:_SLUG_MAX].strip("-") or "decision"


def _line(value: str) -> str:
    return " ".join(value.split())


def _required(value: str | None, what: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{what} must not be empty")
    return text


def render_record(
    decision_id: str,
    title: str,
    version: int,
    timestamp: str,
    scope: str,
    supersedes: str | None,
    content: str,
    rationale: str,
    impact: str,
) -> str:
    """The record in the layout of the existing hand-written records; refuses lossy input."""
    head = _line(_required(title, "title"))
    body = {
        "Decision": _required(content, "decision"),
        "Rationale": _required(rationale, "rationale"),
        "Impact": (impact or "").strip() or NOT_STATED_IMPACT,
    }
    text = "\n".join(
        [
            f"# {decision_id} — {head}",
            "",
            f"- **ID:** {decision_id}",
            f"- **Status:** {DecisionStatus.APPROVED}",
            f"- **Version:** {version}",
            f"- **Timestamp:** {timestamp}",
            f"- **Scope:** {_line(scope or '') or NOT_STATED_SCOPE}",
            f"- **Supersedes:** {supersedes or 'none'}",
            *(part for name, value in body.items() for part in ("", f"## {name}", "", value)),
            "",
        ]
    )
    parsed_title, _, sections = _scan(text.splitlines())
    if parsed_title != head or any(sections.get(k) != v for k, v in body.items()):
        raise ValueError("decision text must not contain Markdown '## ' section headings")
    return text


def mark_superseded(text: str, by: str) -> str:
    """Change only the Status line to superseded and add a Superseded by field line."""
    lines = text.splitlines(keepends=True)
    status_at = supersedes_at = last_field = None
    for index, line in enumerate(lines):
        if line.startswith("## "):
            break
        match = _FIELD.match(line.strip())
        if match:
            last_field = index
            name = match.group(1).strip().lower()
            if name == "status" and status_at is None:
                status_at = index
            elif name == "supersedes":
                supersedes_at = index
    if status_at is None or last_field is None:
        raise ValueError("record has no Status field line")
    ending = lines[status_at][len(lines[status_at].rstrip("\r\n")) :] or "\n"
    lines[status_at] = f"- **Status:** {DecisionStatus.SUPERSEDED}{ending}"
    anchor = supersedes_at if supersedes_at is not None else last_field
    if not lines[anchor].endswith(("\n", "\r")):
        lines[anchor] += ending
    lines.insert(anchor + 1, f"- **Superseded by:** {by}{ending}")
    return "".join(lines)
