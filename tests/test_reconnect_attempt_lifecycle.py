"""Replay-only workers settle their own attempts without settling native uncertainty."""

import json
import uuid
from contextlib import contextmanager

import pytest

from harness import execution_worker as worker
from harness.execution_models import ExecutionConflict
from harness.execution_store import get_execution
from tests.execution_helpers import approved_execution, started


@pytest.fixture
def reconnect(controller, sample_repo, monkeypatch):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, eid)
    controller.conn.execute(
        "UPDATE executions SET status='WorkerOwned',native_id=? WHERE id=?",
        (str(uuid.uuid4()), eid),
    )
    add_attempt(controller.conn, eid, "current", "test-owner")
    monkeypatch.setattr(worker, "replay_source", lambda *args: True)

    def forbidden(*args, **kwargs):
        raise AssertionError("reconnect must not initialize or launch native execution")

    monkeypatch.setattr("harness.execution_initialization.initialize_execution", forbidden)
    monkeypatch.setattr("harness.native_runtime.NativeRuntime", forbidden)
    return controller, eid


def add_attempt(conn, eid, name, token, *, status="Reserved", mode="reconnect", child=None):
    conn.execute(
        "INSERT INTO execution_attempts "
        "(id,execution_id,token,mode,status,child_pid,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (name, eid, token, mode, status, child, "before", "before"),
    )


def attempts(conn):
    return {row["id"]: dict(row) for row in conn.execute("SELECT * FROM execution_attempts")}


def test_replay_settles_only_its_matching_attempt(reconnect):
    controller, eid = reconnect
    add_attempt(controller.conn, eid, "historical", "old-owner")
    before = attempts(controller.conn)["historical"]
    worker.supervise(controller.conn, eid, "test-owner", mode="reconnect")
    rows = attempts(controller.conn)
    assert rows["current"]["status"] == "Exited" and rows["current"]["updated_at"] != "before"
    assert rows["historical"] == before
    assert get_execution(controller.conn, eid)["status"] == "RecoveryRequired"


@pytest.mark.parametrize("seam", ["authority", "replay", "delivery"])
def test_handled_failure_settles_replay_and_preserves_diagnostic(reconnect, monkeypatch, seam):
    controller, eid = reconnect

    def denied(*args, **kwargs):
        raise PermissionError("specific " + seam + " failure")

    target = {
        "authority": "harness.execution_worker.require_execution_authority",
        "replay": "harness.execution_worker.replay_source",
        "delivery": "harness.execution_decisions.deliver_pending",
    }[seam]
    monkeypatch.setattr(target, denied)
    worker.supervise(controller.conn, eid, "test-owner", mode="reconnect")
    saved = get_execution(controller.conn, eid)
    assert saved["status"] == "Unavailable" and saved["error"] == "specific " + seam + " failure"
    assert attempts(controller.conn)["current"]["status"] == "Exited"


@pytest.mark.parametrize("delivery_failure", [False, True])
def test_replay_terminal_result_is_preserved(reconnect, monkeypatch, delivery_failure):
    controller, eid = reconnect

    def finish(*args):
        controller.conn.execute(
            "UPDATE executions SET status='Completed',result=?,error=NULL WHERE id=?",
            (json.dumps({"guarded": "fixture-result"}), eid),
        )
        return True

    monkeypatch.setattr(worker, "replay_source", finish)
    if delivery_failure:

        def denied(*args):
            raise PermissionError("delivery failure after terminal replay")

        monkeypatch.setattr("harness.execution_decisions.deliver_pending", denied)
    worker.supervise(controller.conn, eid, "test-owner", mode="reconnect")
    saved = get_execution(controller.conn, eid)
    assert saved["status"] == "Completed" and saved["result"] == {"guarded": "fixture-result"}
    assert saved["error"] is None and attempts(controller.conn)["current"]["status"] == "Exited"


