"""Disposable managed-launcher preparation shared by runtime pin tests."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from al.tests.conftest import copy_al_source


def invoke(argv, cwd, env):
    return subprocess.run(
        list(map(str, argv)), cwd=cwd, env=env, capture_output=True, text=True, timeout=60
    )


# One bootstrapped launcher per pytest process, copied per test (#36); never handed out itself.
_LAUNCHED = {}


def _environment(home):
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
    return env


def _bootstrap(base):
    """Copy the AL source, create the repository and bootstrap the protected launcher under base."""
    source = copy_al_source(base / "source/al")
    home = base / "home"
    home.mkdir()
    repo = base / "repo"
    repo.mkdir()
    env = _environment(home)
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


@pytest.fixture
def launched(tmp_path_factory):
    """A freshly bootstrapped managed launcher, its AL source and an empty repository.

    The bootstrap (source copy plus launcher installation, 7-12 s) runs once per process at one
    fixed path; every test then gets a private copy of that result at the same path, verified
    byte-identical (paths, bytes, permission bits, symlink targets) to what the bootstrap left."""
    from al.tests.tree_templates import tree_digest

    base = tmp_path_factory.getbasetemp() / "launched"
    template = _LAUNCHED.get("template")
    if base.exists():
        shutil.rmtree(base)
    if template is None:
        base.mkdir(parents=True)
        _bootstrap(base)
        template = tmp_path_factory.getbasetemp() / "launched-template"
        shutil.copytree(base, template, symlinks=True)
        _LAUNCHED.update(template=template, digest=tree_digest(base))
        if tree_digest(template) != _LAUNCHED["digest"]:
            raise AssertionError("launcher template differs from the bootstrap it recorded")
    else:
        shutil.copytree(template, base, symlinks=True)
        if tree_digest(base) != _LAUNCHED["digest"]:
            raise AssertionError("launcher copy is not byte-identical to its template")
    source, home, repo = base / "source/al", base / "home", base / "repo"
    return source, home, repo, home / "bin/alctl", _environment(home)


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
