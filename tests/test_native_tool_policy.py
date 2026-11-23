"""Version-specific native controller profile checks against installed executables."""

import os
import subprocess

import pytest

from tests.native_model_fixture import native_executable


@pytest.mark.native
@pytest.mark.parametrize("host,version", [("codex", "0.164.0"), ("claude", "2.1.310")])
def test_installed_native_profile_prerequisites(host, version):
    version = os.environ.get("HARNESS_TEST_" + host.upper() + "_VERSION", version)
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
        lambda *a, **k: {"path": "synthetic-unit-executable", "version": "2.1.325"},
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


def test_generated_codex_roles_await_assignment_before_reading_complete_handoff(
    tmp_path, monkeypatch
):
    import tomllib
    from pathlib import Path

    from harness.native_tool_policy import ROLES, build_native_policy

    monkeypatch.setattr(
        "harness.native_tool_policy.executable_identity",
        lambda *_, **k: {
            "path": "synthetic-unit-executable",
            "version": "0.164.0",
            "sha256": "fixture",
        },
    )
    build_native_policy(
        {"host": "codex"},
        {
            "package": str(Path(__file__).resolve().parents[1] / "al/codex/skills/al"),
            "executable": "synthetic-unit-executable",
        },
        {},
        mcp_url="http://127.0.0.1:1/mcp",
        token="synthetic-unit-token",
        directory=tmp_path,
    )
    for role in ROLES:
        instructions = tomllib.loads((tmp_path / (role + ".toml")).read_text())[
            "developer_instructions"
        ]
        assert instructions.index("first call await_assignment") < instructions.index(
            "handoff.resource"
        )
        assert "next_offset until truncated is false" in instructions
        assert "complete managed evidence block" in instructions


@pytest.mark.parametrize(
    "reported,accepted",
    [
        ("2.1.326 (Claude Code)", True),
        ("2.1.330 (Claude Code)", True),
        ("2.1.310 (Claude Code)", True),
        ("2.1.3260 (Claude Code)", True),
        ("2.1.309 (Claude Code)", False),
        ("2.2.0 (Claude Code)", False),
        ("3.0.0 (Claude Code)", False),
        ("prefix 2.1.326", False),
        ("2.1.326\n2.1.327", False),
    ],
)
def test_claude_qualification_accepts_tested_release_line(
    tmp_path, monkeypatch, reported, accepted
):
    from types import SimpleNamespace

    from harness.native_tool_policy import executable_identity

    executable = tmp_path / "synthetic-claude"
    executable.write_text("synthetic executable identity")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=reported)
    )
    if accepted:
        identity = executable_identity(executable, "claude")
        assert identity["version"] == reported.split()[0]
        assert "qualification" not in identity
    else:
        with pytest.raises(ValueError, match="unsupported native executable version"):
            executable_identity(executable, "claude")


def test_unsupported_version_names_range_and_override(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from harness.native_tool_policy import executable_identity

    executable = tmp_path / "synthetic-claude"
    executable.write_text("synthetic executable identity")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="2.2.1")
    )
    with pytest.raises(ValueError) as error:
        executable_identity(executable, "claude")
    message = str(error.value)
    assert "claude 2.2.1" in message
    assert ">=2.1.310,<2.2.0" in message
    assert "--accept-unverified-host-version" in message


def test_unverified_override_binds_exact_version_and_binary(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from harness.native_tool_policy import (
        executable_identity,
        record_override,
        recorded_override,
        verify_native_policy,
    )

    executable = tmp_path / "synthetic-codex"
    executable.write_text("synthetic executable identity")
    reported = {"stdout": "codex-cli 0.172.0"}
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, **reported)
    )
    identity = executable_identity(executable, "codex", accept_unverified=True)
    record = identity["qualification"]
    assert record["status"] == "UNVERIFIED" and record["version"] == "0.172.0"
    assert record["sha256"] == identity["sha256"]
    assert "--accept-unverified-host-version" in record["accepted_by"]
    assert record["supported"] == ">=0.164.0,<0.166.0 or >=0.170.0,<0.171.0"
    assert recorded_override(tmp_path) is False
    record_override(tmp_path, record)
    assert recorded_override(tmp_path) == record
    assert executable_identity(executable, "codex", accept_unverified=record) == identity
    verify_native_policy({"host": "codex", "executable": identity})
    reported["stdout"] = "codex-cli 0.173.0"
    with pytest.raises(ValueError, match="unsupported native executable version"):
        executable_identity(executable, "codex", accept_unverified=record)
    reported["stdout"] = "codex-cli 0.172.0"
    executable.write_text("replaced binary")
    with pytest.raises(ValueError, match="unsupported native executable version"):
        executable_identity(executable, "codex", accept_unverified=record)
    with pytest.raises(ValueError, match="unsupported native executable version"):
        verify_native_policy({"host": "codex", "executable": identity})


