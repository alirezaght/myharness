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


@pytest.fixture(scope="session")
def seed_repo(tmp_path_factory):
    """Commit the standard repository once; tests copy its isolated Git data."""
    root = tmp_path_factory.mktemp("al-git-seed") / "repo"
    for rel, text in {"svc/app.py": "x = 1\n", "svc/tests/fixtures/rows.json": "[]\n",
                      "web/app.js": "let a = 1\n", "docs/readme.md": "hi\n"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    (root/"pyproject.toml").write_text('[project]\nname="al-fixture"\ndependencies=[]\n[tool.setuptools.packages.find]\nwhere=["svc"]\n[tool.al.architecture]\nforbidden=["forbidden_dependency"]\n')
    (root / ".gitignore").write_text("__pycache__/\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    return root


@pytest.fixture
def repo(tmp_path, seed_repo):
    """Give each test an independent worktree without repeating Git setup."""
    root = tmp_path / "repo"
    import shutil
    shutil.copytree(seed_repo, root, symlinks=True)
    return root


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch, repo):
    # Protected run bindings require state in the repository's own runtime layout.
    runtime = repo / ".codex" / "al-runtime"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(al_store, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(al_store, "CURRENT", runtime / "current")
    monkeypatch.setattr(al_store, "REPO_ROOT", repo)
    monkeypatch.setenv("AL_RUNTIME_DIR", str(runtime))
    yield runtime


@pytest.fixture(scope="session")
def launcher(tmp_path_factory):
    """Managed-launcher admission for in-process commands (one pin per test session)."""
    from launcher_context import Launcher, pinned_storage
    root = tmp_path_factory.mktemp("al-launcher")
    root.chmod(0o700)
    return Launcher(*pinned_storage(root, "codex"), "codex")


@pytest.fixture
def state_file(isolated_runtime):
    """Path of the active run's state file (`current`, or a fixed id before `start`)."""
    def _path(session=None):
        if session is not None:
            return isolated_runtime / f"{session}.json"
        current = isolated_runtime / "current"
        isolated_runtime.mkdir(parents=True, exist_ok=True)
        if not current.is_file():
            current.write_text("al-test\n")
        return isolated_runtime / f"{current.read_text().strip()}.json"
    return _path


@pytest.fixture
def cli_process(repo, launcher):
    """Start one admitted al_state command as a real child process (for runs that must overlap)."""
    def _start(*args):
        if args[:1] == ("--session",):
            session, args = args[1], args[2:]
        elif args[:1] == ("start",):
            session = al_store.new_session_id()
        else:
            session = al_store.CURRENT.read_text().strip()
        argv = launcher.child_argv(repo, session, args[:1] == ("start",), args)
        return subprocess.Popen(argv, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return _start


@pytest.fixture
def cli(monkeypatch, capsys, launcher, repo):
    """Invoke `al_state._main()` in-process. Returns (result, stdout, stderr).

    `result` is `0` on a normal return, the int passed to a guard's
    `sys.exit(<int>)`, or the string passed to an argument-error
    `sys.exit(<message>)` (Python does not stringify/print that for us inside
    a try/except — the caller compares against the message directly)."""
    from fixture_state import StateObserver
    observer = StateObserver(None)
    def _run(*args):
        command = args[2:] if args[:1] == ('--session',) else args
        start = command[:1] == ('start',)
        if args[:1] == ('--session',):
            session = args[1]
        elif start:
            session = al_store.new_session_id()
        elif al_store.CURRENT.is_file():
            session = al_store.CURRENT.read_text().strip()
        else:
            session = None
        argv = ["al_state.py", *args] if args[:1] == ('--session',) or session is None else ["al_state.py", "--session", session, *args]
        monkeypatch.setattr(sys, "argv", argv)
        try:
            if session is None or command[:1] == ('inspect',) or command[:2] == ('integration', 'capabilities'):
                al_state._main()
            else:
                launcher.run(Path.cwd(), session, start, al_state._main)
            result = 0
        except SystemExit as exc:
            result = exc.code
        except ValueError as exc:
            result = "al_run: " + str(exc)
        captured = capsys.readouterr()
        command = args[2:] if args[:1] == ('--session',) else args
        if result == 0 and command[:1] == ('start',):
            observer.bind_started(captured.out)
        return result, captured.out, captured.err
    _run.state = observer.read
    _run.fixed_session = observer.default_session
    from launcher_context import current_protocol
    yield current_protocol(_run)
    launcher.leave()
