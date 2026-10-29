"""Real read-only operation dispatch with no writer assignment."""

import json
import time
import uuid
from pathlib import Path

import pytest

from harness.command_safety import classify_command
from tests.test_execution_operations import operations  # noqa: F401


@pytest.mark.parametrize(
    "args",
    [
        ["--pre", "sh", "x"],
        ["--follow", "x"],
        ["--files", "../"],
        ["x", "/etc/passwd"],
        ["x", ".env.example"],
        ["--config", "evil"],
        ["--files", "--unknown"],
    ],
)
def test_discovery_rejects_unsafe_arguments(tmp_path, args):
    result = classify_command({"argv": ["rg", *args], "cwd": str(tmp_path)})
    assert result["decision"] == "approval_required"


def test_discovery_rejects_symlink_and_environment(tmp_path):
    (tmp_path / "alias").symlink_to("/etc")
    assert (
        classify_command({"argv": ["rg", "x", "alias"], "cwd": str(tmp_path)})["decision"]
        == "approval_required"
    )
    assert (
        classify_command(
            {
                "argv": ["rg", "--files"],
                "cwd": str(tmp_path),
                "environment": {"RIPGREP_CONFIG_PATH": "evil"},
            }
        )["decision"]
        == "approval_required"
    )


def test_readonly_real_dispatch_contains_secret_and_symlink(operations):  # noqa: F811
    operation, principal, _, _, _, _, _ = operations
    native_root = operation.registry.native_root / "sessions" / "2026" / "10" / "28"
    native_root.mkdir(parents=True)
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
        (native_root / ("rollout-" + thread + ".jsonl")).write_text(
            json.dumps({"type": "session_meta", "payload": payload}) + "\n"
        )
    principal = operation.registry.enroll(child)
    assert principal["role"] == "al-architect" and principal["writable"] is False
    assert operation.dispatch(principal, "await_assignment", {"timeout": 1})["status"] == "ReadOnly"
    root = Path(operation.envelope["repository"])
    (root / "ordinary-discovery.txt").write_text("DISCOVERY_ORDINARY_SENTINEL\n")
    (root / ".env.example").write_text("DISCOVERY_SECRET_SENTINEL\n")
    (root / "secret-alias").symlink_to(root / ".env.example")
    for argv, expected in [
        (["rg", "--files", "--hidden"], "ordinary-discovery.txt"),
        (["rg", "--hidden", "--glob", "**", "DISCOVERY_"], "DISCOVERY_ORDINARY_SENTINEL"),
    ]:
        result = operation.dispatch(
            principal,
            "run_command",
            {"argv": argv, "cwd": ".", "task_id": "none", "expected_revision": "none"},
        )
        output, deadline = "", time.monotonic() + 10
        while time.monotonic() < deadline:
            value = operation.dispatch(principal, "poll_process", {"handle": result["handle"]})
            output += value["output"]
            if value["status"] != "Running":
                break
            time.sleep(0.02)
        assert value["status"] == "Exited" and value["returncode"] == 0, (value, output)
        assert expected in output
        assert "DISCOVERY_SECRET_SENTINEL" not in output and ".env.example" not in output
    assert operation.assignments == {}
    with pytest.raises(ValueError):
        operation.dispatch(
            principal,
            "run_command",
            {
                "argv": ["rg", "DISCOVERY_", "secret-alias"],
                "cwd": ".",
                "task_id": "none",
                "expected_revision": "none",
            },
        )


def test_os_boundary_denies_secret_even_without_discovery_filter(operations):  # noqa: F811
    import subprocess

    from harness.command_discovery import trusted_rg
    from harness.command_safety import safe_environment
    from harness.execution_isolation import operation_popen

    operation, _, _, _, _, _, _ = operations
    root = Path(operation.envelope["repository"])
    secret = root / ".env.example"
    secret.write_text("OS_SECRET_SENTINEL")
    proc = operation_popen(
        operation.profile(),
        [trusted_rg(root), "--no-config", "OS_SECRET", str(secret)],
        cwd=root,
        env=safe_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output, error = proc.communicate(timeout=10)
    assert proc.returncode != 0 and b"OS_SECRET_SENTINEL" not in output, (output, error)
