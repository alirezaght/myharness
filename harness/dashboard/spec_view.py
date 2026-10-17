"""The spec view (P2-03): the current spec rendered, and every version with its body diff.

The current spec follows SpecStore.current (highest approved version), else draft.md, else the
highest version file. Diffs cover the body only, which is what the content hash seals.
"""

import difflib
from typing import Any

from harness.cli_spec import _as_dict
from harness.dashboard.markdown import parse_markdown
from harness.spec_record import SpecParseError, SpecRecord, SpecStatus, read_spec_text
from harness.spec_store import SpecStore

DRAFT_LABEL = "draft"
KINDS = {"@": "hunk", "+": "add", "-": "remove", " ": "context"}


def _label(record: SpecRecord) -> str:
    return DRAFT_LABEL if record.version is None else f"v{record.version}"


def body_diff(old: SpecRecord, new: SpecRecord) -> dict[str, Any]:
    """Unified diff of the two bodies; the ---/+++ file header lines are dropped by position."""
    raw = list(difflib.unified_diff(old.body.splitlines(), new.body.splitlines(), lineterm="", n=3))
    lines = [{"kind": KINDS[line[0]], "text": line} for line in raw[2:]]
    return {
        "from": _label(old),
        "to": _label(new),
        "added": sum(line["kind"] == "add" for line in lines),
        "removed": sum(line["kind"] == "remove" for line in lines),
        "lines": lines,
    }


def _entry(record: SpecRecord, previous: SpecRecord | None) -> dict[str, Any]:
    diff = None if previous is None else body_diff(previous, record)
    return _as_dict(record) | {"label": _label(record), "diff": diff}


def _history(records: list[SpecRecord], draft: SpecRecord | None) -> list[dict[str, Any]]:
    entries, previous = [], None
    for record in records:
        entries.append(_entry(record, previous))
        previous = record
    if draft is not None:
        entries.append(_entry(draft, previous))
    return entries


def _shown(records: list[SpecRecord], draft: SpecRecord | None) -> SpecRecord | None:
    approved = [r for r in records if r.status is SpecStatus.APPROVED]
    if approved:
        return max(approved, key=lambda r: r.version or 0)
    return draft or (records[-1] if records else None)


def spec_section(store: SpecStore) -> dict[str, Any]:
    """A malformed v<N>.md fails the view (as before); a bad draft only marks the section."""
    records = store.records()
    section: dict[str, Any] = {
        "directory": str(store.directory),
        "versions": [{"version": r.version, "status": str(r.status)} for r in records],
        "draft": store.draft_path.is_file(),
        "shown": None,
        "history": [],
        "unavailable": None,
    }
    draft = None
    try:
        draft = store.draft()
    except SpecParseError as error:
        section["unavailable"] = str(error)
    section["history"] = _history(records, draft)
    record = _shown(records, draft)
    if record is None or record.path is None:
        return section
    try:
        blocks = parse_markdown(read_spec_text(record.path))
    except SpecParseError as error:
        section["unavailable"] = str(error)
        return section
    section["shown"] = {
        "label": _label(record),
        "status": str(record.status),
        "version": record.version,
        "path": str(record.path),
        "blocks": blocks,
    }
    return section
