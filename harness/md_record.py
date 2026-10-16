"""Shared layout of the Markdown records: `- **Field:** value` header lines and `## ` sections."""

import re

FIELD = re.compile(r"^- \*\*(\w[\w ]*):\*\*\s*(.*)$")
_TITLE_PREFIX = re.compile(r"^DEC-\d+\s*[—–-]\s*")
SUPERSEDED = "superseded"


def scan_record(lines: list[str]) -> tuple[str, dict[str, str], dict[str, str]]:
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
        match = FIELD.match(line.strip())
        if match:
            fields[match.group(1).strip().lower()] = match.group(2).strip()
    return title, fields, {name: "\n".join(body).strip() for name, body in sections.items()}


def mark_superseded(text: str, by: str) -> str:
    """Change only the Status line to superseded and add a Superseded by field line."""
    lines = text.splitlines(keepends=True)
    status_at = supersedes_at = last_field = None
    for index, line in enumerate(lines):
        if line.startswith("## "):
            break
        match = FIELD.match(line.strip())
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
    lines[status_at] = f"- **Status:** {SUPERSEDED}{ending}"
    anchor = supersedes_at if supersedes_at is not None else last_field
    if not lines[anchor].endswith(("\n", "\r")):
        lines[anchor] += ending
    lines.insert(anchor + 1, f"- **Superseded by:** {by}{ending}")
    return "".join(lines)
