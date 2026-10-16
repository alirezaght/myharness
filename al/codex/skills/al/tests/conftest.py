#!/usr/bin/env python3
"""Shared fixtures for the Codex AL test suite.

`al_store.RUNTIME_DIR` / `al_store.CURRENT` are computed once, at import time,
from `AL_RUNTIME_DIR`. Every other AL script imports specific names
(`load`, `save`, `session_id`, ...) out of `al_store` by reference, but those
functions still resolve `RUNTIME_DIR`/`CURRENT` as globals in `al_store`'s own
module namespace at CALL time — so patching the two attributes on the already
imported `al_store` module (rather than re-exporting the env var after other
modules have already bound their own copies) isolates every test's runtime
state in `tmp_path`, regardless of import order.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import al_state  # noqa: E402
import al_store  # noqa: E402


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture
def repo(tmp_path):
    """A throw-away repository, so `start` and the evidence fingerprints never
    read the real working tree."""
    root = tmp_path / "repo"
    for rel, text in {"svc/app.py": "x = 1\n", "svc/tests/fixtures/rows.json": "[]\n",
                      "web/app.js": "let a = 1\n", "docs/readme.md": "hi\n"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    return root


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch, repo):
    monkeypatch.chdir(repo)
    monkeypatch.setattr(al_store, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(al_store, "CURRENT", tmp_path / "current")
    monkeypatch.setattr(al_store, "REPO_ROOT", repo)
    monkeypatch.setenv("AL_RUNTIME_DIR", str(tmp_path))
    yield tmp_path


@pytest.fixture
def state_file(isolated_runtime):
    """Path of the active run's state file (`current`, or a fixed id before `start`)."""
    def _path(session=None):
        if session is not None:
            return isolated_runtime / f"{session}.json"
        current = isolated_runtime / "current"
        if not current.is_file():
            current.write_text("al-test\n")
        return isolated_runtime / f"{current.read_text().strip()}.json"
    return _path


@pytest.fixture
def cli_process(repo):
    """Start `al_state.py` as a real child process (for runs that must overlap)."""
    def _start(*args):
        if "--session" not in args and al_store.CURRENT.is_file():
            session = al_store.CURRENT.read_text().strip()
            args = ("--session", session, *args)
        return subprocess.Popen([sys.executable, str(SCRIPTS / "al_state.py"), *args],
                                cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return _start


@pytest.fixture
def cli(monkeypatch, capsys):
    """Invoke `al_state.main()` in-process. Returns (result, stdout, stderr).

    `result` is `0` on a normal return, the int passed to a guard's
    `sys.exit(<int>)`, or the string passed to an argument-error
    `sys.exit(<message>)` (Python does not stringify/print that for us inside
    a try/except — the caller compares against the message directly)."""
    def _run(*args):
        monkeypatch.setattr(sys, "argv", ["al_state.py", *args])
        try:
            al_state.main()
            result = 0
        except SystemExit as exc:
            result = exc.code
        captured = capsys.readouterr()
        return result, captured.out, captured.err
    return _run
