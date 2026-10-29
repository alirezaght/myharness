"""Versioned tool repair retains real protected AL origin and execution authority."""

import json
import shutil
from pathlib import Path

import pytest

from harness.execution_bridge import bridge_root, selected_bridge, upgrade_bridge, verify_snapshot
from harness.execution_contract import canonical, digest
from harness.execution_models import ExecutionConflict
from harness.execution_store import get_execution
from tests.test_execution_operations import operations as operation_fixture


@pytest.fixture
def operations(controller, monkeypatch, launched, request):
    # These maintenance/read-only operations launch no contained child commands;
    # genuine managed AL initialization still runs and validates protected origin.
    yield from operation_fixture.__wrapped__(controller, monkeypatch, launched, request)


def candidate(operations, monkeypatch, tmp_path):
    import harness.execution_bootstrap as bootstrap
    import harness.execution_hosts as hosts

    operation, principal, controller, initialization, package, execution, envelope = operations
    # Publish a different bridge from a real independent source tree. Never alter
    # an existing trusted snapshot, receipt or AL package pin.
    source = tmp_path / "bridge-source" / "harness"
    shutil.copytree(
        Path(bootstrap.__file__).parent, source, ignore=shutil.ignore_patterns("__pycache__")
    )
    with (source / "__init__.py").open("a") as stream:
        stream.write("\n# Distinct maintenance candidate for upgrade regression.\n")
    monkeypatch.setattr(bootstrap, "__file__", str(source / "execution_bootstrap.py"))
    monkeypatch.setattr(hosts, "managed_launcher", lambda: package["launcher"])
    controller.conn.execute(
        "UPDATE executions SET native_id=? WHERE id=?", (principal["thread_id"], execution["id"])
    )
    return controller, get_execution(controller.conn, execution["id"]), initialization


def test_upgrade_preserves_approval_origin_pin_and_old_bridge(operations, monkeypatch, tmp_path):
    controller, execution, initialization = candidate(operations, monkeypatch, tmp_path)
    old = bridge_root(execution)
    old_bytes = {p: p.read_bytes() for p in old.rglob("*") if p.is_file()}
    protected = {
        key: Path(initialization[key]).read_bytes()
        for key in ("binding_path", "initialization_path", "source")
    }
    result = upgrade_bridge(
        controller, execution["id"], reason="Expose required bounded pinned reference documents"
    )
    assert result["status"] == "Upgraded"
    assert bridge_root(execution) == Path(result["bridge"]) != old
    assert verify_snapshot(old) != verify_snapshot(bridge_root(execution))
    assert get_execution(controller.conn, execution["id"]) == execution
    assert all(p.read_bytes() == raw for p, raw in old_bytes.items())
    assert all(Path(initialization[k]).read_bytes() == raw for k, raw in protected.items())
    receipt = json.loads(Path(result["receipt"]).read_bytes())
    assert receipt["native_id"] == execution["native_id"]
    assert receipt["al_run_id"] == execution["al_run_id"]
    assert result["resume_required"] is True
    replay = upgrade_bridge(
        controller, execution["id"], reason="Confirm same bridge candidate is already selected"
    )
    assert replay["status"] == "Current"


def test_failed_upgrade_event_restores_old_selection(operations, monkeypatch, tmp_path):
    from harness.state import TaskWorkspace

    controller, execution, _ = candidate(operations, monkeypatch, tmp_path)
    directory = Path(execution["handoff_path"]).parent
    original = bridge_root(execution)

    def fail(*args, **kwargs):
        raise RuntimeError("simulated ledger publication failure")

    monkeypatch.setattr(TaskWorkspace, "record_event", fail)
    with pytest.raises(RuntimeError, match="publication failure"):
        upgrade_bridge(
            controller, execution["id"], reason="Fix portable pinned skill reference reads"
        )
    assert not (directory / "bridge-selection.json").exists()
    assert bridge_root(execution) == original
    assert list(
        (directory / "bridge-upgrades").glob("*.json")
    )  # immutable unselected audit receipt