@pytest.mark.parametrize("path", ["superseded", "terminal", "lock-denied"])
def test_unowned_or_unprocessed_paths_do_not_settle(reconnect, monkeypatch, path):
    controller, eid = reconnect
    before = attempts(controller.conn)
    if path == "superseded":
        controller.conn.execute("UPDATE executions SET worker_token='new-owner' WHERE id=?", (eid,))
        with pytest.raises(ExecutionConflict, match="superseded"):
            worker.supervise(controller.conn, eid, "test-owner", mode="reconnect")
    elif path == "terminal":
        controller.conn.execute("UPDATE executions SET status='Completed' WHERE id=?", (eid,))
        worker.supervise(controller.conn, eid, "test-owner", mode="reconnect")
    else:

        @contextmanager
        def denied(*args, **kwargs):
            raise PermissionError("worker lock inaccessible")
            yield

        monkeypatch.setattr(worker, "worker_lock", denied)
        with pytest.raises(PermissionError):
            worker.supervise(controller.conn, eid, "test-owner", mode="reconnect")
    assert attempts(controller.conn) == before


@pytest.mark.parametrize(
    "state,mode,child",
    [
        ("Reconciled", "reconnect", None),
        ("Reserved", "resume", None),
        ("Running", "reconnect", 123456),
    ],
)
def test_ineligible_attempt_is_not_overwritten(reconnect, state, mode, child):
    controller, eid = reconnect
    controller.conn.execute(
        "UPDATE execution_attempts SET status=?,mode=?,child_pid=? WHERE id='current'",
        (state, mode, child),
    )
    before = attempts(controller.conn)
    worker.supervise(controller.conn, eid, "test-owner", mode="reconnect")
    assert attempts(controller.conn) == before


def test_lost_token_during_processing_does_not_settle(reconnect, monkeypatch):
    controller, eid = reconnect
    before = attempts(controller.conn)

    def replace(*args):
        controller.conn.execute(
            "UPDATE executions SET worker_token='replacement' WHERE id=?", (eid,)
        )
        return True

    monkeypatch.setattr(worker, "replay_source", replace)
    worker.supervise(controller.conn, eid, "test-owner", mode="reconnect")
    assert attempts(controller.conn) == before


def test_settled_reconnect_passes_real_upgrade_attempt_guard(reconnect, monkeypatch, tmp_path):
    from harness.execution_bridge import upgrade_bridge
    from harness.execution_handoff import read_handoff
    from harness.execution_hosts import al_source

    controller, eid = reconnect
    worker.supervise(controller.conn, eid, "test-owner", mode="reconnect")
    saved = get_execution(controller.conn, eid)
    source = al_source(saved, read_handoff(saved["handoff_path"]))
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        json.dumps(
            {
                "run_id": saved["al_run_id"],
                "session_id": saved["al_session_id"],
                "writers": {},
                "evidence": {"entries": []},
            }
        )
    )
    # Only packaging/origin prerequisites are stand-ins; attempt and live-child guards run.
    monkeypatch.setattr("harness.execution_hosts.managed_launcher", lambda: "fixture-launcher")
    monkeypatch.setattr(
        "harness.execution_initialization.validate_existing_origin",
        lambda *args: {"al_run_id": saved["al_run_id"]},
    )
    monkeypatch.setattr("harness.execution_bridge.selected_bridge", lambda *args: tmp_path)
    monkeypatch.setattr("harness.execution_bridge.verify_snapshot", lambda *args: "a" * 64)
    monkeypatch.setattr(
        "harness.execution_bootstrap.authority_snapshot", lambda *args, **kwargs: tmp_path
    )
    assert (
        upgrade_bridge(
            controller, eid, reason="Verify completed replay no longer blocks bridge maintenance"
        )["status"]
        == "Current"
    )
    add_attempt(controller.conn, eid, "uncertain-native", "uncertain-owner", mode="resume")
    with pytest.raises(ExecutionConflict, match="settle recorded native attempts"):
        upgrade_bridge(
            controller, eid, reason="Uncertain native history must still prevent bridge replacement"
        )


def test_prompt_requires_fresh_observed_child_without_replacing_run(reconnect):
    controller, eid = reconnect
    from harness.execution_handoff import read_handoff

    saved = get_execution(controller.conn, eid)
    prompt = worker._prompt(
        saved, {"launcher": "/fixture/alctl"}, read_handoff(saved["handoff_path"]), "resume"
    )
    assert "Terminal child registrations cannot be reopened" in prompt
    assert "fresh actual native invocation" in prompt and "observed child ID" in prompt
    assert "valid task binding" in prompt and saved["al_session_id"] in prompt
    assert "Never invoke start or create a different AL run" in prompt
