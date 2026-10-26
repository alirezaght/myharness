import os
import shutil
from pathlib import Path

import pytest

from harness.execution_hosts import native_identity, resume_argv, start_argv
from harness.execution_store import get_execution
from harness.execution_worker import supervise
from tests.execution_helpers import approved_execution


def test_fixed_native_argv_and_explicit_resume():
    for host in ("claude", "codex"):
        start = start_argv(host, host, "/tmp/repo", "123")
        resume = resume_argv(host, host, "/tmp/repo", "explicit-recorded-id")
        assert "explicit-recorded-id" in resume
        assert not set(start + resume) & {
            "--last",
            "--continue",
            "--dangerously-skip-permissions",
            "--yolo",
            "--ephemeral",
        }
        with pytest.raises(ValueError):
            resume_argv(host, host, "/tmp/repo", "")
    with pytest.raises(ValueError):
        native_identity("codex", b'{"type":"thread.started","thread_id":"bad"}')


def test_real_external_host_exit_zero_is_incomplete(
    controller, sample_repo, monkeypatch, tmp_path, launched
):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    source = Path(__file__).resolve().parents[1]
    package = sample_repo / ".claude/skills/al"
    shutil.copytree(source / "al/claude/skills/al", package)
    shutil.copytree(source / "al/claude/agents", sample_repo / ".claude/agents/al")
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "alctl").symlink_to(launched[3])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-fixture-provider-key")
    from tests.native_provider_fixture import install_fake_claude

    # A provider process with no semantic AL result cannot complete implementation.
    install_fake_claude(binary)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    controller.conn.execute(
        "UPDATE executions SET status='WorkerOwned',worker_token='owner' WHERE id=?", (eid,)
    )
    supervise(controller.conn, eid, "owner")
    saved = get_execution(controller.conn, eid)
    assert saved["status"] == "Interrupted" and saved["result"] is None
    assert "no guarded AL" in saved["error"]


def test_native_error_facts_exclude_reasoning_and_preserve_actionable_failure():
    from harness.execution_hosts import native_diagnostic

    assert (
        native_diagnostic(
            "codex",
            b'{"type":"item.completed","item":{"type":"reasoning","text":"private reasoning"}}',
        )
        is None
    )
    assert "authentication" in native_diagnostic(
        "codex", b'{"type":"turn.failed","error":{"message":"authentication required"}}'
    )
    assert "permission" in native_diagnostic(
        "claude",
        b'{"type":"result","subtype":"error_during_execution","errors":["permission denied"]}',
    )


def test_target_harness_package_cannot_shadow_detached_worker(
    controller, sample_repo, monkeypatch, tmp_path
):
    import time

    from harness.execution_worker import launch_worker

    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    shadow = sample_repo / "harness"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("")
    marker = tmp_path / "untrusted-worker-ran"
    (shadow / "execution_worker.py").write_text(
        "from pathlib import Path\n"
        f'Path({str(marker)!r}).write_text("untrusted target module ran")\n'
    )
    binary = tmp_path / "git-only"
    binary.mkdir()
    (binary / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(binary))
    monkeypatch.setenv("PYTHONPATH", str(sample_repo))
    monkeypatch.setenv("HARNESS_AUTHORITY_ROOT", str(sample_repo))
    launch_worker(controller.conn, eid)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if marker.exists() or get_execution(controller.conn, eid)["status"] == "Unavailable":
            break
        time.sleep(0.05)
    assert not marker.exists(), "target repository replaced the trusted detached worker"
    assert get_execution(controller.conn, eid)["status"] == "Unavailable"
