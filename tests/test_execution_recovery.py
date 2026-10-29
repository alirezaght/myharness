import pytest

from harness.execution import start_execution
from harness.execution_models import ExecutionConflict
from harness.execution_recovery import reconcile_execution, resume_execution
from harness.execution_store import get_execution
from harness.execution_worker import worker_lock
from tests.execution_helpers import approved_execution


def test_unknown_spawn_is_not_retried(controller, sample_repo, monkeypatch):
    tid, eid = approved_execution(controller, sample_repo, monkeypatch)
    controller.conn.execute("UPDATE executions SET status='RecoveryRequired' WHERE id=?", (eid,))
    assert start_execution(controller, tid, launch=False)["execution"]["id"] == eid
    with pytest.raises(ExecutionConflict, match="unknown"):
        resume_execution(controller, eid)
    with pytest.raises(ExecutionConflict, match="unknown"):
        reconcile_execution(
            controller,
            eid,
            outcome="exited",
            evidence="Inspected logs only; native identity is still unknown",
        )


def test_worker_lock_and_terminal_immutability(controller, sample_repo, monkeypatch):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    execution = get_execution(controller.conn, eid)
    with worker_lock(execution), pytest.raises(ExecutionConflict, match="live worker"):
        with worker_lock(execution):
            pass
    controller.conn.execute(
        "UPDATE executions SET status='Completed',result='{}' WHERE id=?", (eid,)
    )
    assert resume_execution(controller, eid)["status"] == "Completed"
    assert controller.conn.execute("SELECT COUNT(*) FROM execution_attempts").fetchone()[0] == 0


def test_only_proven_pre_spawn_failure_returns_to_reserved(controller, sample_repo, monkeypatch):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    controller.conn.execute(
        "INSERT INTO execution_attempts "
        "(id,execution_id,token,mode,status,created_at,updated_at) "
        "VALUES ('a',?,'token','start','Launching','now','now')",
        (eid,),
    )
    with pytest.raises(ExecutionConflict, match="cannot be declared unstarted"):
        reconcile_execution(
            controller,
            eid,
            outcome="not-started",
            evidence="No PID was found in the process table after interruption",
        )


def _concurrent_reservation(db, task, out):
    from pathlib import Path

    from harness.controller import HarnessController
    from harness.db import connect
    from harness.execution import start_execution

    conn = connect(Path(db))
    try:
        result = start_execution(HarnessController(conn), task, launch=False)
        out.put((result["execution"]["id"], result["created"]))
    finally:
        conn.close()


def test_independent_processes_reserve_one_approval(controller, sample_repo, monkeypatch, db_path):
    import multiprocessing

    tid, eid = approved_execution(controller, sample_repo, monkeypatch)
    # Start from the actual approved task before its first execution reservation.
    controller.conn.execute("DELETE FROM execution_epochs WHERE execution_id=?", (eid,))
    controller.conn.execute("DELETE FROM executions WHERE id=?", (eid,))
    ctx = multiprocessing.get_context("fork")
    out = ctx.Queue()
    processes = [
        ctx.Process(target=_concurrent_reservation, args=(str(db_path), tid, out)) for _ in range(2)
    ]
    for process in processes:
        process.start()
    try:
        results = [out.get(timeout=15) for _ in processes]
    finally:
        for process in processes:
            process.join(timeout=15)
            if process.is_alive():
                process.terminate()
                process.join()
    assert all(process.exitcode == 0 for process in processes)
    assert len({row[0] for row in results}) == 1
    assert sorted(row[1] for row in results) == [False, True]
    assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1


def test_explicit_discovered_native_identity_is_reconciled_without_launch(
    controller, sample_repo, monkeypatch
):
    import uuid

    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    controller.conn.execute(
        "INSERT INTO execution_attempts "
        "(id,execution_id,token,mode,status,created_at,updated_at) "
        "VALUES ('uncertain',?,'lost-token','start','Launching','now','now')",
        (eid,),
    )
    controller.conn.execute("UPDATE executions SET status='RecoveryRequired' WHERE id=?", (eid,))
    native = str(uuid.uuid4())
    result = reconcile_execution(
        controller,
        eid,
        outcome="exited",
        native_id=native,
        evidence=(
            "Inspected the exact native conversation and confirmed its previous process has exited."
        ),
    )
    assert result["native_id"] == native and result["status"] == "Interrupted"
    assert controller.conn.execute("SELECT COUNT(*) FROM execution_attempts").fetchone()[0] == 1
    with pytest.raises(ExecutionConflict, match="retarget"):
        reconcile_execution(
            controller,
            eid,
            outcome="exited",
            native_id=str(uuid.uuid4()),
            evidence=(
                "A different conversation must never replace the already bound native session."
            ),
        )


