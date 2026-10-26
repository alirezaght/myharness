"""Actual native child calls resolve their native-injected protocol metadata."""

import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

from harness.execution_mcp import serve_mcp
from harness.execution_principals import PrincipalRegistry
from harness.native_tool_policy import ROLES, build_native_policy
from tests.native_model_fixture import (
    codex_fixture_argv,
    native_executable,
    responses_fixture,
    tool_inventory,
)

pytestmark = pytest.mark.native


@pytest.mark.parametrize("role", ROLES)
def test_actual_codex_root_and_child_lineage(tmp_path, role):
    repository = tmp_path / "repo"
    repository.mkdir()
    native_home = tmp_path / "native"
    native_home.mkdir()
    registry_box = {}
    resolved = []
    errors = []
    root_stages = []
    child_stages = []
    resuming = []

    def resolve(meta):
        try:
            principal = registry_box["registry"].resolve(meta)
            resolved.append(principal)
            return principal
        except (ValueError, KeyError) as error:
            errors.append(str(error))
            raise ValueError(str(error)) from None

    def dispatch(principal, name, arguments):
        # Profile/identity liveness proof: no operation effects are requested here.
        if name != "read_file":
            raise ValueError("not admitted in profile proof")
        return {"principal": principal["role"], "status": "identity_verified"}

    def plan(request, index):
        child = any(
            "# AL-" in json.dumps(item)
            for item in request.get("input", [])
            if item.get("role") == "developer"
        )
        if child:
            child_stages.append(index)
            if len(child_stages) == 1:
                return ("mcp__harness.read_file", {"path": "README.md"})
            if len(child_stages) == 2:
                return ("functions.exec_command", {"cmd": "touch " + str(repository / "ESCAPE")})
            if len(child_stages) == 3:
                return (
                    "functions.apply_patch",
                    {"patch": "*** Begin Patch\n*** Add File: ESCAPE\n+escape\n*** End Patch"},
                )
            return None
        if not root_stages:
            root_stages.append("spawn")
            if resuming:
                return (
                    "collaboration.followup_task",
                    {"target": "/root/identity_child", "message": "Resume child identity proof"},
                )
            spawn = tool_inventory(request)["collaboration.spawn_agent"]
            assert "agent_type" in spawn["parameters"]["properties"]
            return (
                "collaboration.spawn_agent",
                {
                    "task_name": "identity_child",
                    "agent_type": role,
                    "message": "CHILD_IDENTITY_FIXTURE. Use controller read_file for README.md.",
                    "fork_turns": "none",
                },
            )
        if len(root_stages) == 1:
            root_stages.append("wait")
            return ("collaboration.wait_agent", {"timeout_ms": 1000})
        if len(root_stages) == 2:
            root_stages.append("root_read")
            return ("mcp__harness.read_file", {"path": "README.md"})
        return None

    with serve_mcp(resolve, dispatch) as mcp, responses_fixture(plan) as (url, observed):
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
        registry = PrincipalRegistry(
            tmp_path / "principals", native_home, {"execution": "fixture"}, policy["digest"]
        )
        registry_box["registry"] = registry

        def drive(resume=None):
            proc = subprocess.Popen(
                codex_fixture_argv(policy, url, repository, resume=resume),
                cwd=repository,
                env=dict(os.environ, CODEX_HOME=str(native_home)),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            output = []

            def observe():
                for line in proc.stdout:
                    output.append(line)
                    value = json.loads(line)
                    if value.get("type") == "thread.started":
                        registry.observe_root_start(value["thread_id"])

            observer = threading.Thread(target=observe, daemon=True)
            observer.start()
            proc.wait(timeout=45)
            observer.join(timeout=2)
            stderr = proc.stderr.read()
            proc.stdout.close()
            proc.stderr.close()
            print(
                "native lifecycle",
                "".join(
                    line for line in output if "collab" in line or "mcp" in line or "failed" in line
                ),
            )
            assert proc.returncode == 0, (output, stderr)
            return output, stderr

        output, stderr = drive()

        print(
            "resolved native roles",
            [(p["thread_id"], p["role"], p["session_id"]) for p in resolved],
            "identity errors",
            errors,
        )
        assert {p["role"] for p in resolved} >= {"orchestrator", role}, (
            output[-8:],
            errors,
            stderr,
        )
        first_child = next(p["thread_id"] for p in resolved if p["role"] == role)
        root = registry.root
        prior_count = len(resolved)
        root_stages.clear()
        child_stages.clear()
        resuming.append(True)
        output, stderr = drive(resume=root)
        assert registry.root == root
        assert any(
            p["thread_id"] == first_child and p["role"] == role for p in resolved[prior_count:]
        ), (output, errors, stderr)
        inventories = [list(tool_inventory(v)) for v in observed]
        assert len(observed) >= 6, inventories
        assert not (repository / "ESCAPE").exists()
        assert child_stages, "native child did not execute"
        assert all(
            not any(t in names for t in ["functions.exec_command", "functions.apply_patch"])
            for names in inventories
        )
