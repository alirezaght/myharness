# ruff: noqa: F811
"""Recovery must prove settlement and retain real issuer preparation across lost acknowledgments."""

import json
import sys
from pathlib import Path

import pytest

from harness.execution_models import ExecutionConflict
from harness.execution_recovery_settlement import observe_recovery_settlement, record_not_started
from harness.execution_store import get_execution
from tests.execution_helpers import approved_execution


def _attempt(
    controller, eid, *, pid=None, status="Exited", epoch="legacy-0", token="attempt-token"
):
    controller.conn.execute(
        "INSERT INTO execution_attempts "
        "(id,execution_id,token,mode,status,child_pid,created_at,updated_at,epoch_id) "
        "VALUES (?,?,?,'start',?,?,'now','now',?)",
        (token, eid, token, status, pid, epoch),
    )


def test_exited_or_missing_pid_does_not_prove_native_settlement(
    controller, sample_repo, monkeypatch
):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    execution = get_execution(controller.conn, eid)
    _attempt(controller, eid)
    observation = observe_recovery_settlement(controller, execution)
    assert observation["attempts"][0]["disposition"] == "unknown"
    assert observation["coverage"] == "incomplete"
    record_not_started(execution, "attempt-token", stage="native-setup")
    observation = observe_recovery_settlement(controller, execution)
    assert observation["attempts"][0]["disposition"] == "proven-not-started"
    assert observation["obligations"] == []


