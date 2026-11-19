import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from harness.execution_hosts import native_identity, resume_argv, start_argv
from harness.execution_store import get_execution
from harness.execution_worker import supervise
from harness.native_diagnostics import message
from tests.execution_helpers import approved_execution


@pytest.mark.parametrize("inherited_ceiling", [None, "600000"])
def test_supervised_claude_waits_for_background_agents_without_changing_auth(
    monkeypatch, inherited_ceiling
):
    from harness.native_runtime import native_auth_environment

    key = "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"
    if inherited_ceiling is None:
        monkeypatch.delenv(key, raising=False)
    else:
        monkeypatch.setenv(key, inherited_ceiling)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "synthetic-auth-selection")
    environment = native_auth_environment("claude")
    assert environment[key] == "0"
    assert environment["ANTHROPIC_AUTH_TOKEN"] == "synthetic-auth-selection"
    assert environment["HOME"] == os.environ["HOME"]
    for host in (None, "codex"):
        assert native_auth_environment(host).get(key) == inherited_ceiling
    assert os.environ.get(key) == inherited_ceiling


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


@pytest.mark.parametrize(
    ("failure_record", "expected_error"),
    [
        (None, "no guarded AL"),
        (
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "errors": [],
                "result": "You've hit your 5-hour limit · resets 9:10pm (Europe/Berlin)",
            },
            "[NATIVE_USAGE_LIMIT]",
        ),
        (
            {
                "type": "assistant",
                "isApiErrorMessage": True,
                "error": "rate_limit",
                "message": {
                    "content": [{"type": "text", "text": "Usage limit reached; resets at 21:10"}]
                },
            },
            "[NATIVE_USAGE_LIMIT]",
        ),
    ],
)
def test_real_external_host_exit_zero_is_incomplete(
    controller, sample_repo, monkeypatch, tmp_path, launched, failure_record, expected_error
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
    executable = install_fake_claude(binary)
    if failure_record:
        with executable.open("a") as stream:
            stream.write(f"\nprint({json.dumps(failure_record)!r}, flush=True)\n")
            stream.write(
                "print(json.dumps({'type':'result','subtype':'success','is_error':False,"
                "'session_id':native_id}), flush=True)\n"
            )
        # The fixture exits immediately after emitting its records. Reap that
        # exact child before representing its absent process group, so restricted
        # test runners need no permission to send signals to native processes.
        popen = subprocess.Popen
        killpg = os.killpg
        children = {}

        def capture_child(argv, *args, **kwargs):
            native_launch = argv[0] == str(executable) and "--version" not in argv
            if native_launch:
                assert kwargs["env"]["CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"] == "0"
            child = popen(argv, *args, **kwargs)
            if native_launch:
                children[child.pid] = child
            return child

        def exited_fixture_group(pid, _signal):
            if pid not in children:
                return killpg(pid, _signal)
            assert children[pid].wait(timeout=5) == 0
            raise ProcessLookupError

        monkeypatch.setattr(subprocess, "Popen", capture_child)
        monkeypatch.setattr(os, "killpg", exited_fixture_group)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    controller.conn.execute(
        "UPDATE executions SET status='WorkerOwned',worker_token='owner' WHERE id=?", (eid,)
    )
    supervise(controller.conn, eid, "owner")
    saved = get_execution(controller.conn, eid)
    assert saved["status"] == "Interrupted" and saved["result"] is None
    assert expected_error in saved["error"]
    assert "Claude success:" not in saved["error"]


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


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (
            {"type": "result", "subtype": "success", "is_error": True},
            message("NATIVE_PROVIDER_FAILED"),
        ),
        (
            {
                "type": "assistant",
                "isApiErrorMessage": True,
                "error": "rate_limit",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "private reasoning"},
                        {"type": "text", "text": "You've hit your 5-hour limit"},
                    ]
                },
            },
            message("NATIVE_USAGE_LIMIT"),
        ),
        (
            {
                "type": "assistant",
                "isApiErrorMessage": True,
                "error": "rate_limit",
                "message": {"content": [{"type": "thinking", "thinking": "private reasoning"}]},
            },
            message("NATIVE_USAGE_LIMIT"),
        ),
        (
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "You've hit your 5-hour limit"}]},
            },
            None,
        ),
        (
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "You've hit your 5-hour limit",
            },
            None,
        ),
    ],
)
def test_claude_limit_diagnostics_require_error_metadata_and_exclude_reasoning(record, expected):
    from harness.execution_hosts import native_diagnostic

    assert native_diagnostic("claude", json.dumps(record).encode()) == expected


def test_claude_limit_diagnostic_is_bounded():
    from harness.execution_hosts import native_diagnostic

    record = {
        "type": "assistant",
        "isApiErrorMessage": True,
        "error": "rate_limit",
        "message": {"content": [{"type": "text", "text": "x" * 5000}]},
    }
    assert native_diagnostic("claude", json.dumps(record).encode()) == (
        message("NATIVE_USAGE_LIMIT")
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