def test_live_native_child_prevents_resume_even_after_reconnect_attempt(
    controller, sample_repo, monkeypatch
):
    import os
    import uuid

    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    controller.conn.execute(
        "UPDATE executions SET native_id=?,status='Interrupted' WHERE id=?",
        (str(uuid.uuid4()), eid),
    )
    controller.conn.execute(
        "INSERT INTO execution_attempts "
        "(id,execution_id,token,mode,status,child_pid,created_at,updated_at) "
        "VALUES ('child',?,'child-token','start','Running',?,'now','now')",
        (eid, os.getpid()),
    )
    controller.conn.execute(
        "INSERT INTO execution_attempts "
        "(id,execution_id,token,mode,status,created_at,updated_at) "
        "VALUES ('read-reconnect',?,'reconnect-token','reconnect','Reserved','now','now')",
        (eid,),
    )
    with pytest.raises(ExecutionConflict, match="live"):
        resume_execution(controller, eid)
    with pytest.raises(ExecutionConflict, match="alive"):
        reconcile_execution(
            controller,
            eid,
            outcome="exited",
            evidence="A stale worker cannot declare this still-live native process completed.",
        )


def test_replay_accepts_legacy_path_aliases_but_refuses_other_targets(
    controller, sample_repo, monkeypatch, tmp_path
):
    import json
    import subprocess
    from pathlib import Path

    from harness.execution_bootstrap import execution_environment
    from harness.execution_worker import replay_source

    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    execution = get_execution(controller.conn, eid)
    handoff = Path(execution["handoff_path"]).resolve()
    original_bytes = handoff.read_bytes()
    alias = tmp_path / "legacy-runtime-alias"
    alias.symlink_to(handoff.parent, target_is_directory=True)
    repository_alias = tmp_path / "repository-alias"
    repository_alias.symlink_to(sample_repo, target_is_directory=True)
    # Reproduce an existing reservation stored through an alias; do not rewrite its envelope.
    controller.conn.execute(
        "UPDATE executions SET handoff_path=?,worker_token='alias-owner' WHERE id=?",
        (str(alias / "handoff.json"), eid),
    )
    from tests.al_launcher import al_command, bootstrap_alctl

    alctl = bootstrap_alctl(tmp_path)
    runtime = Path(sample_repo).resolve() / ("." + execution["host"]) / "al-runtime"
    result = subprocess.run(
        al_command(
            alctl,
            execution["host"],
            sample_repo,
            execution["al_session_id"],
            "start",
            "--package-file",
            str(handoff.parent / "task-package.json"),
            "--handoff",
            str(handoff),
            session_directory=True,
        ),
        cwd=sample_repo,
        env=execution_environment(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    source = (
        runtime / "sessions" / execution["al_session_id"] / (execution["al_session_id"] + ".json")
    )
    state = json.loads(source.read_bytes())
    state["identity"]["root"] = str(repository_alias)
    source.write_text(json.dumps(state))
    assert replay_source(controller.conn, eid, "alias-owner")
    assert get_execution(controller.conn, eid)["cursor"] == 2
    assert handoff.read_bytes() == original_bytes

    foreign = tmp_path / "different-handoff.json"
    foreign.write_bytes(original_bytes)  # Same bytes are not the same handoff identity.
    state["integration"]["handoff"] = str(foreign)
    source.write_text(json.dumps(state))
    with pytest.raises(ExecutionConflict, match="handoff/repository"):
        replay_source(controller.conn, eid, "alias-owner")
    state["integration"]["handoff"] = str(handoff)
    other_root = tmp_path / "different-repository"
    other_root.mkdir()
    state["identity"]["root"] = str(other_root)
    source.write_text(json.dumps(state))
    with pytest.raises(ExecutionConflict, match="handoff/repository"):
        replay_source(controller.conn, eid, "alias-owner")
    assert get_execution(controller.conn, eid)["cursor"] == 2
