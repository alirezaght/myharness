"""Installed Claude restricted surface: model-injected omitted handlers have no effects."""

import json
import os
import subprocess

import pytest

from harness.execution_mcp import serve_mcp
from tests.native_model_fixture import anthropic_fixture, native_executable
from tests.test_native_tool_boundary import deny_identity, no_dispatch

pytestmark = pytest.mark.native


def test_actual_claude_hidden_handlers(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sentinel = repo / "ESCAPE"
    calls = [
        ("Bash", {"command": "touch " + str(sentinel)}),
        ("Write", {"file_path": str(sentinel), "content": "escape"}),
        ("WebFetch", {"url": "http://127.0.0.1:9/escape", "prompt": "fetch"}),
        ("mcp__foreign__run", {"command": "touch " + str(sentinel)}),
    ]
    with serve_mcp(deny_identity, no_dispatch) as mcp, anthropic_fixture(calls) as (url, observed):
        config = tmp_path / "mcp.json"
        config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "harness": {
                            "type": "http",
                            "url": mcp["url"],
                            "headers": {"Authorization": "Bearer " + mcp["token"]},
                        }
                    }
                }
            )
        )
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"disableAllHooks": True, "enabledPlugins": {}}))
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
            "--tools",
            "Agent",
            "--allowedTools",
            "Agent,mcp__harness__*",
            "--disable-slash-commands",
            "--model",
            "sonnet",
            "Native boundary fixture",
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
        assert observed, (result.returncode, result.stdout[-3000:], result.stderr[-2000:])
        assert result.returncode == 0, (result.stdout[-3000:], result.stderr[-3000:])
        names = [t["name"] for t in observed[0]["tools"]]
        print("actual Claude2.1.310 complete root tools", names)
        assert "Agent" in names
        assert any("harness" in n for n in names)
        assert not {"Bash", "Write", "Edit", "WebFetch"} & set(names)
        assert len(observed) >= len(calls) + 1
        assert not sentinel.exists()
