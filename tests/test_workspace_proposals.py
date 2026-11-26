"""Proposal isolation, deterministic overlays, transactions and replay."""

import hashlib
import json
import math

import pytest

from harness.cli_spec import spec_store
from harness.dashboard.execution_view import task_execution
from harness.dashboard.view import dashboard_view, open_state
from harness.db import transaction
from harness.design_models import DesignSection
from harness.execution_store import get_execution
from harness.workspace_proposals import ProposalConflict, WorkspaceProposalService, apply_operations
from tests.execution_helpers import approved_execution
from tests.test_dashboard_diagrams import _sample, _state
from tests.test_reliability_store import install_statement_fault


def binding(db_path, task):
    return dashboard_view(db_path, task, None)["task"]["workspace"]


def save(db_path, task, base, operations, rid="request-0001"):
    with open_state(db_path) as controller:
        return WorkspaceProposalService(controller, task).add(
            base,
            operations,
            rid,
            spec_store(controller, task, None),
            lambda: task_execution(controller, task),
        )


def test_persistence_replay_before_staleness_and_conflict(db_path):
    task = _sample(db_path)
    workspace = binding(db_path, task)
    op = [{"type": "requirement", "target": "request", "text": "Proposed clarification"}]
    proposal, created = save(db_path, task, workspace["base"], op)
    assert created and proposal["source"] == workspace["source"]
    assert proposal["result"]["requirements"][0]["text"] == "Proposed clarification"
    assert binding(db_path, task)["source"] == workspace["source"]
    _state(db_path, task, "component Added")
    replay, created = save(db_path, task, workspace["base"], op)
    assert not created and replay == proposal
    with pytest.raises(ProposalConflict):
        save(db_path, task, workspace["base"], [], rid="request-0001")
    with pytest.raises(ProposalConflict):
        save(db_path, task, workspace["base"], op, rid="request-0002")
    assert binding(db_path, task)["proposals"][0]["stale"]


def test_graph_edits_are_deterministic_and_keep_ids(db_path):
    task = _sample(db_path)
    source = binding(db_path, task)["source"]
    node = source["graph"]["nodes"][0]["id"]
    edge = source["graph"]["edges"][0]
    operations = [
        {"type": "node_label", "target": node, "label": "Renamed"},
        {"type": "node_position", "target": node, "x": -12.5, "y": 250},
        {"type": "connection_remove", "target": edge["id"]},
        {
            "type": "connection_add",
            "target": "proposed:new",
            "from": edge["from"],
            "to": edge["to"],
            "label": "New",
        },
    ]
    result = apply_operations(source, operations)
    assert result == apply_operations(source, operations)
    assert (
        result["graph"]["nodes"][0]
        | {"label": source["graph"]["nodes"][0]["label"], "x": 60, "y": 60}
        == source["graph"]["nodes"][0]
    )
    assert edge in source["graph"]["edges"] and edge not in result["graph"]["edges"]


@pytest.mark.parametrize(
    "operation",
    [
        {"type": "node_position", "target": "component:browser", "x": math.nan, "y": 1},
        {"type": "node_position", "target": "component:browser", "x": True, "y": 1},
        {"type": "node_label", "target": "foreign", "label": "x"},
        {
            "type": "connection_add",
            "target": "proposed:new",
            "from": "foreign",
            "to": "foreign",
            "label": "",
        },
        {"type": "plan", "target": "E99999", "text": "x"},
    ],
)
def test_bad_or_foreign_targets_leave_no_records(db_path, operation):
    task = _sample(db_path)
    workspace = binding(db_path, task)
    with pytest.raises((ValueError, LookupError)):
        save(db_path, task, workspace["base"], [operation])
    assert binding(db_path, task)["proposals"] == []


def test_event_failure_rolls_back_proposal(db_path, monkeypatch):
    task = _sample(db_path)
    workspace = binding(db_path, task)
    from harness.dashboard import view

    hits = install_statement_fault(
        monkeypatch,
        view,
        lambda sql, parameters: (
            "insert into events" in " ".join(sql.lower().split())
            and any(value == "workspace.proposed" for value in parameters)
        ),
    )
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        save(
            db_path,
            task,
            workspace["base"],
            [{"type": "requirement", "target": "request", "text": "No partial record"}],
        )
    assert len(hits) == 1
    assert binding(db_path, task)["proposals"] == []


def test_conflicting_operations_and_cross_task_base_are_rejected(db_path):
    task = _sample(db_path)
    other = _sample(db_path)
    workspace = binding(db_path, task)
    with pytest.raises(ProposalConflict):
        save(
            db_path,
            other,
            workspace["base"],
            [{"type": "requirement", "target": "request", "text": "Foreign base"}],
        )
    edit = {"type": "requirement", "target": "request", "text": "One edit"}
    with pytest.raises(ValueError, match="duplicate"):
        save(db_path, task, workspace["base"], [edit, edit | {"text": "Conflicting edit"}])
    assert binding(db_path, task)["proposals"] == []
    assert binding(db_path, other)["proposals"] == []


