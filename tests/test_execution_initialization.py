"""Genuine protected initialization precedes contained AL state writes."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.execution_bootstrap import authority_snapshot, native_environment
from harness.execution_hosts import al_argv
from harness.execution_initialization import initialize_execution
from harness.execution_isolation import contained_argv, prepare_isolation
from harness.execution_store import get_execution
from harness.execution_worker import replay_source
from tests.execution_helpers import approved_execution, claimed

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="macOS whole-process compatibility sandbox"
)


def test_supervisor_start_and_contained_existing_state(controller, monkeypatch, launched):
    _, home, repo, launcher, env = launched
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    _, eid = approved_execution(controller, repo, monkeypatch, host="codex")
    claimed(controller.conn, eid)
    execution = get_execution(controller.conn, eid)
    from harness.execution_handoff import read_handoff

    envelope = read_handoff(execution["handoff_path"])
    identity = initialize_execution(controller, execution, {"launcher": str(launcher)}, envelope)
    before = Path(identity["binding_path"]).read_bytes()
    assert json.loads(before)["status"] == "ACTIVE"
    assert replay_source(controller.conn, eid, "test-owner")
    assert get_execution(controller.conn, eid)["al_run_id"] == identity["al_run_id"]
    assert (
        initialize_execution(controller, execution, {"launcher": str(launcher)}, envelope)
        == identity
    )
    transport = {"port": 54321, "url": "http://127.0.0.1:54321", "token": "fixture"}
    profile = prepare_isolation(execution, envelope, identity, transport)
    root = authority_snapshot(Path(execution["handoff_path"]).parent)
    native = native_environment(profile, transport, root)
    # Exact existing run can write state/artifacts inside containment.
    command = al_argv(
        str(launcher),
        "codex",
        repo,
        execution["al_session_id"],
        "checkpoint",
        "--reason",
        "Contained runtime checkpoint",
        "--resume-instructions",
        "Continue exact run",
        existing_run=True,
    )
    result = subprocess.run(
        contained_argv(profile, command),
        cwd=repo,
        env=native,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    # Task admission invokes the genuine fixed RO authority bridge inside containment.
    task = Path(execution["handoff_path"]).parent / "register-task.json"
    task.write_text(
        json.dumps(
            {
                "id": "A",
                "title": "Approved implementation",
                "files": ["docs"],
                "criteria": ["AC-01"],
                "dependencies": [],
            }
        )
    )
    for arguments in (
        ("spec-gate", "SPEC_READY"),
        ("classify", "STANDARD", "--designer", "no", "--implementers", "al-engineer"),
        ("integration", "task-register", "--file", str(task)),
        ("integration", "task-admit", "--task", "A"),
    ):
        command = al_argv(
            str(launcher), "codex", repo, execution["al_session_id"], *arguments, existing_run=True
        )
        result = subprocess.run(
            contained_argv(profile, command),
            cwd=repo,
            env=native,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr + result.stdout
    assert Path(identity["binding_path"]).read_bytes() == before
