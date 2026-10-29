"""Closed revision operands enable real read-only planning without writer admission."""

import json
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from harness.command_safety import classify_command, git_read
from tests.test_execution_operations import operations  # noqa: F401


@pytest.mark.parametrize(
    "revision",
    [
        "HEAD..refs/remotes/origin/main",
        "refs/heads/feature..HEAD",
        "refs/tags/v1.0.0..HEAD",
        "a" * 40 + "..HEAD",
        "HEAD.." + "b" * 64,
    ],
)
def test_closed_log_revisions(tmp_path, revision):
    request = {"argv": ["git", "log", "--oneline", "-n10", revision], "cwd": str(tmp_path)}
    assert classify_command(request)["decision"] == "read_only"


@pytest.mark.parametrize(
    "operands",
    [
        ["HEAD...refs/remotes/origin/main"],
        ["HEAD.."],
        ["..HEAD"],
        ["HEAD~1..HEAD"],
        ["HEAD:.env"],
        ["refs/heads/../secret..HEAD"],
        ["refs/heads/.private..HEAD"],
        ["refs/heads/main.lock..HEAD"],
        ["HEAD..refs/remotes/origin//main"],
        ["HEAD..refs/remotes/origin/main\n"],
        ["--output=/tmp/unowned"],
        ["--format=%(contents)"],
        ["--ext-diff"],
        ["-n101", "HEAD..HEAD"],
        ["--", ".env"],
        ["HEAD..HEAD", "refs/heads/main"],
    ],
)
def test_unsafe_log_operands_need_authority(tmp_path, operands):
    request = {"argv": ["git", "log", "--oneline", *operands], "cwd": str(tmp_path)}
    assert classify_command(request)["decision"] == "approval_required"


def test_actual_git_read_has_no_patch_or_secret_output(sample_repo):
    assert git_read(sample_repo, ["log", "--oneline", "HEAD..HEAD"]) == ""
    with pytest.raises(ValueError, match="closed grammar"):
        git_read(sample_repo, ["log", "-p", "HEAD"])


def test_actual_readonly_architect_can_inspect_range(operations, tmp_path):  # noqa: F811
    operation, _, _, _, _, _, _ = operations
    root = Path(operation.envelope["repository"])
    # This is the disposable fixture repository, never a user/source checkout.
    assert root == tmp_path / "repo"
    subprocess.run(
        ["git", "-C", str(root), "update-ref", "refs/remotes/origin/main", "HEAD"],
        check=True,
        capture_output=True,
    )
    native = operation.registry.native_root / "sessions" / "2026" / "10" / "29"
    native.mkdir(parents=True)
    child = str(uuid.uuid4())
    for thread, source in [
        (operation.registry.root, "exec"),
        (
            child,
            {
                "subagent": {
                    "thread_spawn": {
                        "parent_thread_id": operation.registry.root,
                        "agent_role": "al-architect",
                    }
                }
            },
        ),
    ]:
        payload = {"id": thread, "session_id": thread, "cli_version": "0.164.0", "source": source}
        (native / f"rollout-{thread}.jsonl").write_text(
            json.dumps({"type": "session_meta", "payload": payload}) + "\n"
        )
    principal = operation.registry.enroll(child)
    assert principal["role"] == "al-architect" and not principal["writable"]
    assert operation.dispatch(principal, "await_assignment", {"timeout": 1})["status"] == "ReadOnly"
    result = operation.dispatch(
        principal,
        "run_command",
        {
            "argv": ["git", "log", "--oneline", "HEAD..refs/remotes/origin/main"],
            "cwd": ".",
            "task_id": "none",
            "expected_revision": "none",
        },
    )
    deadline = time.monotonic() + 10
    while True:
        status = operation.dispatch(principal, "poll_process", {"handle": result["handle"]})
        if status["status"] != "Running" or time.monotonic() >= deadline:
            break
        time.sleep(0.02)
    assert status["status"] == "Exited" and status["returncode"] == 0, status
    assert operation.assignments == {}
