"""Genuine managed AL atomic writes inside the exact session operation mount."""

import subprocess
import sys
from pathlib import Path

import pytest

from harness.command_safety import safe_environment
from harness.execution_hosts import al_argv
from harness.execution_initialization import initialize_execution
from harness.execution_isolation import operation_popen, prepare_operation_isolation
from harness.execution_store import get_execution
from tests.execution_helpers import approved_execution, claimed
from tests.native_model_fixture import operation_sandbox_unavailable

pytestmark = pytest.mark.skipif(
    operation_sandbox_unavailable(), reason="Linux operation sandbox capability unavailable"
)


def test_private_runtime_atomic_saves(controller, monkeypatch, launched, tmp_path):
    _, _, repo, launcher, environment = launched
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    _, eid = approved_execution(controller, repo, monkeypatch, host="codex")
    claimed(controller.conn, eid)
    execution = get_execution(controller.conn, eid)
    from harness.execution_handoff import read_handoff

    envelope = read_handoff(execution["handoff_path"])
    identity = initialize_execution(controller, execution, {"launcher": str(launcher)}, envelope)
    source = Path(identity["source"])
    assert source.parent.name == execution["al_session_id"]
    assert source.parent.parent.name == "sessions"
    sibling = source.parent.parent / "another"
    sibling.mkdir()
    sibling_state = sibling / "another.json"
    sibling_state.write_text("protected sibling")
    binding = Path(identity["binding_path"]).read_bytes()
    profile = prepare_operation_isolation(
        repo,
        writable=[],
        protected=[
            identity["binding_path"],
            identity["pin_root"],
            envelope["database"],
            execution["handoff_path"],
        ],
        hidden=[],
        directory=tmp_path / "operation",
        session_directory=source.parent,
        coordination_lock=identity["coordination_lock"],
    )
    env = safe_environment() | {"TMPDIR": profile.temporary}
    for index in range(2):
        previous = source.read_bytes()
        arguments = al_argv(
            str(launcher),
            "codex",
            repo,
            execution["al_session_id"],
            "checkpoint",
            "--reason",
            "Actual operation checkpoint " + str(index),
            "--resume-instructions",
            "Continue same native run",
            existing_run=True,
        )
        child = operation_popen(
            profile,
            arguments,
            cwd=repo,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = child.communicate(timeout=30)
        assert child.returncode == 0, (stdout, stderr)
        assert source.read_bytes() != previous
    evidence = al_argv(
        str(launcher),
        "codex",
        repo,
        execution["al_session_id"],
        "preflight",
        "--name",
        "anonymous-ipc",
        "--",
        sys.executable,
        "-c",
        "import asyncio; asyncio.run(asyncio.sleep(0)); print('IPC OK')",
        existing_run=True,
    )
    child = operation_popen(
        profile,
        evidence,
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stdout, stderr = child.communicate(timeout=45)
    assert child.returncode == 0, (stdout, stderr)
    import json

    state = json.loads(source.read_bytes())
    entry = state["evidence"]["entries"][-1]
    assert entry["status"] == "PASS", entry
    assert entry["process"]["ownership"] == "live-Popen-start_new_session", entry
    assert entry["pid"] > 0 and entry["exit"] == 0
    assert "IPC OK" in Path(entry["log"]).read_text()
    script = """import os,sys
from pathlib import Path
own,sibling=sys.argv[1:]
for action in [lambda:Path(sibling).write_text('escape'),lambda:os.unlink(sibling),
               lambda:os.rename(own,sibling),lambda:Path(str(Path(sibling).parent.parent/'new')).mkdir()]:
    try:action()
    except OSError:pass
    else:raise SystemExit('shared/sibling runtime escaped')
"""
    child = operation_popen(
        profile,
        [sys.executable, "-I", "-c", script, str(source), str(sibling_state)],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stdout, stderr = child.communicate(timeout=10)
    assert child.returncode == 0, (stdout, stderr)
    assert sibling_state.read_text() == "protected sibling"
    assert Path(identity["binding_path"]).read_bytes() == binding
