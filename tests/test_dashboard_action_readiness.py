"""Filesystem readiness and recorded AL outcomes are separate facts."""

import errno
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.dashboard import action_readiness as readiness
from harness.dashboard.execution_view import execution_action
from harness.execution_projection import execution_view
from harness.execution_store import get_execution
from tests.execution_helpers import approved_execution
from tests.test_dashboard_decision_retry import decision_fixture  # noqa: F401


def test_live_worker_lock_is_available_for_refresh_and_bytes_unchanged(
    controller, sample_repo, monkeypatch
):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    saved = get_execution(controller.conn, eid)
    path = Path(saved["handoff_path"]).parent / "worker.lock"
    path.write_bytes(b"live-owner-sentinel")
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = readiness.probe_readiness(saved)
        assert result["actions"]["reconnect"] is True
        assert result["checks"][0]["state"] == "owned"
        assert result["actions"]["resume"] is False
    assert path.read_bytes() == b"live-owner-sentinel"


def test_successor_probes_original_worker_lock(controller, sample_repo, monkeypatch, tmp_path):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    saved = get_execution(controller.conn, eid)
    original = Path(saved["handoff_path"]).parent
    selected = tmp_path / "successor"
    selected.mkdir()
    handoff = selected / "handoff.json"
    handoff.write_bytes(Path(saved["handoff_path"]).read_bytes())
    successor = saved | {"handoff_path": str(handoff), "authority_directory": str(original)}
    with (original / "worker.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = readiness.probe_readiness(successor)
        assert result["checks"][0] == {"capability": "worker_lock", "state": "owned"}
        assert result["actions"]["resume"] is False
    assert not (selected / "worker.lock").exists()
    assert (selected / "inbox").is_dir()


def test_denied_preflight_creates_no_intent(controller, sample_repo, monkeypatch, db_path):
    tid, eid = approved_execution(controller, sample_repo, monkeypatch)

    def denied(path):
        raise PermissionError(errno.EPERM, "denied")

    monkeypatch.setattr(readiness, "_lock", denied)
    status, value = execution_action(
        db_path,
        json.dumps(
            {
                "task": tid,
                "execution": eid,
                "blocker": "environment",
                "revision": 1,
                "request_id": "new-request",
                "verdict": "alternative",
                "text": "I will fix harness myself.",
                "tasks": ["A"],
                "criteria": ["AC-01"],
                "amendment": False,
            }
        ).encode(),
        "decision",
    )
    assert status == 409 and "trusted terminal" in value["error"]
    assert controller.conn.execute("SELECT count(*) FROM execution_decisions").fetchone()[0] == 0


def test_resolved_decision_does_not_verify_environment_or_exit_success(
    controller,
    decision_fixture,  # noqa: F811
    monkeypatch,
):
    _, eid, row = decision_fixture
    controller.conn.execute(
        "UPDATE execution_decisions SET state='Resolved' WHERE id=?", (row["id"],)
    )
    controller.conn.execute(
        "UPDATE executions SET status='Interrupted',error=? WHERE id=?",
        ("native host exited 0; no guarded AL implementation result", eid),
    )

    def denied(path):
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(readiness, "_lock", denied)
    readiness.probe_readiness(get_execution(controller.conn, eid))
    view = execution_view(controller.conn, eid)
    assert view["status"] == "Interrupted" and "without a verified" in view["status_explanation"]
    assert view["decisions"][0]["delivery_status"] == "acknowledged"
    assert "does not verify" in view["decisions"][0]["outcome_notice"]
    assert view["readiness"]["native_discovery"] == "unverified"
    assert view["readiness"]["attribution"]["status"] == "unverified"
    assert (
        view["readiness"]["attribution"]["inspected_contract"]["metadata_only_compatibility"]
        == "unsupported"
    )
    assert not view["readiness"]["actions"]["reconnect"]


def test_real_restricted_process_reports_denied_probe(tmp_path):
    if sys.platform != "darwin":
        pytest.skip("macOS sandbox-exec permission probe")
    directory = tmp_path / "destination"
    directory.mkdir()
    profile = tmp_path / "probe.sb"
    profile.write_text(
        "(version 1) (allow default) (deny file-write* (subpath "
        + json.dumps(str(directory.resolve()))
        + "))"
    )
    script = (
        "from pathlib import Path; "
        "from harness.dashboard.action_readiness import _directory; _directory(Path("
        + repr(str(directory))
        + "))"
    )
    result = subprocess.run(
        ["/usr/bin/sandbox-exec", "-f", str(profile), sys.executable, "-c", script],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0 and b"PermissionError" in result.stderr, result.stderr
    assert list(directory.iterdir()) == []


def test_get_projection_uses_snapshot_without_probes(controller, sample_repo, monkeypatch):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)

    def forbidden(*args):
        raise AssertionError("GET must not probe or write")

    monkeypatch.setattr(readiness, "probe_readiness", forbidden)
    assert execution_view(controller.conn, eid)["readiness"]["checked_at"] is None


def test_exact_lock_permission_error_is_not_treated_as_absence(tmp_path, monkeypatch):
    lock = tmp_path / "worker.lock"
    original = os.open

    def denied(path, *args, **kwargs):
        if Path(path) == lock:
            raise PermissionError(errno.EPERM, "exact path denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied)
    with pytest.raises(PermissionError):
        readiness._lock(lock)
    assert not lock.exists()


def test_exact_destination_permission_error_is_not_replaced_by_parent_probe(tmp_path, monkeypatch):
    destination = tmp_path / "inbox"
    original = os.open

    def denied(path, *args, **kwargs):
        if Path(path) == destination:
            raise PermissionError(errno.EPERM, "exact destination denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied)
    with pytest.raises(PermissionError):
        readiness._directory(destination)
    assert not destination.exists()


def test_terminal_architect_registration_cannot_reopen(controller, sample_repo, monkeypatch):
    from harness.execution_projection import ingest_event
    from harness.execution_store import entities
    from tests.execution_helpers import event, started

    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, eid)
    agent = {
        "id": "architect-old",
        "name": "Architect",
        "role": "al-architect",
        "status": "Completed",
    }
    ingest_event(controller.conn, eid, event(controller.conn, eid, 2, "agent.registered", agent))
    with pytest.raises(ValueError, match="terminal entity"):
        ingest_event(
            controller.conn,
            eid,
            event(
                controller.conn,
                eid,
                3,
                "agent.updated",
                {"id": "architect-old", "status": "Running"},
            ),
        )
    assert entities(controller.conn, eid, "agent")[0]["status"] == "Completed"


def test_worker_ownership_reason_never_recommends_restart(controller, sample_repo, monkeypatch):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    saved = get_execution(controller.conn, eid) | {"native_id": "synthetic-session"}
    with (Path(saved["handoff_path"]).parent / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = readiness.probe_readiness(saved)
        assert result["actions"]["reconnect"]
        assert result["action_reasons"]["resume"]["code"] == "worker_owned"
        with pytest.raises(ValueError, match="already owns") as error:
            readiness.require_ready(saved, "resume")
        assert "trusted terminal" not in str(error.value)


@pytest.mark.parametrize("status", ["Running", "Launching", "WorkerOwned"])
def test_repeated_http_resume_rechecks_active_status_without_native_call(
    controller, sample_repo, monkeypatch, db_path, status
):
    import http.client

    import harness.dashboard.execution_view as actions
    from tests.test_dashboard import serve

    tid, eid = approved_execution(controller, sample_repo, monkeypatch)
    saved = get_execution(controller.conn, eid) | {
        "native_id": "synthetic-session",
        "status": "Interrupted",
    }
    assert readiness.probe_readiness(saved)["actions"]["resume"]
    controller.conn.execute(
        "UPDATE executions SET native_id=?,status=? WHERE id=?", (saved["native_id"], status, eid)
    )
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("active Resume must not invoke the native seam")

    monkeypatch.setattr(actions, "resume_execution", forbidden)
    monkeypatch.setattr("harness.execution_recovery.launch_worker", forbidden)
    with serve(db_path, tid) as server:
        connection = http.client.HTTPConnection("127.0.0.1", server.port)
        for _ in range(2):
            connection.request(
                "POST",
                "/api/execution/resume",
                json.dumps({"task": tid, "execution": eid}),
                {"Origin": server.url.rstrip("/"), "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            data = json.loads(response.read())
            assert response.status == 409 and "already active" in data["error"]
            assert "trusted terminal" not in data["error"]
        connection.close()
    assert calls == []


def test_missing_identity_and_other_unavailability_are_not_permission_failures(
    controller, sample_repo, monkeypatch
):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    saved = get_execution(controller.conn, eid)
    result = readiness.probe_readiness(saved)
    assert result["action_reasons"]["resume"]["code"] == "missing_native_identity"
    assert "trusted terminal" not in result["action_reasons"]["resume"]["message"]

    def unavailable(path):
        raise OSError(errno.ENOENT, "synthetic state missing")

    monkeypatch.setattr(readiness, "_lock", unavailable)
    with pytest.raises(ValueError, match="capability unavailable") as failure:
        readiness.require_ready(saved, "resume")
    assert "trusted terminal" not in str(failure.value)


@pytest.mark.parametrize(
    "error,restart",
    [
        (OSError(errno.ENOENT, "synthetic path missing"), False),
        (OSError(errno.EBUSY, "synthetic resource busy"), False),
        (PermissionError(errno.EPERM, "synthetic permission denied"), True),
    ],
)
def test_action_oserror_restart_guidance_is_permission_specific(
    controller, sample_repo, monkeypatch, db_path, error, restart
):
    import harness.dashboard.execution_view as actions

    tid, eid = approved_execution(controller, sample_repo, monkeypatch)
    controller.conn.execute(
        "UPDATE executions SET status='Interrupted',native_id='synthetic-session' WHERE id=?",
        (eid,),
    )

    def fail(*args):
        raise error

    monkeypatch.setattr(actions, "resume_execution", fail)
    status, result = execution_action(
        db_path, json.dumps({"task": tid, "execution": eid}).encode(), "resume"
    )
    assert status == 503 and ("trusted terminal" in result["error"]) is restart
    assert str(error) in result["error"]