def test_commit_failure_rolls_back_ledger_and_selection(operations, monkeypatch, tmp_path):
    import sqlite3

    controller, execution, _ = candidate(operations, monkeypatch, tmp_path)
    original = bridge_root(execution)
    before = controller.workspace(execution["task_id"]).events()

    def authorizer(action, first, second, database, trigger):
        if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    controller.conn.set_authorizer(authorizer)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="authorized"):
            upgrade_bridge(
                controller,
                execution["id"],
                reason="Safely repair bounded portable skill reference access",
            )
    finally:
        controller.conn.set_authorizer(None)
    assert not controller.conn.in_transaction
    assert controller.workspace(execution["task_id"]).events() == before
    assert bridge_root(execution) == original


def test_live_attempt_refuses_upgrade_before_snapshot_publication(
    operations, monkeypatch, tmp_path
):
    controller, execution, _ = candidate(operations, monkeypatch, tmp_path)
    # An uncertain attempt is also refused even when no child process is present.
    from harness.db import utcnow

    controller.conn.execute(
        "INSERT INTO execution_attempts "
        "(id,execution_id,token,mode,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
        (
            "attempt-fixture",
            execution["id"],
            "test-owner",
            "resume",
            "Reserved",
            utcnow(),
            utcnow(),
        ),
    )
    with pytest.raises(ExecutionConflict, match="settle recorded native attempts"):
        upgrade_bridge(
            controller, execution["id"], reason="Fix portable pinned skill reference reads"
        )
    assert not (Path(execution["handoff_path"]).parent / "bridge-selection.json").exists()


def test_selected_bridge_rejects_traversal_before_reading_candidate(tmp_path):
    directory = tmp_path.resolve()
    receipt = directory / "bridge-upgrades" / "receipt.json"
    receipt.parent.mkdir()
    value = {
        "schema_version": 1,
        "kind": "execution-bridge-upgrade-v1",
        "from": "a" * 64,
        "to": "../../../outside",
    }
    raw = canonical(value).encode()
    receipt.write_bytes(raw)
    (directory / "bridge-selection.json").write_text(
        canonical({"receipt": "bridge-upgrades/receipt.json", "sha256": digest(raw)})
    )
    with pytest.raises(ExecutionConflict, match="unsupported bridge upgrade receipt"):
        selected_bridge(directory)


def test_pinned_references_are_readable_but_confined(operations):
    operation, principal, _, initialization, _, _, _ = operations
    index = operation.dispatch(
        principal, "read_file", {"path": "harness:skill/index", "limit": 65536}
    )
    resources = json.loads(index["text"])["resources"]
    assert "harness:skill/reference/handoffs.md" in resources
    assert "harness:skill/reference/schemas/architect-report.json" in resources
    for resource in (
        "harness:skill/reference/handoffs.md",
        "harness:skill/reference/schemas/architect-report.json",
    ):
        result = operation.dispatch(principal, "read_file", {"path": resource, "limit": 65536})
        assert result["text"] and not result["truncated"]
        assert result["pin_identity"] == Path(initialization["pin_root"]).name
    for resource in (
        "harness:skill/reference/../../scripts/al_state.py",
        "harness:skill/reference/unknown.md",
        "harness:skill/scripts/al_state.py",
    ):
        with pytest.raises(ValueError):
            operation.dispatch(principal, "read_file", {"path": resource})


