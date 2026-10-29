# ruff: noqa: F811
"""Same-execution runtime recovery through the genuine protected managed issuer."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from al.tests.runtime_run_helpers import launched  # noqa: F401
from harness.execution_epochs import require_active_epoch
from harness.execution_models import ExecutionConflict
from harness.execution_runtime_recovery import activate_runtime, prepare_runtime
from harness.execution_store import get_execution
from tests.conftest import conn, controller, db_path, isolated_home  # noqa: F401
from tests.test_template_classification_integration import operations, registered  # noqa: F401

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS prepared-start containment")


def test_same_execution_activation_fences_old_and_requires_explicit_successor_identity(registered):  # noqa: F811
    _, _, controller, initialization, package, execution, envelope = registered[0]
    from harness.execution_store import bind_host

    native_id = registered[0][0].registry.root
    bind_host(controller.conn, execution["id"], native_id, "test-owner")
    execution = get_execution(controller.conn, execution["id"])
    original = Path(initialization["source"]).read_bytes()
    prepared = prepare_runtime(
        controller,
        execution["id"],
        expected_epoch_id="legacy-0",
        expected_authority_generation=0,
        request_id="runtime-fixture",
        reason="Repair the exact immutable runtime without changing the approved specification",
    )
    assert prepared["state"] == "PREPARED" and prepared["native_resumed"] is False
    active = activate_runtime(
        controller,
        execution["id"],
        request_id="runtime-fixture",
        preparation_sha256=prepared["preparation_sha256"],
        expected_epoch_id="legacy-0",
        expected_authority_generation=0,
    )
    assert active["state"] == "ACTIVE" and active["native_resumed"] is False
    current = get_execution(controller.conn, execution["id"])
    assert current["id"] == execution["id"] and current["native_id"] == execution["native_id"]
    assert (
        current["approval_id"] == execution["approval_id"]
        and current["binding"] == execution["binding"]
    )
    assert Path(initialization["source"]).read_bytes() == original
    from harness.execution_epochs import get_epoch

    assert (
        get_epoch(controller.conn, execution["id"], "legacy-0")["identity"]["al_run_id"]
        == execution["al_run_id"]
    )
    from harness.execution_handoff import read_handoff
    from harness.execution_worker import _prompt

    prompt = _prompt(current, package, read_handoff(current["handoff_path"]), "resume")
    for identity in (
        current["epoch_id"],
        current["al_session_id"],
        current["al_run_id"],
        current["handoff_path"],
        native_id,
    ):
        assert identity in prompt
    assert "historical verification debt" in prompt and "fresh actual" in prompt
    with pytest.raises(ExecutionConflict, match="stale_epoch"):
        require_active_epoch(controller.conn, execution["id"], mutation=True)
    with pytest.raises(ExecutionConflict, match="stale_epoch"):
        require_active_epoch(
            controller.conn,
            execution["id"],
            expected_epoch_id="legacy-0",
            expected_authority_generation=0,
            mutation=True,
        )
    require_active_epoch(
        controller.conn,
        execution["id"],
        expected_epoch_id=active["epoch_id"],
        expected_authority_generation=active["authority_generation"],
        mutation=True,
    )
    for sid, allowed in [(execution["al_session_id"], False), (current["al_session_id"], True)]:
        result = subprocess.run(
            [
                package["launcher"],
                "run",
                "--repository",
                envelope["repository"],
                "--platform",
                "codex",
                "--session",
                sid,
                "--existing-run",
                "--",
                "show",
                "--line",
            ],
            cwd=envelope["repository"],
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert (result.returncode == 0) is allowed, (result.stdout[-1500:], result.stderr[-1500:])
    assert (
        activate_runtime(
            controller,
            execution["id"],
            request_id="runtime-fixture",
            preparation_sha256=prepared["preparation_sha256"],
            expected_epoch_id="legacy-0",
            expected_authority_generation=0,
        )
        == active
    )


@pytest.mark.parametrize("with_amendment", [False, True])
def test_successor_public_guard_can_author_while_old_principal_is_fenced(
    registered, tmp_path, with_amendment
):  # noqa: F811
    import uuid
    from datetime import UTC, datetime

    from harness.execution_epochs import get_epoch
    from harness.execution_handoff import read_handoff
    from harness.execution_initialization import initialize_execution
    from harness.execution_operations import OperationController
    from harness.execution_principals import PrincipalRegistry
    from harness.execution_runtime_recovery import verify_selected_authority
    from tests.test_execution_continuity import command, report

    old_operation, old_principal, controller, _, package, old_execution, envelope = registered[0]
    amendment_id = None
    if with_amendment:
        from al.tests.test_runtime_successor_lineage import report as old_report
        from harness.execution_decisions import record_execution_decision
        from harness.execution_worker import replay_source

        old_report(
            registered[0],
            "task-register",
            {
                "id": "old-A",
                "title": "Original scope",
                "criteria": ["AC-01"],
                "files": ["app"],
                "dependencies": [],
            },
            tmp_path,
        )
        old_report(
            registered[0],
            "blocker",
            {
                "id": "old-spec-choice",
                "kind": "developer_engineering_decision",
                "tasks": ["old-A"],
                "question": "Choose scoped behavior",
                "context": "Synthetic explicit developer choice",
                "alternatives": ["first", "second"],
                "recommendation": "first",
            },
            tmp_path,
        )
        replay_source(controller.conn, old_execution["id"], "test-owner")
        decision = record_execution_decision(
            controller,
            old_execution["id"],
            "old-spec-choice",
            blocker_revision=1,
            request_id="accepted-scope",
            verdict="alternative",
            decision_text="Use the explicit alternate behavior for the original scoped criterion.",
            tasks=["old-A"],
            criteria=["AC-01"],
            amendment=True,
        )
        old_report(registered[0], "decision", json.loads(decision["delivery"]), tmp_path)
        replay_source(controller.conn, old_execution["id"], "test-owner")
        amendment_id = decision["id"]
    prepared = prepare_runtime(
        controller,
        old_execution["id"],
        expected_epoch_id="legacy-0",
        expected_authority_generation=0,
        request_id="public-runtime-fixture",
        reason="Repair immutable runtime for authorized scoped work",
    )
    activate_runtime(
        controller,
        old_execution["id"],
        request_id="public-runtime-fixture",
        preparation_sha256=prepared["preparation_sha256"],
        expected_epoch_id="legacy-0",
        expected_authority_generation=0,
    )
    current = get_execution(controller.conn, old_execution["id"])
    with pytest.raises(ExecutionConflict, match="stale_epoch"):
        old_operation.dispatch(old_principal, "read_file", {"path": "README.md"})
    selected = verify_selected_authority(current, get_epoch(controller.conn, current["id"]))
    handoff = read_handoff(current["handoff_path"])
    initial = initialize_execution(controller, current, package, handoff)
    registry = PrincipalRegistry(
        Path(current["handoff_path"]).parent / "fixture-principals",
        old_operation.registry.native_root,
        current["binding"],
        "successor-fixture-policy",
        epoch_fence=selected,
    )
    registry.observe_root_start(old_operation.registry.root)
    principal = dict(old_principal, policy=registry.policy)
    operation = OperationController(
        Path(envelope["database"]),
        current,
        handoff,
        package,
        initial,
        registry,
        Path(current["handoff_path"]).parent / "operations",
    )
    try:
        command(operation, principal, "spec-gate", "SPEC_READY")
        command(
            operation,
            principal,
            "classify",
            "STANDARD",
            "--designer",
            "no",
            "--implementers",
            "al-engineer",
        )
        report(
            operation,
            principal,
            "task-register",
            {
                "id": "fresh-A",
                "title": "Fresh successor work",
                "criteria": ["AC-01"],
                "files": ["work"],
                "dependencies": [],
            },
        )
        if with_amendment:
            mapped = json.loads(Path(initial["source"]).read_bytes())
            scope = mapped["integration"]["tasks"]["fresh-A"]["inherited_scopes"]
            assert [row["id"] for row in scope] == [amendment_id]
            assert scope[0]["predecessor_tasks"] == ["old-A"]
            assert (
                mapped["integration"]["deliveries"] == {}
                and mapped["proof_admission"]["status"] == "pending"
            )
        command(operation, principal, "phase", "IMPLEMENTATION")
        root = registry.native_root / "sessions/2026/10/29"
        root.mkdir(parents=True, exist_ok=True)
        aid = str(uuid.uuid4())
        for tid, source in [
            (registry.root, "exec"),
            (
                aid,
                {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": registry.root,
                            "agent_role": "al-engineer",
                        }
                    }
                },
            ),
        ]:
            payload = {
                "id": tid,
                "session_id": tid,
                "cli_version": "0.164.0",
                "source": source,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            (root / ("rollout-" + tid + ".jsonl")).write_text(
                json.dumps({"type": "session_meta", "payload": payload}) + "\n"
            )
        child = registry.enroll(aid)
        report(
            operation,
            principal,
            "agent-register",
            {
                "id": aid,
                "name": "Fresh observed fixture producer",
                "role": "al-engineer",
                "task": "fresh-A",
            },
        )
        report(operation, principal, "agent-update", {"id": aid, "status": "Running"})
        command(operation, principal, "integration", "task-start", "--task", "fresh-A")
        command(
            operation,
            principal,
            "writer",
            "start",
            "--id",
            "successor-writer",
            "--role",
            "al-engineer",
            "--files",
            "work",
            "--task",
            "fresh-A",
            "--agent",
            aid,
        )
        operation.dispatch(
            child,
            "apply_patch",
            {
                "task_id": "fresh-A",
                "expected_revision": operation.assignments[aid]["revision"],
                "patch": (
                    "*** Begin Patch\n*** Add File: work/successor.py\n"
                    "+answer = 42\n*** End Patch\n"
                ),
            },
        )
        assert (Path(envelope["repository"]) / "work/successor.py").read_text() == "answer = 42\n"
        command(
            operation,
            principal,
            "writer",
            "finish",
            "--id",
            "successor-writer",
            "--outcome",
            "success",
        )
        state = json.loads(Path(initial["source"]).read_bytes())
        assert state["proof_admission"]["status"] == "pending"
        assert state["implementation_readiness"]["obligations"]
        command(
            operation, principal, "approved", "--reason", "Missing proof cannot pass", success=False
        )
    finally:
        operation.close()


@pytest.mark.parametrize("edge", ["database-selected", "selector-published"])
def test_interrupted_activation_rolls_forward_without_restoring_predecessor(
    registered, monkeypatch, edge
):  # noqa: F811
    import harness.execution_runtime_activation as activation
    import harness.execution_runtime_recovery as recovery
    import harness.owned_subprocess as owned

    _, _, controller, initialization, _, execution, _ = registered[0]
    prepared = prepare_runtime(
        controller,
        execution["id"],
        expected_epoch_id="legacy-0",
        expected_authority_generation=0,
        request_id="interrupted-runtime",
        reason="Repair immutable runtime with durable roll-forward recovery",
    )
    original_source = Path(initialization["source"]).read_bytes()
    if edge == "database-selected":
        original = activation._select_database

        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("synthetic response lost after database CAS")

        monkeypatch.setattr(activation, "_select_database", interrupted)
    else:
        original = owned.run_owned

        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("synthetic response lost after selector publication")

        monkeypatch.setattr(owned, "run_owned", interrupted)
    with pytest.raises(OSError, match="synthetic response lost"):
        activate_runtime(
            controller,
            execution["id"],
            request_id="interrupted-runtime",
            preparation_sha256=prepared["preparation_sha256"],
            expected_epoch_id="legacy-0",
            expected_authority_generation=0,
        )
    if edge == "database-selected":
        monkeypatch.setattr(activation, "_select_database", original)
    else:
        monkeypatch.setattr(owned, "run_owned", original)
    # Preserve all enclosing disposable fixture authority/environment overrides.
    assert (
        Path(os.environ["HOME"]).resolve()
        == Path(registered[0][4]["launcher"]).resolve().parents[1]
    )
    from harness.execution_hosts import managed_launcher

    assert Path(managed_launcher()).resolve() == Path(registered[0][4]["launcher"]).resolve()
    # Do not substitute an older protected selector or restore the predecessor.
    assert get_execution(controller.conn, execution["id"])["epoch_id"] != "legacy-0"
    assert Path(initialization["source"]).read_bytes() == original_source
    result = recovery.recover_runtime_transition(
        controller, execution["id"], request_id="interrupted-runtime"
    )
    assert result["state"] == "ACTIVE" and result["native_resumed"] is False
    require_active_epoch(
        controller.conn,
        execution["id"],
        expected_epoch_id=result["epoch_id"],
        expected_authority_generation=result["authority_generation"],
        mutation=True,
    )


def test_public_runtime_cli_preserves_same_execution_and_never_resumes_native(
    registered, db_path, capsys
):
    from harness.cli import main

    _, _, controller, _, _, execution, _ = registered[0]
    common = ["--db", str(db_path), "execution"]
    identity = [
        execution["task_id"],
        execution["id"],
        "--request-id",
        "cli-runtime",
        "--expected-epoch",
        "legacy-0",
        "--expected-authority-generation",
        "0",
    ]
    assert (
        main(
            [
                *common,
                "runtime-prepare",
                *identity,
                "--reason",
                (
                    "Repair immutable runtime while preserving "
                    "the sealed approval and native conversation"
                ),
            ]
        )
        == 0
    )
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["state"] == "PREPARED" and prepared["native_resumed"] is False
    assert (
        main(
            [
                *common,
                "runtime-inspect",
                execution["task_id"],
                execution["id"],
                "--request-id",
                "cli-runtime",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["state"] == "PREPARED"
    assert (
        main(
            [
                *common,
                "runtime-activate",
                *identity,
                "--preparation-sha256",
                prepared["preparation_sha256"],
            ]
        )
        == 0
    )
    active = json.loads(capsys.readouterr().out)
    assert active["state"] == "ACTIVE" and active["native_resumed"] is False
    current = get_execution(controller.conn, execution["id"])
    assert current["native_id"] == execution["native_id"] and current["status"] == "Interrupted"
