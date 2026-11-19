import pytest

from harness.decisions import (
    DecisionStatus,
    history_files,
    load_decisions,
    load_history,
    parse_decision,
    render_record,
    slug,
)
from harness.registry import DecisionRegistry
from tests.sample_repo import APPROVED, SUPERSEDED


@pytest.fixture
def registry(tmp_path):
    directory = tmp_path / "decisions"
    directory.mkdir()
    (directory / "DEC-0001-storage.md").write_text(APPROVED, encoding="utf-8")
    (directory / "DEC-0002-old.md").write_text(SUPERSEDED, encoding="utf-8")
    return DecisionRegistry(directory)


def _change(registry, version, text="Store exports in SQLite."):
    return registry.supersede(
        "DEC-0001", "Storage engine", text, f"why v{version + 1}", "Persistence", "migrate", version
    )


def test_record_round_trips_every_field(tmp_path):
    registry = DecisionRegistry(tmp_path / "new" / "decisions")
    assert registry.next_id() == "DEC-0001"
    record = registry.record(
        "Export   format", "CSV only.\n\nNo XLSX.", "Simple.", "", "Readers parse CSV."
    )
    assert record.path == tmp_path / "new" / "decisions" / "DEC-0001-export-format.md"
    assert (record.id, record.title, record.status, record.version) == (
        "DEC-0001",
        "Export format",
        DecisionStatus.APPROVED,
        1,
    )
    assert (record.scope, record.supersedes, record.superseded_by) == ("not stated", None, None)
    assert (record.decision, record.rationale, record.impact) == (
        "CSV only.\n\nNo XLSX.",
        "Simple.",
        "Readers parse CSV.",
    )
    assert record.timestamp and parse_decision(record.path) == record
    assert registry.record("T", "D", "R").impact == "Not stated."
    assert registry.next_id() == "DEC-0003"


def test_render_refuses_section_headings_inside_text():
    with pytest.raises(ValueError, match="section headings"):
        render_record("DEC-0001", "T", 1, "now", "s", None, "a\n## Rationale\nb", "r", "i")
    with pytest.raises(ValueError, match="rationale must not be empty"):
        render_record("DEC-0001", "T", 1, "now", "s", None, "a", "  ", "i")


def test_slug():
    assert slug("SQLite access!") == "sqlite-access"
    assert slug("???") == "decision"
    assert len(slug("x" * 100)) == 40


def test_supersede_keeps_the_full_chain(registry):
    original = registry.get("DEC-0001").path.read_text(encoding="utf-8")
    second = _change(registry, 1)
    assert (second.version, second.supersedes, second.decision) == (
        2,
        "DEC-0001 v1",
        "Store exports in SQLite.",
    )
    assert [r.id for r in load_decisions(registry.directory)] == ["DEC-0001", "DEC-0002"]
    assert registry.get("DEC-0001") == second

    [first] = load_history(registry.directory, "DEC-0001")
    assert first.path == registry.directory / "history" / "DEC-0001-v1-storage.md"
    assert (first.status, first.version, first.superseded_by) == (
        DecisionStatus.SUPERSEDED,
        1,
        "DEC-0001 v2",
    )
    expected = original.replace("- **Status:** approved", "- **Status:** superseded").replace(
        "- **Supersedes:** none\n", "- **Supersedes:** none\n- **Superseded by:** DEC-0001 v2\n"
    )
    assert first.path.read_text(encoding="utf-8") == expected
    assert (first.decision, first.rationale, first.impact) == (
        "Store exports as files.",
        "Simple.",
        "None.",
    )

    third = _change(registry, 2, "Store exports in Postgres.")
    chain = registry.chain("DEC-0001")
    assert [(r.version, r.status) for r in chain] == [
        (1, DecisionStatus.SUPERSEDED),
        (2, DecisionStatus.SUPERSEDED),
        (3, DecisionStatus.APPROVED),
    ]
    assert [r.supersedes for r in chain] == [None, "DEC-0001 v1", "DEC-0001 v2"]
    assert [r.superseded_by for r in chain] == ["DEC-0001 v2", "DEC-0001 v3", None]
    assert chain[1].decision == "Store exports in SQLite." and chain[1].rationale == "why v2"
    assert chain[-1] == third and len(history_files(registry.directory)) == 2
    assert registry.next_id() == "DEC-0003"


