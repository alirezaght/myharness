"""Disposable repositories and command helpers for installer integration tests."""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from al.installer.source_materialization import copy2


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


_SOURCE_IGNORED = ("__pycache__", ".pytest_cache", "*.pyc")
# One verified copy of al/ per pytest process and source state; never handed to a test directly.
_SOURCE_TEMPLATE = {}


def _source_state(root):
    """Cheap identity of the al/ tree (names, sizes, modes, inodes, mtimes); any edit changes it."""
    rows = []
    for directory, dirs, names in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in _SOURCE_IGNORED)
        for name in sorted(names):
            if not name.endswith(".pyc"):
                info = os.lstat(os.path.join(directory, name))
                rows.append((directory, name, info.st_size, info.st_mode, info.st_ino, info.st_mtime_ns))
    return tuple(rows)


def copy_al_source(destination):
    """Give the caller its own private copy of al/.

    The reviewed materialization (`copy2`: no aliases, verified bytes, copy2-equivalent
    metadata) is slow, so it runs once per process and source state into a private
    template. Each call then clones that template with shutil.copy2, which reproduces
    the same bytes, modes, times and flags in a fresh tree owned by the caller.
    """
    root = Path(__file__).resolve().parents[1]
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    state = _source_state(root)
    template = _SOURCE_TEMPLATE.get(state)
    if template is None or not template.is_dir():
        base = Path(tempfile.mkdtemp(prefix="al-source-template-")).resolve()
        atexit.register(shutil.rmtree, base, True)
        template = base / "al"
        shutil.copytree(root, template, ignore=shutil.ignore_patterns(*_SOURCE_IGNORED), copy_function=copy2)
        if _source_state(root) != state:
            raise RuntimeError("al/ source changed while its test template was copied")
        _SOURCE_TEMPLATE.clear()
        _SOURCE_TEMPLATE[state] = template
    shutil.copytree(template, destination, symlinks=True, copy_function=shutil.copy2)
    return Path(destination)
