"""Actual Claude child profile rejects hidden inherited root controller calls."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from harness.execution_mcp import serve_mcp
from harness.native_tool_policy import ROLES
from tests.native_model_fixture import anthropic_fixture, native_executable

pytestmark = pytest.mark.native


@pytest.mark.parametrize("role", ROLES)
def test_actual_claude_child_mcp_boundary(tmp_path, role):
    repo = tmp_path / "repo"
    repo.mkdir()
    root_calls, child_calls, stages = [], [], {}

    def root_resolve(meta):
        root_calls.append(meta)
        return {"role": "orchestrator"}

    def child_resolve(meta):
        child_calls.append(meta)
        return {"role": role}

    def read_dispatch(principal, name, arguments):
        if name != "read_file":
            raise ValueError("profile proof has no mutation admission")
        return {"status": "profile_read", "role": principal["role"]}

    def plan(request, index):
        names = [tool["name"] for tool in request.get("tools", [])]
        child = "Agent" not in names
        key = "child" if child else "root"
        stage = stages.get(key, 0)
        stages[key] = stage + 1
        if child:
            if stage == 0:
                return ("mcp__scout__read_file", {"path": "README.md"})
            if stage == 1:
                return ("mcp__root__al_command", {"arguments": ["show", "--line"]})
            if stage == 2:
                return ("Bash", {"command": "touch " + str(repo / "ESCAPE")})
            return None
        if stage == 0:
            tool = next(tool for tool in request["tools"] if tool["name"] == "Agent")
            print("actual Claude Agent schema", sorted(tool["input_schema"].get("properties", {})))
            return (
                "Agent",
                {
                    "subagent_type": role,
                    "description": "Native child profile proof",
                    "prompt": "Use read_file for README.md then complete child profile proof.",
                },
            )
        return None

    with (
        serve_mcp(root_resolve, read_dispatch) as root,
        serve_mcp(child_resolve, read_dispatch) as child,
    ):
        config = tmp_path / "mcp.json"
        config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "root": {
                            "type": "http",
                            "url": root["url"],
                            "headers": {"Authorization": "Bearer " + root["token"]},
                        }
                    }
                }
            )
        )
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"disableAllHooks": True, "enabledPlugins": {}}))
        role_path = Path(__file__).resolve().parents[1] / ("al/claude/agents/" + role + ".md")
        header, instructions = role_path.read_text().split("---", 2)[1:]
        model = next(
            line.split(":", 1)[1].strip()
            for line in header.splitlines()
            if line.startswith("model:")
        )
        agents = tmp_path / "agents.json"
        agents.write_text(
            json.dumps(
                {
                    role: {
                        "description": "AL Scout",
                        "prompt": instructions,
                        "model": model,
                        "tools": ["mcp__scout__read_file"],
                        "disallowedTools": ["Agent", "Bash", "Read", "Write", "Edit", "WebFetch"],
                        "mcpServers": [
                            {
                                "scout": {
                                    "type": "http",
                                    "url": child["url"],
                                    "headers": {"Authorization": "Bearer " + child["token"]},
                                }
                            }
                        ],
                    }
                }
            )
        )
        with anthropic_fixture(plan) as (url, observed):
            argv = [
                native_executable("claude"),
                "--print",
                "--verbose",
                "--output-format",
                "stream-json",
                "--restricted",
                "--strict-mcp-config",
                "--mcp-config",
                str(config),
                "--settings",
                str(settings),
                "--agents",
                str(agents),
                "--tools",
                "Agent",
                "--allowedTools",
                "Agent,mcp__root__*,mcp__scout__*",
                "--disable-slash-commands",
                "--model",
                "sonnet",
                "Native child boundary fixture",
            ]
            result = subprocess.run(
                argv,
                cwd=repo,
                env=dict(
                    os.environ,
                    CLAUDE_CONFIG_DIR=str(tmp_path / "native"),
                    ANTHROPIC_BASE_URL=url,
                    ANTHROPIC_API_KEY="synthetic-native-fixture",
                ),
                capture_output=True,
                text=True,
                timeout=45,
            )
            assert result.returncode == 0, (result.stdout[-2500:], result.stderr[-2500:])
            print(
                "Claude complete native tool inventories",
                [[t["name"] for t in v["tools"]] for v in observed],
            )
            print("Claude child MCP protocol metadata", child_calls)
            assert child_calls, (result.stdout[-2500:], result.stderr[-2500:], stages)
            assert not root_calls, "child invoked hidden inherited privileged root MCP handler"
            assert not (repo / "ESCAPE").exists()
            initial_records = [
                json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
            ]
            root_id = next(
                v["session_id"]
                for v in initial_records
                if v.get("type") == "system" and v.get("subtype") == "init"
            )
            stages.clear()
            resumed = subprocess.run(
                [*argv[:-1], "--resume", root_id, argv[-1]],
                cwd=repo,
                env=dict(
                    os.environ,
                    CLAUDE_CONFIG_DIR=str(tmp_path / "native"),
                    ANTHROPIC_BASE_URL=url,
                    ANTHROPIC_API_KEY="synthetic-native-fixture",
                ),
                capture_output=True,
                text=True,
                timeout=45,
            )
            assert resumed.returncode == 0, (resumed.stdout[-2500:], resumed.stderr[-2500:])
            resumed_records = [
                json.loads(line) for line in resumed.stdout.splitlines() if line.startswith("{")
            ]
            assert any(v.get("session_id") == root_id for v in resumed_records)
            assert len(child_calls) >= 2
            assert not root_calls
            assert not (repo / "ESCAPE").exists()
