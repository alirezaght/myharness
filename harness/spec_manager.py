"""Specification Manager (SPEC §5, §7): draft -> approved -> superseded specs tied to a commit.

`preview` and `verify` write nothing. `draft` writes only draft.md; `approve` writes only the new
v<N>.md, the header of the previous version, and removes draft.md. Each mutation is one
transaction with an event and a `spec` checkpoint; a failed step restores every file it touched.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from harness.controller import HarnessController
from harness.db import transaction, utcnow
from harness.decisions import DecisionRecord, load_history, parse_decision
from harness.design import ArchitectureDesigner
from harness.interview import InterviewEngine
from harness.phases import Phase
from harness.repo_scan import git_state
from harness.review import FinalReviewer
from harness.review_report import ReviewReport
from harness.spec_document import build_body
from harness.spec_record import (
    SpecRecord,
    SpecStatus,
    content_hash,
    parse_spec,
    render_spec,
    split_body,
)
from harness.spec_store import SpecStore
from harness.state_specs import SpecVersion
from harness.testplan import TestPlanner

RECORDED_EVENTS = ("decision.recorded", "decision.superseded")
OK = "ok"
HASH_MISMATCH = "MODIFIED (content hash mismatch)"
DB_MISMATCH = "MODIFIED (differs from the local approval record)"
NOT_SEALED = "(not sealed)"


OnSealed = Callable[[dict, ReviewReport], None]
Guard = Callable[[ReviewReport], None]


class SpecError(ValueError):
    pass


def _check(record: SpecRecord, number: int, row: SpecVersion | None) -> str:
    if record.version != number:
        return f"MODIFIED (Version {record.version} does not match the file name)"
    if not record.content_hash or record.content_hash != content_hash(record.body):
        return HASH_MISMATCH
    if row is not None and (row.content_hash, row.revision) != (
        record.content_hash,
        record.revision,
    ):
        return DB_MISMATCH
    return OK


def verify_store(store: SpecStore, rows: list[SpecVersion] | None = None) -> list[tuple[str, str]]:
    """(label, result) per file; rows are the local approval records, None on a plain clone."""
    recorded = {row.version: row for row in rows or []}
    results = []
    for number, path in store.version_files():
        row = recorded.pop(number, None)
        try:
            record = parse_spec(path)
        except ValueError as error:
            results.append((f"v{number}", f"MODIFIED (cannot be parsed: {error})"))
            continue
        results.append((f"v{number}", _check(record, number, row)))
    results += [(f"v{n}", "MODIFIED (file missing; recorded locally)") for n in sorted(recorded)]
    if store.draft_path.is_file():
        results.append(("draft", NOT_SEALED))
    return results


def _recorded_decisions(events: list) -> list[DecisionRecord]:
    found: dict[tuple[str, int], DecisionRecord] = {}
    for event in events:
        if event.kind not in RECORDED_EVENTS or not event.payload.get("path"):
            continue
        ident, version, path = (
            event.payload["id"],
            event.payload["version"],
            Path(event.payload["path"]),
        )
        candidates = [parse_decision(path)] if path.is_file() else []
        candidates += load_history(path.parent, ident) if path.parent.is_dir() else []
        for record in candidates:
            if (record.id, record.version) == (ident, version):
                found.setdefault((ident, version), record)
                break
    return list(found.values())


class SpecificationManager:
    def __init__(
        self, controller: HarnessController, task_id: str, store: SpecStore, repo_root: Path
    ):
        self.controller = controller
        self.conn = controller.conn
        self.task_id = controller.get_task(task_id).id
        self.ws = controller.workspace(task_id)
        self.store = store
        self.repo_root = Path(repo_root)

    # Read-only

    def body(self, *, proposed=False) -> str:
        engine = InterviewEngine(self.controller, self.task_id)
        designer = ArchitectureDesigner(self.controller, self.task_id)
        checkpoint = self.ws.latest_checkpoint("investigation")
        decisions = checkpoint.payload.get("decisions", []) if checkpoint else []
        recorded = _recorded_decisions(self.ws.events())
        plan = TestPlanner(self.controller, self.task_id).document()
        summary, design = engine.summary(), designer.document()
        body = build_body(summary, design, decisions, recorded, self.ws.proposals(), plan)
        if proposed:
            from harness.spec_document import proposed_content

            body = proposed_content(
                body, self.ws.proposals(), self.ws.design_entries(), self.ws.scenarios(), plan
            )
        return body

    def render(
        self,
        status: SpecStatus,
        version: int | None,
        revision: str | None,
        working_tree: str,
        supersedes: int | None,
    ) -> str:
        task = self.controller.get_task(self.task_id)
        return render_spec(
            task.id,
            task.request,
            status,
            version,
            utcnow(),
            f"{task.complexity} ({task.complexity_source})",
            revision,
            working_tree,
            supersedes,
            self.body(proposed=status is SpecStatus.DRAFT),
        )

    def preview(self) -> str:
        return self._draft_text()[0]

    def _draft_text(self) -> tuple[str, str | None]:
        state, current = git_state(self.repo_root), self.store.current()
        supersedes = current.version if current else None
        text = self.render(SpecStatus.DRAFT, None, state.revision, state.working_tree, supersedes)
        return text, state.revision

    def verify(self) -> list[tuple[str, str]]:
        return verify_store(self.store, self.ws.spec_versions())

    # Mutations

    def draft(self) -> Path:
        path = self.store.draft_path
        previous = path.read_bytes().decode("utf-8") if path.is_file() else None
        try:
            with self._mutation():
                text, revision = self._draft_text()
                self.ws.record_event("spec.drafted", {"path": str(path), "revision": revision})
                self.ws.checkpoint("spec", {"status": "draft", "path": str(path)})
                self.store.write_draft(text)
        except BaseException:
            if previous is None:
                path.unlink(missing_ok=True)
            elif path.is_file():
                self.store.restore(path, previous)
            raise
        return path

    def approve(
        self, on_sealed: OnSealed | None = None, guard: Guard | None = None
    ) -> tuple[SpecRecord, SpecRecord | None]:
        """Write v<N>.md sealed by its content hash and supersede v<N-1>; returns both.

        `on_sealed(payload, report)` runs last inside the same transaction; if it raises,
        every file and row of this approval is rolled back. `guard(report)` runs inside the
        transaction after the phase check and before anything is read from git or written;
        if it raises, nothing is written.
        """
        created: list[Path] = []
        restores: list[tuple[Path, str]] = []
        try:
            with self._mutation():
                if guard is None:
                    previous = self._approve_in_transaction(created, restores, on_sealed)
                else:
                    previous = self._approve_in_transaction(
                        created, restores, on_sealed, guard=guard
                    )
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            for path, text in restores:
                self.store.restore(path, text)
            raise
        return parse_spec(created[0]), (parse_spec(previous.path) if previous else None)

    def _approve_in_transaction(
        self,
        created: list[Path],
        restores: list[tuple[Path, str]],
        on_sealed: OnSealed | None = None,
        guard: Guard | None = None,
    ) -> SpecRecord | None:
        task = self.controller.get_task(self.task_id)
        if task.phase is not Phase.APPROVAL:
            raise SpecError(
                f"task {task.id} is in phase {task.phase}; run: harness phase {task.id} next"
            )
        report = FinalReviewer(self.controller, self.task_id).report()
        if guard is not None:
            guard(report)
        if report.blocking:
            raise SpecError("final design review blocks approval:\n" + report.render())
        state = git_state(self.repo_root)
        if state.revision is None:
            raise SpecError(
                "approval needs a committed repository revision (git rev-parse HEAD found none);"
                " the spec cannot be tied to a commit"
            )
        version, previous = self.store.next_version(), self.store.current()
        before = previous.version if previous else None
        text = self.render(SpecStatus.APPROVED, version, state.revision, state.working_tree, before)
        path, sealed = self.store.version_path(version), content_hash(split_body(text)[1])
        created.append(self.store.write_version(version, text))
        self.ws.record_spec_version(version, str(path), state.revision, state.working_tree, sealed)
        if previous is not None and self.ws.spec_version(before) is not None:
            self.ws.mark_spec_superseded(before, version)
        payload = {"version": version, "path": str(path), "revision": state.revision}
        payload |= {"working_tree": state.working_tree, "content_hash": sealed}
        self.ws.record_event("spec.approved", {**payload, "supersedes": before})
        self.ws.checkpoint(
            "spec",
            {"status": "approved", "version": version, "path": str(path), "content_hash": sealed},
        )
        if previous is not None and previous.path is not None:
            old = self.store.supersede_header(previous, version)
            restores.append((previous.path, old))
        draft = self.store.draft_path
        if draft.is_file():
            restores.append((draft, draft.read_bytes().decode("utf-8")))
            draft.unlink()
        if on_sealed is not None:
            on_sealed(dict(payload), report)
        return previous

    @contextmanager
    def _mutation(self) -> Iterator[None]:
        with transaction(self.conn):
            self.controller.get_task(self.task_id)
            yield
            self.conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?", (utcnow(), self.task_id)
            )