@pytest.mark.parametrize(
    "version,execution",
    [
        ("0.164.0", True),
        ("0.165.0", True),
        ("0.165.4", True),
        ("0.166.0", False),
        ("0.170.0", True),
        ("0.170.2", True),
        ("0.171.0", False),
    ],
)
def test_codex_tested_release_lines_admit_execution_and_authoring(tmp_path, version, execution):
    import json
    import uuid

    from harness.authoring_policy import build_profile, manifest_version
    from harness.native_identity import read_native_identity
    from harness.native_tool_policy import executable_identity, version_supported
    from tests.test_authoring_native import native170_executable

    executable = native170_executable(tmp_path, version=version)
    assert version_supported("codex", version) is execution
    if execution:
        assert executable_identity(executable, "codex")["version"] == version
    else:
        with pytest.raises(ValueError, match="unsupported native executable version"):
            executable_identity(executable, "codex")
    if manifest_version(version) == "0.170.0":
        assert (
            build_profile("codex", tmp_path, executable=executable)["executable"]["version"]
            == version
        )
    elif not execution:
        assert manifest_version(version) is None
        with pytest.raises(ValueError):
            build_profile("codex", tmp_path, executable=executable)
    root = tmp_path / "native"
    sessions = root / "sessions/2026/11/19"
    sessions.mkdir(parents=True)
    thread = str(uuid.uuid4())
    path = sessions / ("rollout-" + thread + ".jsonl")
    path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": thread,
                    "session_id": str(uuid.uuid4()),
                    "cli_version": version,
                    "source": "cli",
                },
            }
        )
        + "\n"
    )
    if execution:
        assert read_native_identity(root, thread).version == version
    else:
        with pytest.raises(ValueError, match="identity/version mismatch"):
            read_native_identity(root, thread)
        override = read_native_identity(root, thread, accepted_versions=(version,))
        assert override.version == version


def _codex_features_executable(tmp_path, *, ignore=None):
    executable = tmp_path / "codex-features"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import sys,tomllib\n"
        "args=sys.argv[1:]\n"
        "if '--version' in args: print('codex-cli 0.170.1'); raise SystemExit()\n"
        "assert args[-2:]==['features','list']\n"
        "for i,arg in enumerate(args):\n"
        "  if arg=='-c' and args[i+1].startswith('features.'):\n"
        "    key,raw=args[i+1][9:].split('=',1)\n"
        "    value=tomllib.loads('v='+raw)['v']\n"
        "    if key==" + repr(ignore) + ": value=not value\n"
        "    print(key+' stable '+str(value).lower())\n"
    )
    executable.chmod(0o700)
    return executable


def _codex_policy(tmp_path, executable):
    from pathlib import Path

    from harness.native_tool_policy import build_native_policy

    return build_native_policy(
        {"host": "codex"},
        {
            "package": str(Path(__file__).resolve().parents[1] / "al/codex/skills/al"),
            "executable": str(executable),
        },
        {},
        mcp_url="http://127.0.0.1:1/mcp",
        token="synthetic-unit-token",
        directory=tmp_path / "policy",
    )


def test_codex_170_execution_disables_its_qualified_tool_inventory(tmp_path):
    from harness.authoring_policy import feature_values

    policy = _codex_policy(tmp_path, _codex_features_executable(tmp_path))
    options = policy["options"]
    pairs = [options[i + 1] for i in range(0, len(options), 2)]
    assert "features.code_mode_host_grpc=false" in pairs
    assert "features.browser_use=false" in pairs
    assert "features.multi_agent_v2=true" in pairs and "agents.enabled=true" in pairs
    assert "suppress_unstable_features_warning=true" in pairs
    expected = {
        "features." + key + "=" + ("true" if value else "false")
        for key, value in feature_values("0.170.0").items()
        if key not in {"multi_agent", "multi_agent_v2"}
    }
    assert expected <= set(pairs)
    assert not any(pair.startswith("features.multi_agent=") for pair in pairs)


def test_codex_170_execution_probe_refuses_ignored_restriction(tmp_path):
    executable = _codex_features_executable(tmp_path, ignore="browser_use")
    with pytest.raises(ValueError, match="unsupported execution profile"):
        _codex_policy(tmp_path, executable)
