# ruff: noqa: F811
"""Failed invocation settlement retains failure and uses real protected AL reconciliation."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.execution_models import ExecutionConflict
from harness.execution_settlement import settle_exited_work
from harness.execution_store import get_execution
from tests.test_execution_bridge import operations  # noqa: F401


def failed_writer(operations, monkeypatch):
    _, principal, controller, initial, package, execution, _ = operations
    monkeypatch.setattr(
        "harness.execution_settlement.managed_launcher", lambda: package["launcher"]
    )
    controller.conn.execute(
        "UPDATE executions SET native_id=?,status='Interrupted' WHERE id=?",
        (principal["thread_id"], execution["id"]),
    )
    path = Path(initial["source"])
    state = json.loads(path.read_bytes())
    state["integration"]["agents"]["failed-agent"] = {
        "id": "failed-agent",
        "name": "Failed fixture",
        "role": "al-engineer",
        "status": "Completed",
        "task": None,
        "activity": "Fixture failed",
        "progress": None,
        "started_at": 1,
        "ended_at": 2,
        "registration": "fixture",
    }
    state["writers"]["failed-writer"] = {
        "id": "failed-writer",
        "state": "UNRESOLVED",
        "outcome": "failed",
        "agent": "failed-agent",
        "role": "al-engineer",
        "files": ["app"],
        "commands": [],
        "phase": state["phase"],
        "started_at": 1,
        "ended_at": 2,
    }
    path.write_text(json.dumps(state))
    # Real, reaped native-process boundary; the fixture lifecycle is deliberately
    # incomplete. No code success or actual writer admission is fabricated.
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=5)
    controller.conn.execute(
        "INSERT INTO execution_attempts "
        "(id,execution_id,token,mode,status,child_pid,epoch_id,created_at,updated_at) "
        "VALUES ('settlement',?,'settlement-token','resume','Exited',?,?,'now','now')",
        (execution["id"], process.pid, execution["epoch_id"]),
    )
    return controller, get_execution(controller.conn, execution["id"]), path


def test_completed_failed_writer_is_reconciled_without_approving_work(operations, monkeypatch):
    controller, execution, source = failed_writer(operations, monkeypatch)
    before = get_execution(controller.conn, execution["id"])
    result = settle_exited_work(controller, execution["id"])
    assert result == {"status": "Settled", "reconciled": ["failed-writer"]}
    writer = json.loads(source.read_bytes())["writers"]["failed-writer"]
    assert writer["state"] == "RECONCILED" and writer["outcome"] == "failed"
    assert writer["reconciliation"]["artifact"]
    assert get_execution(controller.conn, execution["id"]) == before
    assert settle_exited_work(controller, execution["id"])["reconciled"] == []


def test_unknown_or_living_native_attempt_is_never_auto_settled(operations, monkeypatch):
    controller, execution, source = failed_writer(operations, monkeypatch)
    before = source.read_bytes()
    controller.conn.execute(
        "UPDATE execution_attempts SET child_pid=? WHERE id='settlement'", (os.getpid(),)
    )
    with pytest.raises(ExecutionConflict, match="live or uncertain"):
        settle_exited_work(controller, execution["id"])
    assert source.read_bytes() == before
    controller.conn.execute(
        "UPDATE execution_attempts SET child_pid=NULL,status='Reserved' WHERE id='settlement'"
    )
    assert settle_exited_work(controller, execution["id"])["reconciled"] == []
    assert source.read_bytes() == before