def test_approved_execution_proposal_leaves_approved_artifacts_and_effective_design_unchanged(
    controller, sample_repo, monkeypatch, db_path
):
    task, execution_id = approved_execution(controller, sample_repo, monkeypatch)
    workspace = controller.workspace(task)
    with transaction(controller.conn):
        workspace.state_design(
            DesignSection.COMPONENTS, "component Protected design: remains effective"
        )

    from harness.spec_store import SpecStore

    spec = SpecStore(sample_repo / "docs/specs" / task).current()
    assert spec is not None and spec.path is not None
    spec_bytes = spec.path.read_bytes()
    spec_hash = hashlib.sha256(spec_bytes).hexdigest()
    approval_rows = controller.conn.execute(
        "SELECT * FROM approvals WHERE task_id=? ORDER BY spec_version", (task,)
    ).fetchall()
    execution_before = get_execution(controller.conn, execution_id)
    effective_before = workspace.design_entries()
    base = binding(db_path, task)

    proposal, created = save(
        db_path,
        task,
        base["base"],
        [{"type": "requirement", "target": "request", "text": "Proposed refinement only"}],
        rid="approved-proposal-0001",
    )

    assert created and proposal["result"]["requirements"][0]["text"] == "Proposed refinement only"
    assert spec.path.read_bytes() == spec_bytes
    assert hashlib.sha256(spec.path.read_bytes()).hexdigest() == spec_hash
    assert (
        controller.conn.execute(
            "SELECT * FROM approvals WHERE task_id=? ORDER BY spec_version", (task,)
        ).fetchall()
        == approval_rows
    )
    execution_after = get_execution(controller.conn, execution_id)
    assert execution_after["binding"] == execution_before["binding"]
    assert (execution_after["epoch_id"], execution_after["authority_generation"]) == (
        execution_before["epoch_id"],
        execution_before["authority_generation"],
    )
    assert workspace.design_entries() == effective_before


def test_proposal_base_rejects_a_new_recorded_execution_epoch_and_generation(
    controller, sample_repo, monkeypatch, db_path
):
    task, execution_id = approved_execution(controller, sample_repo, monkeypatch)
    initial = binding(db_path, task)
    execution = get_execution(controller.conn, execution_id)
    next_epoch = "fixture-successor-epoch"
    next_generation = execution["authority_generation"] + 1
    identity = json.dumps(
        {
            "execution_id": execution_id,
            "epoch_id": next_epoch,
            "authority_generation": next_generation,
            "fixture": "recorded successor; no native worker launched",
        },
        sort_keys=True,
    )
    with transaction(controller.conn):
        controller.conn.execute(
            "UPDATE execution_epochs SET state='ARCHIVED' WHERE execution_id=? AND epoch_id=?",
            (execution_id, execution["epoch_id"]),
        )
        controller.conn.execute(
            "INSERT INTO execution_epochs(execution_id,epoch_id,authority_generation,"
            "state,identity,created_at) "
            "VALUES(?,?,?,'ACTIVE',?,?)",
            (execution_id, next_epoch, next_generation, identity, "fixture-time"),
        )
        controller.conn.execute(
            "UPDATE executions SET epoch_id=?,authority_generation=? WHERE id=?",
            (next_epoch, next_generation, execution_id),
        )

    changed = binding(db_path, task)
    assert changed["source"]["execution"]["epoch_id"] == next_epoch
    assert changed["source"]["execution"]["authority_generation"] == next_generation
    with pytest.raises(ProposalConflict):
        save(
            db_path,
            task,
            initial["base"],
            [{"type": "requirement", "target": "request", "text": "Must remain stale"}],
            rid="stale-epoch-proposal-0001",
        )
    assert binding(db_path, task)["proposals"] == []


def test_workspace_proposal_migration_respects_existing_upstream_version(tmp_path, monkeypatch):
    from harness import db as db_module

    db_path = tmp_path / "upstream-version.db"
    migrations = db_module._migrations()
    baseline = [migration for migration in migrations if migration[0] <= 16]
    monkeypatch.setattr(db_module, "_migrations", lambda: baseline)
    conn = db_module.connect(db_path)
    try:
        existing = conn.execute(
            "SELECT name,applied_at FROM schema_migrations WHERE version=16"
        ).fetchone()[:]
        assert existing[0] == "0016_knowledge_index.sql"
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='workspace_proposals'"
        ).fetchone()
    finally:
        conn.close()

    monkeypatch.setattr(db_module, "_migrations", lambda: migrations)
    conn = db_module.connect(db_path)
    try:
        proposal_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_proposals'"
        ).fetchone()
        reserved_name = conn.execute(
            "SELECT name FROM schema_migrations WHERE version=16"
        ).fetchone()[0]
        assert (
            conn.execute(
                "SELECT name,applied_at FROM schema_migrations WHERE version=16"
            ).fetchone()[:]
            == existing
        )
        assert {
            "proposal_table_created": proposal_table is not None,
            "reserved_migration_name": reserved_name,
        } == {
            "proposal_table_created": True,
            "reserved_migration_name": "0016_knowledge_index.sql",
        }
    finally:
        conn.close()
