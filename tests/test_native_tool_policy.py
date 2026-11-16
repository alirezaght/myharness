"""Version-specific native controller profile checks against installed executables."""

import os
import subprocess

import pytest

from tests.native_model_fixture import native_executable


@pytest.mark.native
@pytest.mark.parametrize("host,version", [("codex", "0.164.0"), ("claude", "2.1.310")])
def test_installed_native_profile_prerequisites(host, version):
    if host == "claude":
        version = os.environ.get("HARNESS_TEST_CLAUDE_VERSION", version)
    executable = native_executable(host)
    assert executable, host
    found = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10)
    assert version in found.stdout, (executable, found.stdout)
    help_result = subprocess.run(
        [executable, *(["exec"] if host == "codex" else []), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert help_result.returncode == 0
    required = (
        ["--ignore-user-config", "--ignore-rules"]
        if host == "codex"
        else ["--restricted", "--strict-mcp-config", "--agents", "--tools"]
    )
    for flag in required:
        assert flag in help_result.stdout, (executable, version, flag)
    print(f"installed native profile {executable} {found.stdout.strip()}")


def test_generated_claude_permissions_cover_exact_declared_channels(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from harness.native_tool_policy import ROLES, TOOLS, build_claude_policy

    monkeypatch.setattr(
        "harness.native_tool_policy.executable_identity",
        lambda *a: {"path": "synthetic-unit-executable", "version": "2.1.325"},
    )
    channels = {
        role: {"url": "http://127.0.0.1:1/mcp", "token": "synthetic-unit-token"}
        for role in ("orchestrator", *ROLES)
    }
    policy = build_claude_policy(
        {
            "package": str(Path(__file__).resolve().parents[1] / "al/claude/skills/al"),
            "executable": "synthetic-unit-executable",
        },
        tmp_path / "policy",
        channels,
        ["synthetic-hook", "--context", "synthetic-context"],
        "synthetic-session",
    )
    options = policy["options"]
    allowed = set(options[options.index("--allowedTools") + 1].split(","))
    profiles = json.loads((tmp_path / "policy/agents.json").read_text())
    expected = {"Agent", "SendMessage", "TaskStop"} | {"mcp__harness__" + name for name in TOOLS}
    for role, profile in profiles.items():
        server = "harness_" + role.replace("-", "_")
        exact = {"mcp__" + server + "__" + name for name in TOOLS}
        assert set(profile["tools"]) == exact
        assert set(profile["mcpServers"][0]) == {server}
        assert {"Bash", "Read", "Write", "Edit", "Agent", "Task", "WebFetch"} <= set(
            profile["disallowedTools"]
        )
        expected |= exact
    assert allowed == expected
    assert not any("*" in name for name in allowed)
    assert options[options.index("--tools") + 1] == "Agent,SendMessage,TaskStop"
    settings = json.loads((tmp_path / "policy/settings.json").read_text())
    assert settings["disableAllHooks"] is False
    assert {
        "SessionStart",
        "SubagentStart",
        "PreToolUse",
        "Stop",
        "WorktreeCreate",
        "WorktreeRemove",
    } == set(settings["hooks"])