def test_settlement_uses_attempts_original_epoch_and_receipt_binding(
    controller, sample_repo, monkeypatch, tmp_path
):
    from harness.execution_contract import canonical
    from harness.execution_handoff import atomic_file

    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    execution = get_execution(controller.conn, eid)
    _attempt(controller, eid, pid=99999999)
    old = dict(execution)
    directory = tmp_path / "successor"
    directory.mkdir(mode=0o700)
    controller.conn.execute(
        "INSERT INTO execution_epochs "
        "(execution_id,epoch_id,authority_generation,state,identity,created_at) "
        "VALUES (?,'new-epoch',1,'ACTIVE',?,'now')",
        (eid, canonical({"handoff_path": str(directory / "handoff.json")})),
    )
    execution.update(
        epoch_id="new-epoch", authority_generation=1, handoff_path=str(directory / "handoff.json")
    )
    value = {
        "kind": "closed-native-context-v1",
        "execution_id": eid,
        "epoch_id": "legacy-0",
        "authority_generation": 0,
        "token": "attempt-token",
        "child_pid": 99999999,
        "native_returncode": 0,
        "owned_processes_closed": True,
        "tool_transport_closed": True,
    }
    path = Path(old["handoff_path"]).parent / "settlements/native-attempt-token.json"
    atomic_file(path, canonical(value).encode())
    assert (
        observe_recovery_settlement(controller, execution)["attempts"][0]["disposition"] == "closed"
    )
    value["epoch_id"] = "new-epoch"
    path.write_text(canonical(value))
    assert (
        observe_recovery_settlement(controller, execution)["attempts"][0]["disposition"]
        == "unknown"
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS protected prepared startup")
def test_retained_issuer_receipt_closes_lost_sqlite_preparation_ack(registered, monkeypatch):
    from harness.execution_initialization_channel import InitializationChannel
    from harness.execution_runtime_recovery import prepare_runtime, recover_runtime_transition

    _, _, controller, _, _, execution, _ = registered[0]
    original = InitializationChannel.serve

    def lose_ack(channel, process):
        def refuse(receipt):
            raise OSError("synthetic SQLite acknowledgment lost")

        channel.persist = refuse
        return original(channel, process)

    monkeypatch.setattr(InitializationChannel, "serve", lose_ack)
    with pytest.raises((OSError, ValueError), match="acknowledgment lost|preparation failed"):
        prepare_runtime(
            controller,
            execution["id"],
            expected_epoch_id="legacy-0",
            expected_authority_generation=0,
            request_id="lost-preparation-ack",
            reason="Recover a durable protected receipt without initializing another successor",
        )
    row = controller.conn.execute(
        "SELECT * FROM execution_runtime_transitions WHERE request_id='lost-preparation-ack'"
    ).fetchone()
    assert row["state"] == "PREPARING" and row["preparation"] is None
    recovered = recover_runtime_transition(
        controller, execution["id"], request_id="lost-preparation-ack"
    )
    assert recovered["state"] == "ACTIVE"
    row = controller.conn.execute(
        "SELECT * FROM execution_runtime_transitions WHERE request_id='lost-preparation-ack'"
    ).fetchone()
    receipt = json.loads(row["preparation"])
    assert (
        get_execution(controller.conn, execution["id"])["al_run_id"] == receipt["successor_run_id"]
    )


# The genuine prepared-start fixture is used only by the protected transition test.
from tests.test_template_classification_integration import operations, registered  # noqa: E402,F401


@pytest.mark.parametrize(
    "raw,condition", [(b"broken json", "corrupt"), (b"[]", "corrupt"), (b"{}", "binding-mismatch")]
)
def test_runtime_conditions_are_not_silently_missing(tmp_path, raw, condition):
    from harness.execution_planning_recovery import _condition

    source = tmp_path / ".codex/al-runtime/run1.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(raw)
    execution = {"host": "codex", "al_session_id": "run1", "al_run_id": "run123", "binding": {}}
    assert _condition(execution, {"repository": str(tmp_path)}) == condition
    source.unlink()
    assert _condition(execution, {"repository": str(tmp_path)}) == "missing"
    source.symlink_to(tmp_path / "absent")
    assert _condition(execution, {"repository": str(tmp_path)}) == "binding-mismatch"


def test_resolved_database_label_cannot_reconstruct_accepted_amendment(
    controller, sample_repo, monkeypatch
):
    from harness.execution_contract import canonical, digest
    from harness.execution_handoff import read_handoff
    from harness.execution_recovery_lineage import build_recovery_lineage
    from harness.execution_store import bind_al_run

    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    execution = get_execution(controller.conn, eid)
    bind_al_run(controller.conn, eid, execution["al_session_id"], "run123")
    execution = get_execution(controller.conn, eid)
    envelope = read_handoff(execution["handoff_path"])
    sha = digest(canonical(envelope["task_package"]).encode()).split(":")[1]
    previous = {
        "session_id": execution["al_session_id"],
        "run_id": "run123",
        "repository_identity": {"root": str(sample_repo)},
    }
    initial = {**previous, "original_task_package": {"sha256": sha}}
    protected = {
        "predecessor_transaction": previous,
        "predecessor_initialization": initial,
        "source_anchors": {"explicit_test_observation": True},
    }
    controller.conn.execute(
        "INSERT INTO execution_decisions "
        "(id,execution_id,blocker_id,blocker_revision,request_id,request,"
        "state,created_at,updated_at) "
        "VALUES ('decision-one',?,'blocker',1,'request-one','{}','Resolved','now','now')",
        (eid,),
    )
    lineage = build_recovery_lineage(controller, execution, envelope, protected)
    assert lineage["amendments"] == []
    assert lineage["pending_decisions"][0]["id"] == "decision-one"
    assert lineage["history_completeness"] == "incomplete" and lineage["obligations"]
    initial["original_task_package"]["sha256"] = "0" * 64
    with pytest.raises(ExecutionConflict, match="protected initialization"):
        build_recovery_lineage(controller, execution, envelope, protected)


@pytest.mark.skipif(sys.platform != "darwin", reason="genuine macOS protected prepared startup")
@pytest.mark.parametrize("missing", [False, True])
def test_missing_state_prepares_fresh_planning_without_inherited_authority(
    registered, monkeypatch, missing, tmp_path
):
    from harness.execution_planning_recovery import preview_planning, start_planning

    _, _, controller, initialization, _, execution, _ = registered[0]
    from harness.execution_store import bind_host

    bind_host(controller.conn, execution["id"], registered[0][0].registry.root, "test-owner")
    execution = get_execution(controller.conn, execution["id"])
    original_native = execution["native_id"]
    assert original_native
    assert not Path(initialization["source"]).is_relative_to(Path(registered[0][-1]["repository"]))
    if missing:
        Path(initialization["source"]).unlink()
    observed = preview_planning(
        controller, execution["id"], expected_epoch_id="legacy-0", expected_authority_generation=0
    )
    assert observed["preview"]["state_condition"] == ("missing" if missing else "intact")
    assert observed["preview"]["readiness"] == ("investigation-only" if missing else "ready")

    repeated = preview_planning(
        controller, execution["id"], expected_epoch_id="legacy-0", expected_authority_generation=0
    )
    assert repeated["preview_sha256"] == observed["preview_sha256"]
    with pytest.raises(ExecutionConflict, match="stale planning preview"):
        start_planning(
            controller,
            execution["id"],
            request_id="wrong-preview",
            preview_sha256="sha256:" + "0" * 64,
            expected_epoch_id="legacy-0",
            expected_authority_generation=0,
        )
    dispatched = []
    monkeypatch.setattr(
        "harness.execution_worker._spawn_worker",
        lambda argv, execution, attempt: dispatched.append(attempt),
    )

    def hold_native(controller, execution_id, *, request_id):
        current = get_execution(controller.conn, execution_id)
        assert current["status"] == "Reserved" and current["native_id"] is None
        # Restore only this helper while exercising the actual atomic worker reservation.
        from harness.execution_worker import launch_worker

        previous_dispatches = len(dispatched)
        launched = launch_worker(
            controller.conn,
            execution_id,
            mode="start",
            expected_epoch_id=current["epoch_id"],
            expected_authority_generation=current["authority_generation"],
            planning_request_id=request_id,
        )
        repeated = launch_worker(
            controller.conn,
            execution_id,
            mode="start",
            expected_epoch_id=current["epoch_id"],
            expected_authority_generation=current["authority_generation"],
            planning_request_id=request_id,
        )
        assert repeated["fresh_dispatch"]["attempt_id"] == launched["attempt_id"]
        assert len(dispatched) == previous_dispatches + 1
        return {"status": "WorkerOwned", "epoch_id": current["epoch_id"]}

    monkeypatch.setattr("harness.execution_planning_recovery.dispatch_fresh_planning", hold_native)
    result = start_planning(
        controller,
        execution["id"],
        request_id="missing-state-fixture",
        preview_sha256=observed["preview_sha256"],
        expected_epoch_id="legacy-0",
        expected_authority_generation=0,
    )
    current = get_execution(controller.conn, execution["id"])
    from harness.execution_handoff import read_handoff
    from harness.execution_initialization import initialization_identity
    from harness.execution_worker import package_for_native

    selected_handoff = read_handoff(current["handoff_path"])
    assert not (Path(current["handoff_path"]).parent / "bridge-selection.json").exists()
    initialized = initialization_identity(current, selected_handoff)
    installed = dict(registered[0][4], sha256="unrelated-installed-package-history")
    selected = package_for_native(current, selected_handoff, installed)
    expected_package = Path(initialized["pin_root"]) / "payload/codex/skills/al"
    assert selected["package"] == str(expected_package)
    assert selected["sha256"] != installed["sha256"]
    # This is the same rebinding used immediately after first-start initialization,
    # before the supervisor records package identity or dispatches a native child.
    first = package_for_native(
        dict(current, al_run_id=None), selected_handoff, installed, initialization=initialized
    )
    assert first == selected
    from harness.execution_runtime_recovery import _locks

    # ACTIVE retry must return the recorded owned dispatch even while its worker
    # lock is held; it cannot attempt activation or spawn a second native root.
    with _locks(current):
        retried = start_planning(
            controller,
            execution["id"],
            request_id="missing-state-fixture",
            preview_sha256=observed["preview_sha256"],
            expected_epoch_id="legacy-0",
            expected_authority_generation=0,
        )
    assert retried["state"] == "ACTIVE" and retried["fresh_dispatch"]["state"] == "launching"
    assert retried["fresh_dispatch"]["attempt_id"] == dispatched[0]
    assert result["epoch_id"] != "legacy-0" and current["approval_id"] == execution["approval_id"]
    from harness.execution_handoff import read_handoff
    from harness.execution_hosts import al_source

    state = json.loads(al_source(current, read_handoff(current["handoff_path"])).read_bytes())
    assert state["phase"] == "PLANNING" and state["proof_admission"]["status"] == "pending"
    assert state["writers"] == {} and state["evidence"]["entries"] == []
    assert all(not state["integration"][key] for key in ("agents", "tasks", "deliveries"))
    from harness.execution_epochs import get_epoch

    assert (
        get_epoch(controller.conn, execution["id"], "legacy-0")["identity"]["historical_execution"][
            "native_id"
        ]
        == original_native
    )

    if missing:
        import subprocess

        from al.tests.planning_reports import report_for
        from harness.execution_bootstrap import execution_environment

        def command(*arguments, allowed=True):
            result = subprocess.run(
                [
                    registered[0][4]["launcher"],
                    "run",
                    "--repository",
                    registered[0][-1]["repository"],
                    "--platform",
                    "codex",
                    "--session",
                    current["al_session_id"],
                    "--existing-run",
                    "--",
                    *arguments,
                ],
                cwd=registered[0][-1]["repository"],
                env=execution_environment(),
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert (result.returncode == 0) is allowed, (
                result.stdout[-2000:],
                result.stderr[-2000:],
            )
            return result

        command("spec-gate", "SPEC_READY")
        command(
            "classify", "SMALL", "--designer", "no", "--reason", "Current approved fixture scope."
        )
        denied = command(
            "writer",
            "start",
            "--id",
            "before-current-plan",
            "--role",
            "al-engineer",
            "--files",
            "work",
            allowed=False,
        )
        assert "fresh current Architect planning report" in denied.stderr
        packet = json.loads(command("planning", "packet").stdout)
        report = tmp_path / "fresh-current-scope.json"
        report.write_text(json.dumps(report_for(packet)))
        command("planning", "report", "--file", str(report))
        command("phase", "IMPLEMENTATION")
        refreshed = json.loads(
            al_source(current, read_handoff(current["handoff_path"])).read_bytes()
        )
        assert refreshed["proof_admission"]["status"] == "pending"
        assert refreshed["phase"] == "IMPLEMENTATION" and refreshed["writers"] == {}
        # No detached worker was spawned by this fixture. Model its known
        # pre-spawn failure before authorizing a second, distinct transition.
        record_not_started(current, current["worker_token"], stage="worker-spawn")
        controller.conn.execute(
            "UPDATE execution_attempts SET status='PreSpawnFailed' WHERE token=?",
            (current["worker_token"],),
        )
        controller.conn.execute(
            "UPDATE executions SET status='Interrupted' WHERE id=?", (current["id"],)
        )
        again = preview_planning(
            controller,
            current["id"],
            expected_epoch_id=current["epoch_id"],
            expected_authority_generation=current["authority_generation"],
        )
        assert again["preview"]["readiness"] == "ready"
        second = start_planning(
            controller,
            current["id"],
            request_id="second-planning-fixture",
            preview_sha256=again["preview_sha256"],
            expected_epoch_id=current["epoch_id"],
            expected_authority_generation=current["authority_generation"],
        )
        assert second["epoch_id"] != current["epoch_id"]
