# ruff: noqa: F811
"""Publication fences, effect boundaries and genuine runtime activation races."""

import sys
from pathlib import Path

import pytest

from harness.execution_epoch_lock import epoch_action_lock
from harness.execution_models import ExecutionConflict
from harness.execution_store import get_execution
from harness.reliability import store
from tests.test_reliability_store import install_statement_fault, observed, rows  # noqa: F401
from tests.test_template_classification_integration import operations, registered  # noqa: F401


def test_dispatch_releases_sqlite_and_epoch_claim_before_effect(observed, monkeypatch):
    operation, principal, controller = observed
    (Path(operation.envelope["repository"]) / "public.txt").write_text("safe fixture\n")
    effects = []

    def effect(*args):
        execution = get_execution(controller.conn, operation.execution_id)
        # Exclusive claim and independent write prove neither resource is held by publication.
        with epoch_action_lock(execution, exclusive=True):
            controller.conn.execute("BEGIN IMMEDIATE")
            controller.conn.execute("ROLLBACK")
        effects.append(True)
        return {"status": "Running"}

    monkeypatch.setattr(operation, "_dispatch", effect)
    operation.dispatch(principal, "read_file", {"path": "public.txt"})
    assert effects == [True] and rows(observed)[0]["code"] == "RETURNED"


def test_terminal_failure_after_effect_never_replays(observed, monkeypatch):
    operation, principal, _ = observed
    (Path(operation.envelope["repository"]) / "public.txt").write_text("safe fixture\n")
    hits = install_statement_fault(
        monkeypatch,
        store,
        lambda sql, _parameters: (
            "update reliability_observations set state=" in " ".join(sql.lower().split())
        ),
    )
    effects = []
    monkeypatch.setattr(operation, "_dispatch", lambda *a: effects.append(True) or {"ok": True})
    with pytest.raises(ValueError, match="Reliability recording unavailable"):
        operation.dispatch(principal, "read_file", {"path": "public.txt"})
    assert len(hits) == 1
    assert effects == [True] and rows(observed)[0]["state"] == "pending"
    assert not rows(observed, "reliability_incidents")


def test_stale_controller_cannot_start_or_finalize(observed):
    operation, principal, controller = observed
    oid = store.begin(operation, principal, "read_file")
    controller.conn.execute(
        "UPDATE executions SET authority_generation=1 WHERE id=?", (operation.execution_id,)
    )
    with pytest.raises(ExecutionConflict, match="stale_epoch"):
        store.finish(operation, oid, "RETURNED")
    with pytest.raises(ExecutionConflict, match="stale_epoch"):
        store.begin(operation, principal, "read_file")
    assert len(rows(observed)) == 1 and rows(observed)[0]["state"] == "pending"


@pytest.mark.skipif(sys.platform != "darwin", reason="genuine macOS runtime activation")
def test_actual_active_epoch_transition_between_effect_and_terminal(
    registered, monkeypatch, tmp_path
):
    from harness.execution_runtime_recovery import activate_runtime, prepare_runtime
    from harness.execution_store import bind_host
    from tests.test_execution_operations import observed_principal

    operation, _, controller, _, _, execution, _ = registered[0]
    root = operation.registry.root
    principal = observed_principal(operation, root, "orchestrator")
    bind_host(controller.conn, execution["id"], root, "test-owner")
    monkeypatch.chdir(tmp_path)
    prepared = prepare_runtime(
        controller,
        execution["id"],
        expected_epoch_id="legacy-0",
        expected_authority_generation=0,
        request_id="reliability-transition",
        reason="Repair disposable fixture runtime at the observation publication boundary",
    )
    effects, activations = [], []

    def effect(*args):
        effects.append(True)
        activations.append(
            activate_runtime(
                controller,
                execution["id"],
                request_id="reliability-transition",
                preparation_sha256=prepared["preparation_sha256"],
                expected_epoch_id="legacy-0",
                expected_authority_generation=0,
            )
        )
        return {"status": "Running"}

    monkeypatch.setattr(operation, "_dispatch", effect)
    with pytest.raises(ExecutionConflict, match="stale_epoch"):
        operation.dispatch(principal, "read_file", {"path": "unused"})
    assert effects == [True] and activations[0]["state"] == "ACTIVE"
    activated = activations[0]
    with pytest.raises(ExecutionConflict, match="stale_epoch"):
        operation.dispatch(principal, "read_file", {"path": "unused"})
    result = controller.conn.execute("SELECT * FROM reliability_observations").fetchall()
    assert len(result) == 1 and result[0]["state"] == "pending"
    assert result[0]["epoch_id"] == "legacy-0"
    assert controller.conn.execute("SELECT COUNT(*) FROM reliability_incidents").fetchone()[0] == 0
    assert get_execution(controller.conn, execution["id"])["epoch_id"] == activated["epoch_id"]

    from harness.dashboard.reliability_view import read_incidents
    from tests.test_reliability_api import query_for

    # Old evidence remains explicitly readable, never appended after retirement.
    status, data = read_incidents(operation.database, query_for((operation, principal, controller)))
    assert status == 200 and len(data["pending"]) == 1 and not data["items"]
    operation.epoch_id = activated["epoch_id"]
    operation.authority_generation = get_execution(controller.conn, execution["id"])[
        "authority_generation"
    ]
    controller.conn.execute(
        "UPDATE execution_epochs SET state='RETIRED' WHERE execution_id=? AND epoch_id=?",
        (execution["id"], operation.epoch_id),
    )
    with pytest.raises(ExecutionConflict, match="requires recovery"):
        store.begin(operation, principal, "read_file")
    assert len(controller.conn.execute("SELECT * FROM reliability_observations").fetchall()) == 1


def test_crash_after_pending_commit_before_effect_preserves_unknown(observed, monkeypatch):
    operation, principal, _ = observed
    original = store.begin
    effects = []

    def crash(*args):
        original(*args)
        raise RuntimeError("simulated crash after pending commit")

    monkeypatch.setattr(store, "begin", crash)
    monkeypatch.setattr(operation, "_dispatch", lambda *a: effects.append(True))
    with pytest.raises(RuntimeError, match="simulated crash"):
        operation.dispatch(principal, "read_file", {})
    assert not effects and rows(observed)[0]["state"] == "pending"
    assert not rows(observed, "reliability_incidents")
