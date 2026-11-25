#!/usr/bin/env python3
"""Shared fixtures for the Claude Code AL test suite.

Runtime and native-session state stay isolated in a temporary project for every
test, with the same fixed test session used by Claude's CLI integration cases.
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
import run_templates  # noqa: E402

# Memo tables are found while they are still empty, before any test runs (#36).
run_templates.register_memo_tables()

SESSION = "al-test"

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


@pytest.fixture(scope="session")
def seed_digest(seed_repo):
    from run_templates import tree_digest
    return tree_digest(seed_repo)


@pytest.fixture
def repo(tmp_path, seed_repo, request, tmp_path_factory):
    """Give each test an independent worktree without repeating Git setup.

    A test that starts from a prebuilt run (`run_state`) gets its worktree at this process's
    one run-state path, which is deleted and recreated for every test (#36)."""
    import shutil
    if "run_state" in request.fixturenames:
        from run_templates import fixed_repository
        root = fixed_repository(tmp_path_factory.getbasetemp())
        if root.exists():
            shutil.rmtree(root)
        root.parent.mkdir(parents=True, exist_ok=True)
    else:
        root = tmp_path / "repo"
    shutil.copytree(seed_repo, root, symlinks=True)
    return root


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch, repo):
    # Protected run bindings require state in the repository's own runtime layout.
    runtime = repo / ".claude" / "al-runtime"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(al_store, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(al_store, "CURRENT", runtime / "current")
    monkeypatch.setattr(al_store, "REPO_ROOT", repo)
    monkeypatch.setenv("AL_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    # The shared per-repository fact store (#25) lives under each test's own directory.
    monkeypatch.setenv("AL_ANALYSIS_INDEX_DIR", str(tmp_path / "analysis-index"))
    import al_analysis_index
    al_analysis_index._STORES.clear()
    yield runtime
    al_analysis_index._STORES.clear()


@pytest.fixture(scope="session")
def launcher(tmp_path_factory):
    """Managed-launcher admission for in-process commands (one pin per test session)."""
    from launcher_context import Launcher, pinned_storage
    root = tmp_path_factory.mktemp("al-launcher")
    root.chmod(0o700)
    return Launcher(*pinned_storage(root, "claude"), "claude")


@pytest.fixture
def state_file(isolated_runtime):
    return lambda session=None: isolated_runtime / f"{session or SESSION}.json"


@pytest.fixture
def cli_process(repo, launcher):
    """Start one admitted al_state command as a real child process (for runs that must overlap)."""
    def _start(*args):
        session = SESSION
        if len(args) >= 2 and args[0] == "--session":
            session, args = args[1], args[2:]
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
    observer = StateObserver(SESSION)
    # Like a native conversation, implicit commands follow the latest run it started.
    latest = [SESSION]
    def _run(*args):
        session = latest[0]
        if len(args) >= 2 and args[0] == "--session":
            session, args = args[1], args[2:]
        monkeypatch.setattr(sys, "argv", ["al_state.py", "--session", session, *args])
        try:
            if args[:1] == ('inspect',) or args[:2] == ('integration', 'capabilities'):
                al_state._main()
            else:
                launcher.run(Path.cwd(), session, args[:1] == ('start',), al_state._main)
            result = 0
        except SystemExit as exc:
            result = exc.code
        except ValueError as exc:
            result = "al_run: " + str(exc)
        captured = capsys.readouterr()
        command = args[2:] if args[:1] == ('--session',) else args
        if result == 0 and args[:1] == ('start',):
            latest[0] = session
        if result == 0 and command[:1] == ('start',):
            observer.bind_started(captured.out)
        return result, captured.out, captured.err
    _run.state = observer.read
    _run.fixed_session = observer.default_session
    _run.observer = observer
    from launcher_context import current_protocol
    yield current_protocol(_run)
    launcher.leave()


@pytest.fixture
def run_state(cli, repo, tmp_path, launcher, seed_digest, tmp_path_factory):
    """Start the test from a prebuilt admitted run: `run_state("implemented")` (see run_templates)."""
    from run_templates import RunState, template_store
    return RunState(cli=cli, repo=repo, tmp_path=tmp_path, launcher=launcher, seed_digest=seed_digest,
                    store=template_store(tmp_path_factory.getbasetemp()))
