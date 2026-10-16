"""Format of the specification files in docs/specs/<task>/ (SPEC §5, §7): reader and writer.

The header holds `- **Field:** value` lines; the body runs from the first `## ` line to the end
of the file. An approved file seals its body with `Content hash: sha256:<hex>`; superseding it
later changes only the header's Status line and adds a Superseded by line.
"""

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from harness.md_record import scan_record

DEFAULT_SPECS_DIR = Path("docs") / "specs"
SPEC_FILE = re.compile(r"^v(\d+)\.md$")
DRAFT_NAME = "draft.md"
TITLE_PREFIX = "Specification — "
NO_REVISION = "none"
_VERSION_REF = re.compile(r"^v(\d+)$")


class SpecStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class SpecRecord:
    task_id: str
    title: str
    status: SpecStatus
    version: int | None
    timestamp: str
    complexity: str
    revision: str | None
    working_tree: str
    supersedes: int | None
    superseded_by: int | None
    content_hash: str | None
    path: Path | None
    body: str

    @property
    def sealed(self) -> bool:
        return self.status is not SpecStatus.DRAFT


class SpecParseError(ValueError):
    def __init__(self, path: Path | None, problem: str):
        self.path = path
        super().__init__(f"{path or 'specification'}: {problem}")


def content_hash(body: str) -> str:
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def split_body(text: str) -> tuple[str, str]:
    """(header, body): the body starts at the first line beginning with '## '."""
    if text.startswith("## "):
        return "", text
    at = text.find("\n## ")
    return (text, "") if at < 0 else (text[: at + 1], text[at + 1 :])


def _ref(value: int | None) -> str:
    return "none" if value is None else f"v{value}"


def _parse_ref(value: str, what: str, path: Path | None) -> int | None:
    if value.lower() in ("", "none", "-"):
        return None
    match = _VERSION_REF.match(value)
    if match is None:
        raise SpecParseError(path, f"invalid {what} {value!r}")
    return int(match.group(1))


def render_spec(
    task_id: str,
    title: str,
    status: SpecStatus,
    version: int | None,
    timestamp: str,
    complexity: str,
    revision: str | None,
    working_tree: str,
    supersedes: int | None,
    body: str,
) -> str:
    """The spec file; refuses input that would not read back exactly (lossy input)."""
    status = SpecStatus(status)
    head = " ".join(title.split())
    if not head:
        raise ValueError("specification title must not be empty")
    if status is SpecStatus.DRAFT and version is not None:
        raise ValueError("a draft specification has no version number")
    if status is not SpecStatus.DRAFT and version is None:
        raise ValueError(f"an {status} specification needs a version number")
    if not body.startswith("## "):
        raise ValueError("specification body must start with a '## ' section")
    sealed = content_hash(body) if status is not SpecStatus.DRAFT else None
    lines = [
        f"# {TITLE_PREFIX}{head}",
        "",
        f"- **Task:** {task_id}",
        f"- **Status:** {status}",
        f"- **Version:** {'draft' if version is None else version}",
        f"- **Timestamp:** {timestamp}",
        f"- **Complexity:** {complexity}",
        f"- **Revision:** {revision or NO_REVISION}",
        f"- **Working tree:** {working_tree}",
        f"- **Supersedes:** {_ref(supersedes)}",
        *([f"- **Content hash:** {sealed}"] if sealed else []),
        "",
        "",
    ]
    text = "\n".join(lines) + body
    record = parse_spec_text(text, None)
    expected = (task_id, head, status, version, timestamp, complexity, revision, working_tree)
    actual = (
        record.task_id,
        record.title,
        record.status,
        record.version,
        record.timestamp,
        record.complexity,
        record.revision,
        record.working_tree,
    )
    if expected != actual or record.supersedes != supersedes or record.body != body:
        raise ValueError("specification header fields must be single-line values")
    return text


def parse_spec_text(text: str, path: Path | None) -> SpecRecord:
    header, body = split_body(text)
    title, fields, _ = scan_record(header.splitlines())
    for name in ("task", "status", "version", "timestamp"):
        if not fields.get(name):
            raise SpecParseError(path, f"missing field {name.title()}")
    try:
        status = SpecStatus(fields["status"].lower())
    except ValueError as error:
        raise SpecParseError(path, f"unknown status {fields['status']!r}") from error
    raw_version = fields["version"]
    if raw_version.lower() == "draft":
        version = None
    elif raw_version.isdigit():
        version = int(raw_version)
    else:
        raise SpecParseError(path, f"invalid version {raw_version!r}")
    if (version is None) != (status is SpecStatus.DRAFT):
        raise SpecParseError(path, f"version {raw_version!r} does not fit status {status}")
    revision = fields.get("revision", "")
    return SpecRecord(
        task_id=fields["task"],
        title=title.removeprefix(TITLE_PREFIX).strip() or fields["task"],
        status=status,
        version=version,
        timestamp=fields["timestamp"],
        complexity=fields.get("complexity", ""),
        revision=None if revision.lower() in ("", NO_REVISION) else revision,
        working_tree=fields.get("working tree", ""),
        supersedes=_parse_ref(fields.get("supersedes", ""), "Supersedes", path),
        superseded_by=_parse_ref(fields.get("superseded by", ""), "Superseded by", path),
        content_hash=fields.get("content hash") or None,
        path=path,
        body=body,
    )


def read_spec_text(path: Path) -> str:
    """The exact file text (no newline translation), as the content hash was computed over."""
    try:
        return Path(path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise SpecParseError(Path(path), f"cannot be read ({error})") from error


def parse_spec(path: Path) -> SpecRecord:
    path = Path(path)
    return parse_spec_text(read_spec_text(path), path)
