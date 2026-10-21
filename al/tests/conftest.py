"""Disposable repositories and command helpers for installer integration tests."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def installer_workspace(tmp_path, monkeypatch):
    """Create a tagged AL source and isolated homes/state for CLI tests."""
    from al.tests.test_manifest import make_source

    monkeypatch.delenv("AL_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("AL_DISTRIBUTION_STATE", raising=False)
    source = make_source(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    state = tmp_path / "state"
    return {"root": tmp_path, "source": source, "home": home, "state": state}


def run_alctl(*args, cwd, env=None, check=False):
    """Run the trusted checkout entry point with bytecode disabled."""
    root = Path(__file__).resolve().parents[2]
    process_env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    process_env.pop("AL_RUNTIME_DIR", None)
    process_env.pop("AL_DISTRIBUTION_STATE", None)
    if env:
        process_env.update({str(key): str(value) for key, value in env.items()})
    completed = subprocess.run(
        [sys.executable, str(root / "al/alctl.py"), *map(str, args)],
        cwd=cwd,
        env=process_env,
        text=True,
        capture_output=True,
        check=check,
    )
    try:
        completed.data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        completed.data = None
    return completed


def copy_al_source(destination):
    root = Path(__file__).resolve().parents[1]
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        root,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
    )
    return Path(destination)
