"""Actual isolated entrypoint refuses before mutable imports or manufactured authority."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/install_global.py"


def test_global_entrypoint_requires_isolation_and_never_imports_hostile_startup(tmp_path):
    marker = tmp_path / "executed"
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    (hostile / "sitecustomize.py").write_text(
        "from pathlib import Path;Path(" + repr(str(marker)) + ").write_text('executed')"
    )
    (hostile / "al.py").write_text("raise RuntimeError('untrusted module')")
    env = dict(os.environ, PYTHONPATH=str(hostile))
    refused = subprocess.run(
        [sys.executable, "-I", "-S", str(SCRIPT), "--help"],
        cwd=hostile,
        env=env,
        capture_output=True,
        text=True,
    )
    assert refused.returncode != 0 and "-I -S -B" in refused.stderr
    help_result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), "--help"],
        cwd=hostile,
        env=env,
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0, help_result.stderr[-1000:]
    assert "{plan,apply,verify}" in help_result.stdout
    assert not marker.exists()


def test_public_apply_rejects_unregistered_self_hash_without_target_writes(tmp_path):
    from al.installer.workspace_inputs import sealed
    from al.installer.workspace_plan import FIELDS

    home = tmp_path / "home"
    home.mkdir()
    state = tmp_path / "state"
    body = {key: None for key in FIELDS - {"digest"}}
    body.update(schema_version=1, kind="approved-workspace-global", state_root=str(state))
    fake = tmp_path / "not-admitted.json"
    fake.write_text(json.dumps(sealed(body)))
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(SCRIPT),
            "apply",
            "--state-root",
            str(state),
            "--file",
            str(fake),
            "--sha256",
            hashlib.sha256(fake.read_bytes()).hexdigest(),
        ],
        cwd=home,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, (result.stdout[-1000:], result.stderr[-1000:])
    assert "not the immutable registered" in json.loads(result.stdout)["error"]
    assert list(home.iterdir()) == [] and not state.exists()
