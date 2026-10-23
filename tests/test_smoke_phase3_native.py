"""Native smoke diagnostics remain useful when cleanup is denied."""

import os
import subprocess
from pathlib import Path

import pytest

import scripts.smoke_phase3_native as smoke
from tests.sample_repo import make_sample_repo


def test_owned_process_cleanup_permission_denial_is_reportable(monkeypatch):
    def deny(pid, signal):
        raise PermissionError("operation denied")

    identity = {"pid": 43210, "pgid": 43210, "started": "fixture", "command": "host"}
    monkeypatch.setattr(smoke.os, "killpg", deny)
    monkeypatch.setattr(smoke, "_process_identity", lambda pid: identity)
    error = smoke._stop_process_group(43210, identity)
    assert error == "could not signal owned process group 43210: PermissionError: operation denied"


def test_reused_or_unidentified_process_is_never_signalled(monkeypatch):
    identity = {"pid": 43210, "pgid": 43210, "started": "old", "command": "host"}
    monkeypatch.setattr(smoke, "_process_identity", lambda pid: {**identity, "started": "new"})
    monkeypatch.setattr(
        smoke.os,
        "killpg",
        lambda *args: pytest.fail("unowned process was signalled"),
    )

    error = smoke._stop_process_group(43210, identity)

    assert error == (
        "refused to signal process 43210; owned process identity changed or is unavailable"
    )


def test_process_identity_is_rechecked_before_force_stop(monkeypatch):
    identity = {"pid": 43210, "pgid": 43210, "started": "old", "command": "host"}
    changed = {**identity, "started": "new"}
    identities = iter((identity, changed))
    signals = []
    monkeypatch.setattr(smoke, "_process_identity", lambda pid: next(identities))
    monkeypatch.setattr(smoke.time, "monotonic", iter((0, 4)).__next__)
    monkeypatch.setattr(smoke.os, "killpg", lambda pid, sig: signals.append(sig))

    error = smoke._stop_process_group(43210, identity)

    assert signals == [smoke.signal.SIGTERM]
    assert error == (
        "refused to force-stop process 43210; owned process identity changed or is unavailable"
    )


def test_worker_identity_is_recorded_only_for_its_process_group(monkeypatch):
    owned = {}
    identity = {"pid": 43210, "pgid": 43210, "started": "fixture", "command": "worker"}
    monkeypatch.setattr(smoke, "_process_identity", lambda pid: identity)

    smoke._remember_owned_process(owned, 43210)

    assert owned == {43210: identity}

    not_group_leader = {**identity, "pgid": 1}
    monkeypatch.setattr(smoke, "_process_identity", lambda pid: not_group_leader)
    smoke._remember_owned_process(owned, 43211)
    assert 43211 not in owned


def test_report_error_is_redacted():
    assert smoke._safe_error("Authorization: Bearer abc.def api_key=visible") == (
        "Authorization: [REDACTED] api_key=[REDACTED]"
    )


def test_worker_diagnostics_are_saved_redacted_before_temporary_tree_cleanup(tmp_path):
    log = tmp_path / "worker-001.log"
    log.write_text("Authorization: Bearer abc.def\napi_key=visible-token\nplain diagnostic\n")

    diagnostics = smoke._worker_diagnostics(tmp_path)

    assert diagnostics == [
        {
            "name": "worker-001.log",
            "tail": "Authorization: [REDACTED]\napi_key=[REDACTED]\nplain diagnostic\n",
        }
    ]


def test_runner_restores_exact_parent_environment_after_setup_error(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(smoke, "make_sample_repo", lambda path: project)
    monkeypatch.setattr(smoke, "_make_verifiable", lambda repo: None)
    monkeypatch.setattr(smoke, "_install_candidate", lambda repo, host, launcher: None)
    monkeypatch.setattr(smoke, "_candidate_launcher", lambda root: root / "alctl")

    def fail_connection(database):
        raise RuntimeError("fixture setup failure")

    monkeypatch.setattr(smoke, "connect", fail_connection)
    before = os.environ.copy()
    cwd = Path.cwd()

    with pytest.raises(RuntimeError, match="fixture setup failure"):
        smoke._run("claude", 10)

    assert os.environ == before
    assert Path.cwd() == cwd


def test_verifiable_project_commits_one_real_ci_check(tmp_path):
    repo = make_sample_repo(tmp_path / "project")

    smoke._make_verifiable(repo)

    assert (repo / ".github/workflows/ci.yml").read_text() == smoke.VERIFIABLE_CI
    assert 'requires-python = ">=3.11"' in (repo / "pyproject.toml").read_text()
    assert (repo / "uv.lock").is_file()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    )
    assert status.stdout == ""
