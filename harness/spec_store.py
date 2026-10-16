"""The files of one task's specification: docs/specs/<task>/draft.md and v1.md, v2.md, ...

Approved files are created exclusively and never rewritten; superseding changes only the
header of the previous version. No database access here.
"""

import os
from pathlib import Path

from harness.md_record import mark_superseded
from harness.spec_record import (
    DRAFT_NAME,
    SPEC_FILE,
    SpecRecord,
    SpecStatus,
    parse_spec,
    read_spec_text,
)


def _replace(path: Path, text: str) -> None:
    staged = path.with_name(f".{path.name}.tmp")
    try:
        with staged.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


class SpecStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    @property
    def draft_path(self) -> Path:
        return self.directory / DRAFT_NAME

    def version_path(self, version: int) -> Path:
        return self.directory / f"v{int(version)}.md"

    def version_files(self) -> list[tuple[int, Path]]:
        if not self.directory.is_dir():
            return []
        found = [
            (int(match.group(1)), path)
            for path in self.directory.iterdir()
            if path.is_file() and (match := SPEC_FILE.match(path.name))
        ]
        return sorted(found)

    def records(self) -> list[SpecRecord]:
        return [parse_spec(path) for _, path in self.version_files()]

    def draft(self) -> SpecRecord | None:
        return parse_spec(self.draft_path) if self.draft_path.is_file() else None

    def current(self) -> SpecRecord | None:
        approved = [r for r in self.records() if r.status is SpecStatus.APPROVED]
        return max(approved, key=lambda r: r.version or 0) if approved else None

    def next_version(self) -> int:
        return max((number for number, _ in self.version_files()), default=0) + 1

    def write_draft(self, text: str) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        _replace(self.draft_path, text)
        return self.draft_path

    def write_version(self, version: int, text: str) -> Path:
        path = self.version_path(version)
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8", newline="") as handle:
                handle.write(text)
        except FileExistsError as error:
            raise ValueError(f"{path} already exists; refusing to overwrite it") from error
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path

    def supersede_header(self, record: SpecRecord, by: int) -> str:
        """Mark an approved version superseded in its header only; returns the previous text."""
        if record.status is not SpecStatus.APPROVED or record.path is None:
            raise ValueError(f"v{record.version} is {record.status}; only approved specs supersede")
        before = read_spec_text(record.path)
        _replace(record.path, mark_superseded(before, f"v{int(by)}"))
        return before

    def restore(self, path: Path, text: str) -> None:
        _replace(path, text)