def test_supersede_with_a_new_title_moves_the_current_file(registry):
    record = registry.supersede("DEC-0001", "Database engine", "SQLite.", "r", "", "i", 1)
    assert record.path.name == "DEC-0001-database-engine.md"
    assert not (registry.directory / "DEC-0001-storage.md").exists()
    assert [r.id for r in registry.records()] == ["DEC-0001", "DEC-0002"]


def test_supersede_refusals_write_nothing(registry):
    before = sorted(p.relative_to(registry.directory) for p in registry.directory.rglob("*"))
    with pytest.raises(ValueError, match="DEC-0002 is superseded; only approved"):
        registry.supersede("DEC-0002", "T", "D", "R", "", "", 1)
    with pytest.raises(ValueError, match=r"changed since the proposal \(now v1\)"):
        registry.supersede("DEC-0001", "T", "D", "R", "", "", 2)
    with pytest.raises(ValueError, match="DEC-0042 is not a decision"):
        registry.supersede("DEC-0042", "T", "D", "R", "", "", 1)
    with pytest.raises(ValueError, match="section headings"):
        registry.supersede("DEC-0001", "T", "## Impact", "R", "", "", 1)
    after = sorted(p.relative_to(registry.directory) for p in registry.directory.rglob("*"))
    assert before == after


def test_failed_new_version_restores_the_previous_state(registry, monkeypatch):
    from harness import registry as registry_module

    files = {p: p.read_bytes() for p in registry.directory.rglob("*") if p.is_file()}
    real = registry_module._create

    def fail_on_current(path, text):
        if path.parent == registry.directory:
            raise OSError("disk full")
        real(path, text)

    monkeypatch.setattr(registry_module, "_create", fail_on_current)
    with pytest.raises(OSError, match="disk full"):
        _change(registry, 1)
    assert {p: p.read_bytes() for p in registry.directory.rglob("*") if p.is_file()} == files


def test_references_name_other_current_records(registry):
    citing = registry.record("Exports", "Follow DEC-0001 for storage.", "r")
    registry.record("Unrelated", "Mentions DEC-00012 only.", "r")
    assert [r.id for r in registry.references("DEC-0001")] == [citing.id]
    assert registry.references(citing.id) == []


def test_rework1b_withdrawal_removes_approved_registry_reuse(controller, tmp_path):
    from tests.test_authoring import rework1b_apply, rework1b_service

    service, engine, registry, question, proposal = rework1b_service(
        controller, tmp_path, decision=True
    )
    ident = registry.records()[0].id
    rework1b_apply(
        service,
        f"recommendation:{proposal.id}",
        {"status": "rejected", "response": "Withdraw storage decision"},
    )
    assert registry.get(ident).status == DecisionStatus.REJECTED, (
        "withdrawal left old approved registry authority"
    )
    assert registry.get(ident).version == 2
    assert not engine.summary().requirements


def test_rework1b_registry_transition_compensates_outer_failure(controller, tmp_path, monkeypatch):
    from tests.test_authoring import rework1b_service

    service, _, registry, _, proposal = rework1b_service(controller, tmp_path, decision=True)
    ident = registry.records()[0].id
    target = next(
        row
        for row in service.snapshot()["revision_targets"]
        if row["target"] == f"recommendation:{proposal.id}"
    )
    revised = service.revise_record(
        expected=service.store.base(),
        request_id="rework1b-rollback-revise",
        target=target["target"],
        target_revision=target["revision"],
        value={"status": "modified", "response": "NEW rollback storage"},
    )
    before_files = {
        str(path.relative_to(registry.directory)): path.read_bytes()
        for path in registry.directory.rglob("*")
        if path.is_file()
    }
    before_base = service.store.base()
    observed = []

    def failed_receipt(*args, **kwargs):
        observed.append(registry.get(ident).version)
        raise RuntimeError("injected after registry replacement")

    monkeypatch.setattr(service.store, "record_receipt", failed_receipt)
    with pytest.raises(RuntimeError, match="registry replacement"):
        service.planning(
            expected=before_base,
            request_id="rework1b-rollback-apply",
            operation={
                "op": "revision-apply",
                "target": target["target"],
                "target_revision": revised["contribution"],
            },
        )
    assert observed == [2], "correction did not replace the original registry version"
    assert service.store.base() == before_base
    assert {
        str(path.relative_to(registry.directory)): path.read_bytes()
        for path in registry.directory.rglob("*")
        if path.is_file()
    } == before_files


