"""Durable observation publications against real approval/execution and native registry."""

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from harness.db import connect
from harness.execution_handoff import read_handoff
from harness.execution_operations import OperationController
from harness.execution_principals import PrincipalRegistry
from harness.execution_store import get_execution
from harness.reliability import store
from tests.execution_helpers import approved_execution


@pytest.fixture
def observed(controller, sample_repo, monkeypatch, tmp_path, db_path):
    _, eid = approved_execution(controller, sample_repo, monkeypatch, host="codex", legacy=True)
    execution = get_execution(controller.conn, eid)
    native = tmp_path / "native"
    records = native / "sessions/2026/11/19"
    records.mkdir(parents=True)
    root = str(uuid.uuid4())
    (records / ("rollout-" + root + ".jsonl")).write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": root,
                    "session_id": root,
                    "cli_version": "0.165.0",
                    "source": "exec",
                },
            }
        )
        + "\n"
    )
    registry = PrincipalRegistry(tmp_path / "registry", native, execution["binding"], "safe-policy")
    registry.observe_root_start(root)
    principal = registry.resolve({"threadId": root, "sessionId": root})
    operation = OperationController.__new__(OperationController)
    operation.database, operation.execution_id = db_path, eid
    operation.epoch_id, operation.authority_generation = "legacy-0", 0
    operation.registry, operation.lock = registry, threading.RLock()
    operation.envelope = read_handoff(execution["handoff_path"])
    operation.initialization = {"source": str(tmp_path / "source"), "pin_root": str(tmp_path)}
    # Only the AL projection is substituted; public dispatch still checks actual sealed authority.
    operation.state = lambda record: {"run_id": record["al_run_id"]}
    return operation, principal, controller


def rows(observed, table="reliability_observations"):
    return [dict(r) for r in observed[2].conn.execute("SELECT * FROM " + table)]


def test_atomic_terminal_idempotence_and_conflict(observed):
    operation, principal, _ = observed
    oid = store.begin(operation, principal, "run_command")
    assert rows(observed)[0]["state"] == "pending"
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: store.finish(operation, oid, "DISPATCH_REJECTED"), range(2)))
    assert len(rows(observed, "reliability_incidents")) == 1
    with pytest.raises(ValueError, match="conflicting"):
        store.finish(operation, oid, "RETURNED")
    assert rows(observed)[0]["code"] == "DISPATCH_REJECTED"
    second = store.begin(operation, principal, "run_command")
    assert second != oid
    with closing(connect(operation.database)) as reopened:
        assert (
            reopened.execute(
                "SELECT state FROM reliability_observations WHERE observation_id=?", (second,)
            ).fetchone()[0]
            == "pending"
        )


def test_terminal_incident_failure_rolls_back_all_facts(observed):
    operation, principal, controller = observed
    oid = store.begin(operation, principal, "run_command")
    controller.conn.execute(
        "CREATE TRIGGER fail_incident BEFORE INSERT ON reliability_incidents "
        "BEGIN SELECT RAISE(ABORT,'injected'); END"
    )
    with pytest.raises(ValueError, match="Reliability recording unavailable"):
        store.finish(operation, oid, "DISPATCH_REJECTED")
    assert rows(observed)[0]["state"] == "pending"
    assert rows(observed, "operation_audit")[0]["outcome"] is None
    assert not rows(observed, "reliability_incidents")


def test_pending_failure_rolls_back_audit_and_prevents_dispatch(observed, monkeypatch):
    operation, principal, controller = observed
    controller.conn.execute(
        "CREATE TRIGGER fail_pending BEFORE INSERT ON reliability_observations "
        "BEGIN SELECT RAISE(ABORT,'injected'); END"
    )
    effects = []
    monkeypatch.setattr(operation, "_dispatch", lambda *a: effects.append(True))
    with pytest.raises(ValueError, match="Reliability recording unavailable"):
        operation.dispatch(principal, "run_command", {})
    assert not effects and not rows(observed) and not rows(observed, "operation_audit")


def test_concurrent_conflicting_terminal_publication_has_one_winner(observed):
    operation, principal, _ = observed
    oid = store.begin(operation, principal, "read_file")

    def finish(code):
        try:
            store.finish(operation, oid, code)
            return "committed"
        except ValueError as error:
            assert str(error) == "conflicting terminal observation"
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(finish, ["RETURNED", "DISPATCH_OS_ERROR"]))
    assert sorted(outcomes) == ["committed", "conflict"]
    terminal = rows(observed)[0]
    assert terminal["state"] in {"succeeded", "failed"}
    assert len(rows(observed, "reliability_incidents")) == (terminal["state"] == "failed")


def test_schema_refuses_inconsistent_terminal_null_code(observed):
    import sqlite3

    operation, principal, controller = observed
    oid = store.begin(operation, principal, "read_file")
    with pytest.raises(sqlite3.IntegrityError):
        controller.conn.execute(
            "UPDATE reliability_observations SET state='denied', "
            "finished_at=started_at WHERE observation_id=?",
            (oid,),
        )
    assert rows(observed)[0]["state"] == "pending"


def test_conflicting_safe_environment_outcome_refuses(observed):
    operation, principal, _ = observed
    oid = store.begin(operation, principal, "environment_operation")
    store.finish(operation, oid, "RETURNED", {"status": "SetupPending"})
    store.finish(operation, oid, "RETURNED", {"status": "SetupPending", "private": "ignored"})
    with pytest.raises(ValueError, match="conflicting"):
        store.finish(operation, oid, "RETURNED", {"status": "SetupUnavailable"})
    assert rows(observed)[0]["state"] == "succeeded"
