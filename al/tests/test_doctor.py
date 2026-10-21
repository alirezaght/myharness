"""Read-only diagnostics preserve even an absent state tree byte for byte."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from al.tests.conftest import run_alctl


def _snapshot(root):
    root = Path(root)
    if not root.exists():
        return None
    return {
        path.relative_to(root).as_posix(): (
            "dir" if path.is_dir() else path.read_bytes(),
            path.stat().st_mode & 0o777,
        )
        for path in sorted(root.rglob("*"))
    }


def test_doctor_of_absent_state_is_read_only_and_reports_clean_registry(tmp_path):
    state = tmp_path / "absent-state"
    before = _snapshot(state)
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    environment.pop("AL_RUNTIME_DIR", None)
    environment.pop("AL_DISTRIBUTION_STATE", None)
    result = subprocess.run(
        [sys.executable, str(root / "al/alctl.py"), "doctor", "--state-root", str(state)],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["ok"] is True
    assert _snapshot(state) == before is None


def test_doctor_detects_pending_operation_without_changing_installed_state(installer_workspace):
    workspace = installer_workspace
    installed = run_alctl(
        "install", "--global", "--home", workspace["home"], "--state-root", workspace["state"],
        "--source", workspace["source"], "--version", "1.0.0", "--platform", "codex",
        cwd=workspace["root"],
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    record = next((workspace["state"] / "installer/records").glob("*.json"))
    data = json.loads(record.read_text())
    data["pending"] = "interrupted-operation"
    record.write_text(json.dumps(data))
    before = _snapshot(workspace["state"])

    doctor = run_alctl("doctor", "--state-root", workspace["state"], cwd=workspace["root"])
    assert doctor.returncode == 1
    assert doctor.data["ok"] is False
    assert any("unresolved installer operation" in error for error in doctor.data["errors"])
    assert _snapshot(workspace["state"]) == before