def test_rework1b_commit_failure_restores_registry_and_database(controller, tmp_path, monkeypatch):
    import sqlite3

    from tests.test_authoring import rework1b_service

    service, _, registry, _, proposal = rework1b_service(controller, tmp_path, decision=True)
    target = next(
        row
        for row in service.snapshot()["revision_targets"]
        if row["target"] == f"recommendation:{proposal.id}"
    )
    revised = service.revise_record(
        expected=service.store.base(),
        request_id="rework1b-commit-revise",
        target=target["target"],
        target_revision=target["revision"],
        value={"status": "modified", "response": "NEW commit storage"},
    )
    before = service.store.base()
    files = {
        str(p.relative_to(registry.directory)): p.read_bytes()
        for p in registry.directory.rglob("*")
        if p.is_file()
    }

    class CommitFailure:
        def __getattr__(self, name):
            return getattr(controller.conn, name)

        def execute(self, sql, *args):
            if sql == "COMMIT":
                raise sqlite3.OperationalError("injected commit failure")
            return controller.conn.execute(sql, *args)

    monkeypatch.setattr(service, "conn", CommitFailure())
    with pytest.raises(sqlite3.OperationalError, match="commit failure"):
        service.planning(
            expected=before,
            request_id="rework1b-commit-apply",
            operation={
                "op": "revision-apply",
                "target": target["target"],
                "target_revision": revised["contribution"],
            },
        )
    assert not controller.conn.in_transaction
    assert service.store.base() == before
    assert {
        str(p.relative_to(registry.directory)): p.read_bytes()
        for p in registry.directory.rglob("*")
        if p.is_file()
    } == files


def test_rework1b_registry_write_failure_restores_exact_files(controller, tmp_path, monkeypatch):
    from tests.test_authoring import rework1b_service

    service, _, registry, _, proposal = rework1b_service(controller, tmp_path, decision=True)
    target = next(
        row
        for row in service.snapshot()["revision_targets"]
        if row["target"] == f"recommendation:{proposal.id}"
    )
    revised = service.revise_record(
        expected=service.store.base(),
        request_id="rework1b-write-revise",
        target=target["target"],
        target_revision=target["revision"],
        value={"status": "modified", "response": "NEW failed write"},
    )
    before = service.store.base()
    files = {
        str(p.relative_to(registry.directory)): p.read_bytes()
        for p in registry.directory.rglob("*")
        if p.is_file()
    }
    from harness import registry as module

    original = module._create

    def failed(path, text):
        original(path, text)
        raise OSError("injected after exclusive file creation")

    monkeypatch.setattr(module, "_create", failed)
    with pytest.raises(OSError):
        service.planning(
            expected=before,
            request_id="rework1b-write-apply",
            operation={
                "op": "revision-apply",
                "target": target["target"],
                "target_revision": revised["contribution"],
            },
        )
    assert service.store.base() == before
    assert {
        str(p.relative_to(registry.directory)): p.read_bytes()
        for p in registry.directory.rglob("*")
        if p.is_file()
    } == files


def test_rework1b_current_rejected_record_cannot_be_reused_from_old_investigation(
    controller, tmp_path
):
    from harness.analyzer import load_approved
    from harness.db import transaction
    from harness.decision_workflow import DecisionWorkflow
    from harness.investigation import _decision_dict
    from tests.test_authoring import rework1b_apply, rework1b_service

    service, engine, registry, _, proposal = rework1b_service(controller, tmp_path, decision=True)
    record = registry.records()[0]
    with transaction(controller.conn):
        engine.ws.checkpoint(
            "investigation", {"repo_root": str(tmp_path), "decisions": [_decision_dict(record)]}
        )
    flow = DecisionWorkflow(controller, service.task_id, registry)
    change, _ = flow.change(record.id, "Unapplied change", "Reason", "Impact")
    rework1b_apply(
        service,
        f"recommendation:{proposal.id}",
        {"status": "rejected", "response": "Withdraw actual authority"},
    )
    assert record.id not in engine._approved_decision_ids()
    with pytest.raises(ValueError, match="rejected"):
        flow.preview(change)
    approved, _ = load_approved(tmp_path, registry.directory)
    assert not approved
    assert record.id not in service.manager().structured_body()
