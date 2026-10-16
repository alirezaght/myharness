#!/usr/bin/env python3
"""Shared fixtures for the Claude Code AL test suite.

Every test gets its own throw-away git repository and runtime directory:
`al_store.REPO_ROOT` / `al_store.RUNTIME_DIR` are module globals read at call
time, so patching them on the imported module isolates every command."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import al_state  # noqa: E402
import al_store  # noqa: E402

SESSION = "al-test"


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture
def repo(tmp_path):
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
    runtime = tmp_path / "runtime"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(al_store, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(al_store, "CURRENT", runtime / "current")
    monkeypatch.setattr(al_store, "REPO_ROOT", repo)
    monkeypatch.setenv("AL_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    yield runtime


@pytest.fixture
def state_file(isolated_runtime):
    return lambda session=None: isolated_runtime / f"{session or SESSION}.json"


@pytest.fixture
def cli(monkeypatch, capsys):
    """Run `al_state.main()` in-process. Returns (exit, stdout, stderr)."""
    def _run(*args):
        session = SESSION
        if len(args) >= 2 and args[0] == "--session":
            session, args = args[1], args[2:]
        monkeypatch.setattr(sys, "argv", ["al_state.py", "--session", session, *args])
        try:
            al_state.main()
            result = 0
        except SystemExit as exc:
            result = exc.code
        captured = capsys.readouterr()
        return result, captured.out, captured.err
    return _run


@pytest.fixture
def cli_process(repo):
    """Start `al_state.py` as a real child process (for runs that must overlap)."""
    def _start(*args):
        session = SESSION
        if len(args) >= 2 and args[0] == "--session":
            session, args = args[1], args[2:]
        return subprocess.Popen([sys.executable, str(SCRIPTS / "al_state.py"), "--session", session, *args],
                                cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return _start
