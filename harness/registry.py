"""Decision Registry (SPEC §6.4): Git-versioned Markdown records, one file per version.

The current version lives at <dir>/DEC-NNNN-<slug>.md; superseded versions move to
<dir>/history/DEC-NNNN-v<N>-<slug>.md with only their Status line changed and a
Superseded by line added, so the whole chain stays readable from a plain clone.
"""

import os
import re
from pathlib import Path

from harness.db import utcnow
from harness.decisions import (
    HISTORY_DIR,
    DecisionRecord,
    DecisionStatus,
    decision_files,
    history_files,
    load_decisions,
    load_history,
    mark_superseded,
    parse_decision,
    render_record,
    slug,
)

_ID_IN_NAME = re.compile(r"^DEC-(\d+)")


def _create(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8", newline="") as handle:
        handle.write(text)


class DecisionRegistry:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    @property
    def history_dir(self) -> Path:
        return self.directory / HISTORY_DIR

    def records(self) -> list[DecisionRecord]:
        return load_decisions(self.directory)

    def get(self, decision_id: str) -> DecisionRecord:
        found = [r for r in self.records() if r.id == decision_id]
        if not found:
            raise ValueError(f"{decision_id} is not a decision in {self.directory}")
        if len(found) > 1:
            paths = ", ".join(str(r.path) for r in found)
            raise ValueError(f"{decision_id} has more than one current record: {paths}")
        return found[0]

    def history(self, decision_id: str) -> list[DecisionRecord]:
        return load_history(self.directory, decision_id)

    def chain(self, decision_id: str) -> list[DecisionRecord]:
        current = self.get(decision_id)
        return [*self.history(decision_id), current]

    def next_id(self) -> str:
        files = [*decision_files(self.directory), *history_files(self.directory)]
        numbers = [int(m.group(1)) for p in files if (m := _ID_IN_NAME.match(p.name))]
        return f"DEC-{max(numbers, default=0) + 1:04d}"

    def references(self, decision_id: str) -> list[DecisionRecord]:
        pattern = re.compile(rf"\b{re.escape(decision_id)}\b")
        return [
            r
            for r in self.records()
            if r.id != decision_id
            and any(pattern.search(text) for text in (r.decision, r.rationale, r.impact))
        ]

    def record(
        self,
        title: str,
        content: str,
        rationale: str,
        scope: str | None = None,
        impact: str | None = None,
    ) -> DecisionRecord:
        """A brand-new approved decision at version 1; never overwrites an existing file."""
        decision_id = self.next_id()
        text = render_record(
            decision_id, title, 1, utcnow(), scope or "", None, content, rationale, impact or ""
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{decision_id}-{slug(title)}.md"
        try:
            _create(path, text)
        except FileExistsError as error:
            raise ValueError(f"{path} already exists; refusing to overwrite it") from error
        return parse_decision(path)

    def supersede(
        self,
        decision_id: str,
        title: str,
        content: str,
        rationale: str,
        scope: str | None,
        impact: str | None,
        expected_version: int,
    ) -> DecisionRecord:
        """Record version N+1 and move version N, marked superseded, into history/."""
        current = self.get(decision_id)
        if current.status is not DecisionStatus.APPROVED:
            raise ValueError(
                f"{decision_id} is {current.status}; only approved decisions can be changed"
            )
        if current.version != expected_version:
            raise ValueError(f"{decision_id} changed since the proposal (now v{current.version})")
        version = current.version + 1
        text = render_record(
            decision_id,
            title,
            version,
            utcnow(),
            scope or "",
            f"{decision_id} v{current.version}",
            content,
            rationale,
            impact or "",
        )
        old_text = current.path.read_text(encoding="utf-8")
        old_slug = current.path.stem.removeprefix(decision_id).lstrip("-") or slug(current.title)
        archived = mark_superseded(old_text, f"{decision_id} v{version}")
        history_path = self.history_dir / f"{decision_id}-v{current.version}-{old_slug}.md"
        same_title = " ".join(title.split()) == current.title
        name = current.path.name if same_title else f"{decision_id}-{slug(title)}.md"
        new_path = self.directory / name
        if new_path != current.path and new_path.exists():
            raise ValueError(f"{new_path} already exists; refusing to overwrite it")
        self.history_dir.mkdir(parents=True, exist_ok=True)
        try:
            _create(history_path, archived)
        except FileExistsError as error:
            raise ValueError(f"{history_path} already exists; refusing to overwrite it") from error
        staged, created = new_path.with_name(f".{new_path.name}.tmp"), False
        try:
            if new_path == current.path:
                _create(staged, text)
                os.replace(staged, new_path)
            else:
                _create(new_path, text)
                created = True
                current.path.unlink()
        except BaseException:
            staged.unlink(missing_ok=True)
            if created:
                new_path.unlink(missing_ok=True)
            history_path.unlink(missing_ok=True)
            raise
        return parse_decision(new_path)