@pytest.mark.parametrize("action,focus", [("recover", "tooling"), ("retry-checks", "verification")])
def test_dashboard_recovery_upgrades_before_focused_resume(
    operations, monkeypatch, tmp_path, action, focus
):
    from harness.execution_controls import recover_and_continue

    controller, execution, initialization = candidate(operations, monkeypatch, tmp_path)
    old = bridge_root(execution)
    source_bytes = Path(initialization["source"]).read_bytes()
    launched = []

    def launch(conn, eid, **kwargs):
        assert bridge_root(execution) != old
        assert kwargs["mode"] == "resume" and kwargs["focus"] == focus
        assert eid == execution["id"]
        from harness.execution_epoch_lock import epoch_action_lock

        with pytest.raises(ExecutionConflict, match="in progress"):
            with epoch_action_lock(execution):
                pytest.fail("a concurrent recovery must not enter the upgrade/resume gap")
        launched.append(kwargs)

    monkeypatch.setattr("harness.execution_recovery.launch_worker", launch)
    result = recover_and_continue(
        controller,
        execution["id"],
        action=action,
        expected_epoch_id=execution["epoch_id"],
        expected_authority_generation=execution["authority_generation"],
    )
    assert len(launched) == 1
    assert result["control_result"] == {
        "requested_action": action,
        "bridge_status": "Upgraded",
        "continuation_requested": True,
    }
    assert get_execution(controller.conn, execution["id"]) == execution
    assert Path(initialization["source"]).read_bytes() == source_bytes


def test_dashboard_recovery_rejects_stale_epoch_before_upgrade(operations, monkeypatch, tmp_path):
    from harness.execution_controls import recover_and_continue

    controller, execution, _ = candidate(operations, monkeypatch, tmp_path)
    old = bridge_root(execution)

    def unexpected(*args, **kwargs):
        pytest.fail("stale browser must not upgrade or resume")

    monkeypatch.setattr("harness.execution_bridge.upgrade_bridge", unexpected)
    monkeypatch.setattr("harness.execution_recovery.resume_execution", unexpected)
    with pytest.raises(ExecutionConflict):
        recover_and_continue(
            controller,
            execution["id"],
            action="recover",
            expected_epoch_id="stale",
            expected_authority_generation=0,
        )
    assert bridge_root(execution) == old


@pytest.mark.parametrize("pending", ["ACTIVE", "UNRESOLVED", "RUNNING"])
@pytest.mark.parametrize("already_current", [True, False])
def test_pending_work_only_blocks_actual_bridge_replacement(
    operations, monkeypatch, tmp_path, pending, already_current
):
    controller, execution, initialization = candidate(operations, monkeypatch, tmp_path)
    reason = "Verify same bridge without settling or approving unfinished product work"
    if already_current:
        assert upgrade_bridge(controller, execution["id"], reason=reason)["status"] == "Upgraded"
    directory = Path(execution["handoff_path"]).parent
    pointer = directory / "bridge-selection.json"
    prior = pointer.read_bytes() if pointer.exists() else None
    source = Path(initialization["source"])
    # Inject an interrupted lifecycle in this isolated fixture, preserving real
    # protected origin and selected bridge validation. No live task is modified.
    state = json.loads(source.read_bytes())
    if pending == "RUNNING":
        state["evidence"]["seq"] = 1
        state["evidence"]["entries"].append(
            {
                "id": "E1",
                "status": "RUNNING",
                "run": state["run_id"],
                "reservation": "synthetic-interrupted-reservation",
                "area": "fixture",
                "argv": ["python", "-m", "pytest"],
                "scope": ["app"],
            }
        )
    else:
        state["writers"]["pending-writer"] = {"id": "pending-writer", "state": pending}
    source.write_text(canonical(state))
    before = source.read_bytes()
    events = controller.workspace(execution["task_id"]).events()
    if already_current:
        result = upgrade_bridge(controller, execution["id"], reason=reason)
        assert result["status"] == "Current"
        assert Path(result["bridge"]) == bridge_root(execution)
    else:
        with pytest.raises(ExecutionConflict, match="settled product writers and checks"):
            upgrade_bridge(controller, execution["id"], reason=reason)
    assert source.read_bytes() == before
    assert (pointer.read_bytes() if pointer.exists() else None) == prior
    assert controller.workspace(execution["task_id"]).events() == events
