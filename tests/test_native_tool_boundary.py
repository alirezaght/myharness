"""Actual installed native binary receives malicious omitted tools from model."""

import json
import subprocess
from pathlib import Path

import pytest

from harness.execution_mcp import serve_mcp
from harness.native_tool_policy import build_native_policy
from tests.native_model_fixture import native_executable, responses_fixture

pytestmark = pytest.mark.native


def deny_identity(meta):
    raise ValueError("IDENTITY_NOT_READY")


def no_dispatch(*args):
    raise AssertionError("unregistered principal reached operation dispatch")


def test_installed_codex_omitted_handlers_are_rejected(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    sentinel = repository / "MUST_NOT_EXIST"
    malicious = [
        ("exec_command", {"cmd": "touch " + str(sentinel)}),
        (
            "apply_patch",
            {
                "patch": "*** Begin Patch\n*** Add File: "
                + str(sentinel)
                + "\n+escape\n*** End Patch"
            },
        ),
        ("view_image", {"path": "/private/etc/hosts"}),
        (
            "code_mode",
            {"code": "require('fs').writeFileSync(" + json.dumps(str(sentinel)) + ",'escape')"},
        ),
        ("mcp__foreign__run", {"command": "touch " + str(sentinel)}),
    ]
    with (
        serve_mcp(deny_identity, no_dispatch) as mcp,
        responses_fixture(malicious) as (url, observed),
    ):
        package = {
            "executable": native_executable("codex"),
            "package": str(Path(__file__).resolve().parents[1] / "al/codex/skills/al"),
        }
        policy = build_native_policy(
            {"host": "codex"},
            package,
            {},
            mcp_url=mcp["url"],
            token=mcp["token"],
            directory=tmp_path / "policy",
        )
        argv = [
            policy["executable"]["path"],
            "exec",
            *policy["options"],
            "-c",
            'model_provider="synthetic"',
            "-c",
            'model_providers.synthetic.name="Synthetic fixture"',
            "-c",
            "model_providers.synthetic.base_url=" + json.dumps(url),
            "-c",
            'model_providers.synthetic.wire_api="responses"',
            "-c",
            "model_providers.synthetic.requires_openai_auth=false",
            "-c",
            "features.enable_request_compression=false",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--json",
            "--cd",
            str(repository),
            "--model",
            "gpt-6.1-sol",
            "Native boundary fixture",
        ]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=45)
        assert observed, (result.returncode, result.stdout[-2000:], result.stderr[-4000:])
        assert result.returncode == 0, (result.stdout[-3000:], result.stderr[-3000:])
        raw_tools = list(observed[0]["tools"])
        for item in observed[0]["input"]:
            if item.get("type") == "additional_tools":
                raw_tools.extend(item["tools"])

        def names_for(tools, prefix=""):
            result = []
            for tool in tools:
                if tool.get("type") == "namespace":
                    result.extend(names_for(tool["tools"], prefix + tool["name"] + "."))
                else:
                    result.append(prefix + tool.get("name", tool.get("type", "unknown")))
            return result

        names = names_for(raw_tools)
        print(
            "Codex native binary",
            policy["executable"],
            "complete tool inventory",
            json.dumps(names),
        )
        assert any("spawn_agent" in name for name in names), names
        assert any("harness" in name for name in names), names
        assert not sentinel.exists()
        assert len(observed) >= len(malicious) + 1
        history = json.dumps(observed[-1]["input"])
        assert (
            "unknown" in history.lower()
            or "unsupported" in history.lower()
            or "not available" in history.lower()
        ), history[-2000:]
        assert not set(names) & {name for name, _ in malicious}
