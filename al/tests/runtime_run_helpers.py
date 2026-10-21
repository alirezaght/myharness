"""Disposable managed-launcher preparation shared by runtime pin tests."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from al.tests.conftest import copy_al_source


def invoke(argv, cwd, env):
    return subprocess.run(
        list(map(str, argv)), cwd=cwd, env=env, capture_output=True, text=True, timeout=60
    )


@pytest.fixture
def launched(tmp_path):
    source = copy_al_source(tmp_path / "source/al")
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(
        os.environ,
        HOME=str(home),
        PYTHONDONTWRITEBYTECODE="1",
        GIT_AUTHOR_NAME="fixture",
        GIT_AUTHOR_EMAIL="fixture@example.invalid",
        GIT_COMMITTER_NAME="fixture",
        GIT_COMMITTER_EMAIL="fixture@example.invalid",
    )
    for name in ("AL_RUNTIME_DIR", "AL_DISTRIBUTION_STATE", "PYTHONPATH", "PYTHONHOME"):
        env.pop(name, None)
    for args in [
        ("init", "-q"),
        (
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "initial",
        ),
    ]:
        result = invoke(["git", *args], repo, env)
        assert result.returncode == 0, result.stderr
    launcher = home / "bin/alctl"
    boot = invoke(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            source / "alctl.py",
            "bootstrap",
            "--source-root",
            source,
            "--destination",
            launcher,
            "--state-root",
            home / "installer",
        ],
        repo,
        env,
    )
    assert boot.returncode == 0, (boot.returncode, boot.stdout[-2000:], boot.stderr[-2000:])
    return source, home, repo, launcher, env


def public_run(launched, platform, *arguments, env=None):
    _, _, repo, launcher, default = launched
    return invoke(
        [
            launcher,
            "run",
            "--repository",
            repo,
            "--platform",
            platform,
            "--session",
            "pin-flow",
            "--",
            *arguments,
        ],
        repo,
        env or default,
    )


def run_record(launched):
    _, home, _, _, _ = launched
    paths = list((home / "installer/al-run-pins/runs").glob("*.json"))
    assert len(paths) == 1
    return paths[0], json.loads(paths[0].read_bytes())


def start(launched):
    result = public_run(launched, "codex", "start", "--spec", "Pin transaction controls.")
    assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])
    _, _, repo, _, _ = launched
    return repo / ".codex/al-runtime/pin-flow.json"
