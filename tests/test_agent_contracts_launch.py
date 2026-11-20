# ruff: noqa: F811
"""Actual SQLite/native launch linearization; null PID and a nonce never prove no-start."""

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from harness.db import transaction, utcnow
from harness.execution_contract import digest
from harness.execution_epoch_lock import epoch_action_lock
from harness.execution_recovery_settlement import record_not_started
from harness.execution_store import get_execution
from harness.policy.native_presence import not_started, permit
from harness.policy.resources import prepare
from harness.policy.source_dependencies import local_identity
from tests.test_agent_contracts_mutable import (  # noqa: F401
    observed,
    patch,
    policy_runtime,
    processes,
    strict_runtime,
)


def reserve_native(r):
    execution = get_execution(r.controller.conn, r.operation.execution_id)
    token = execution["worker_token"]
    r.controller.conn.execute(
        "INSERT INTO execution_attempts "
        "(id,execution_id,token,mode,status,created_at,updated_at,epoch_id) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "native-attempt",
            execution["id"],
            token,
            "start",
            "Reserved",
            utcnow(),
            utcnow(),
            execution["epoch_id"],
        ),
    )
    return execution, token


def launch_permission(r, execution, token):
    manifest = digest(local_identity())
    with epoch_action_lock(execution), transaction(r.controller.conn):
        grant = permit(r.controller.conn, execution, token, manifest)
        r.controller.conn.execute(
            "UPDATE execution_attempts SET status='Launching' WHERE token=?", (token,)
        )
    return grant


def test_pre_permit_no_start_is_fenced_and_cannot_later_launch(strict_runtime):
    r = strict_runtime
    execution, token = reserve_native(r)
    with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
        r.operation.dispatch(r.principal, "apply_patch", patch(r))
    record_not_started(execution, token, stage="native-setup", conn=r.controller.conn)
    with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
        launch_permission(r, execution, token)
    r.operation.dispatch(r.principal, "apply_patch", patch(r))
    assert (Path(r.operation.envelope["repository"]) / "svc/new.txt").exists()


def test_strict_reservation_wins_before_native_launch_and_no_start_releases_only_native(
    strict_runtime,
):
    r = strict_runtime
    attempt = prepare(r.operation, r.controller, r.principal, "apply_patch", patch(r), r.state)
    try:
        execution, token = reserve_native(r)
        with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
            launch_permission(r, execution, token)
        record_not_started(execution, token, stage="native-setup", conn=r.controller.conn)
        assert r.controller.conn.execute(
            "SELECT count(*) FROM policy_resource_claims WHERE attempt_id=? AND state='reserved'",
            (attempt.intent.id,),
        ).fetchone()[0]
        attempt.start()
        assert attempt.started
        attempt.owner_finished()
    finally:
        if not attempt.owner_done:
            attempt.release_unstarted()


def test_owned_issued_no_start_can_fence_its_own_future_popen(strict_runtime):
    r = strict_runtime
    execution, token = reserve_native(r)
    grant = launch_permission(r, execution, token)
    record_not_started(
        execution, token, stage="native-spawn", conn=r.controller.conn, launch_permit=grant
    )
    with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
        grant.begin_spawn()
    r.operation.dispatch(r.principal, "apply_patch", patch(r))


def test_pause_after_permit_other_thread_nonce_and_null_pid_cannot_discharge(strict_runtime):
    from harness.db import connect

    r = strict_runtime
    execution, token = reserve_native(r)
    ready, resume = threading.Event(), threading.Event()
    grant_box, errors = [], []

    def owner():
        conn = connect(r.operation.database)
        try:
            manifest = digest(local_identity())
            with epoch_action_lock(execution), transaction(conn):
                grant = permit(conn, execution, token, manifest)
                conn.execute(
                    "UPDATE execution_attempts SET status='Launching' WHERE token=?", (token,)
                )
            grant_box.append(grant)
            ready.set()
            assert resume.wait(10)
            grant.begin_spawn()
            assert not conn.in_transaction
            proc = subprocess.Popen([sys.executable, "-I", "-S", "-c", "pass"], close_fds=True)
            grant.spawned()
            assert proc.wait(timeout=10) == 0
            # This observed parent exit is deliberately NOT a qualified native-tree receipt.
        except BaseException as exc:
            errors.append(exc)
            ready.set()
        finally:
            conn.close()

    thread = threading.Thread(target=owner)
    thread.start()
    try:
        assert ready.wait(10) and not errors
        row = r.controller.conn.execute(
            "SELECT * FROM execution_attempts WHERE token=?", (token,)
        ).fetchone()
        assert row["child_pid"] is None and row["dependency_launch_state"] == "permitted"
        receipt = {
            "kind": "native-not-started-v1",
            "execution_id": execution["id"],
            "epoch_id": execution["epoch_id"],
            "authority_generation": execution["authority_generation"],
            "token": token,
            "stage": "native-spawn",
            "native_started": False,
        }
        for alleged in (None, json.loads(row["dependency_launch_receipt"])["permit"], grant_box[0]):
            with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
                not_started(r.controller.conn, execution, token, receipt, launch_permit=alleged)
        with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
            r.operation.dispatch(r.principal, "apply_patch", patch(r))
    finally:
        resume.set()
        thread.join(15)
    assert not thread.is_alive() and not errors
    with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
        r.operation.dispatch(r.principal, "apply_patch", patch(r))
    assert (
        r.controller.conn.execute(
            "SELECT dependency_launch_state FROM execution_attempts WHERE token=?", (token,)
        ).fetchone()[0]
        == "permitted"
    )


def test_outer_native_presence_has_no_same_execution_exemption(strict_runtime):
    r = strict_runtime
    execution, token = reserve_native(r)
    r.controller.conn.execute(
        "UPDATE execution_attempts SET status='Exited' WHERE token=?", (token,)
    )
    with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
        r.operation.dispatch(r.principal, "apply_patch", patch(r))
    assert (
        r.controller.conn.execute(
            "SELECT dependency_launch_state FROM execution_attempts"
        ).fetchone()[0]
        == "unknown"
    )
