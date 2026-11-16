"""Actual Claude exercises the production-generated role policy and identity hooks."""

import json
import os
import secrets
import subprocess
import sys
import threading
import uuid
from contextlib import ExitStack
from pathlib import Path

import pytest

from harness.execution_bootstrap import authority_snapshot
from harness.execution_mcp import serve_mcp
from harness.execution_principals import PrincipalRegistry
from harness.native_tool_policy import ROLES, build_claude_policy
from tests.native_model_fixture import anthropic_fixture, native_executable

pytestmark = pytest.mark.native


@pytest.mark.parametrize("role", ["al-architect", "al-scout"])
def test_generated_policy_background_child_permissions(tmp_path, role):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("Synthetic assigned repository input.\n")
    session = str(uuid.uuid4())
    registry = PrincipalRegistry(
        tmp_path / "principals", tmp_path / "native", {"fixture": True}, "generated-policy"
    )
    channel_names = {"orchestrator": "harness"} | {
        r: "harness_" + r.replace("-", "_") for r in ROLES
    }
    context = {
        "execution_id": "generated-policy-fixture",
        "session_id": session,
        "epoch": secrets.token_hex(16),
        "policy_digest": "generated-policy",
        "signing_key": secrets.token_hex(32),
        "registry": str(registry.directory),
        "channels": {
            name: {"role": key, "source": "dynamic" if key == "orchestrator" else "agent"}
            for key, name in channel_names.items()
        },
    }
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context))
    context_path.chmod(0o600)
    trusted = authority_snapshot(tmp_path / "runtime")
    program = (
        "import sys,runpy;sys.path.insert(0,"
        + repr(trusted)
        + ");runpy.run_module('harness.claude_identity_hook',run_name='__main__')"
    )
    hook = [sys.executable, "-I", "-B", "-c", program, "--context", str(context_path)]
    verified, stages, observer_errors = [], {}, []
    attempted = []
    child_done, root_turn = threading.Event(), threading.Event()
    own = "mcp__" + channel_names[role] + "__"
    sibling = "al-scout" if role == "al-architect" else "al-architect"

    def dispatch(principal, name, arguments):
        attempted.append((principal["role"], name))
        assert principal["role"] == role, (
            "root or sibling operation escaped generated role isolation"
        )
        assert "__harness_attestation" not in arguments
        if name == "await_assignment":
            result = {"assigned": True, "task": "fixture-task", "role": role}
        elif name == "read_file":
            assert root_turn.wait(10), "background child blocked the root model turn"
            assert arguments["path"] == "README.md"
            result = {"text": (repo / "README.md").read_text()}
        else:
            raise AssertionError("unexpected controller dispatch: " + name)
        verified.append((principal["role"], name))
        return result

    def verifier(channel, name, arguments):
        return registry.resolve_claude(channel, name, arguments, context)

    def plan(request, index):
        child = "Agent" not in [tool["name"] for tool in request.get("tools", [])]
        key = "child" if child else "root"
        stage = stages.get(key, 0)
        stages[key] = stage + 1
        if child:
            calls = [
                (own + "await_assignment", {}),
                (own + "read_file", {"path": "README.md"}),
                ("mcp__harness__read_file", {"path": "README.md"}),
                ("mcp__" + channel_names[sibling] + "__read_file", {"path": "README.md"}),
                ("Bash", {"command": "touch " + str(repo / "ESCAPE")}),
            ]
            if stage < len(calls):
                return calls[stage]
            child_done.set()
            return None
        if stage == 0:
            return (
                "Agent",
                {
                    "subagent_type": role,
                    "description": "Generated background policy probe",
                    "prompt": "Await assignment, read README.md, then finish.",
                    "run_in_background": True,
                },
            )
        root_turn.set()
        if stage == 1:
            return (own + "read_file", {"path": "README.md"})
        if stage == 2:
            return ("Bash", {"command": "touch " + str(repo / "ROOT_ESCAPE")})
        child_done.wait(10)
        return None

    with ExitStack() as stack:
        channels = {
            key: stack.enter_context(
                serve_mcp(
                    None,
                    dispatch,
                    verifier=verifier,
                    claim=registry.claim_attestation,
                    channel=name,
                )
            )
            for key, name in channel_names.items()
        }
        package = {
            "package": str(Path(__file__).resolve().parents[1] / "al/claude/skills/al"),
            "executable": native_executable("claude"),
        }
        policy = build_claude_policy(package, tmp_path / "policy", channels, hook, session)
        url, observed = stack.enter_context(anthropic_fixture(plan))
        argv = [
            policy["executable"]["path"],
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            *policy["options"],
            "--session-id",
            session,
            "--model",
            "sonnet",
            "Generated policy probe",
        ]
        for resume in (False, True):
            stages.clear()
            verified.clear()
            attempted.clear()
            child_done.clear()
            root_turn.clear()
            output = []
            session_index = argv.index("--session-id")
            command = (
                argv
                if not resume
                else [
                    *argv[:session_index],
                    *argv[session_index + 2 : -1],
                    "--resume",
                    session,
                    argv[-1],
                ]
            )
            process = subprocess.Popen(
                command,
                cwd=repo,
                env=dict(
                    os.environ,
                    CLAUDE_CONFIG_DIR=str(tmp_path / "native-config"),
                    ANTHROPIC_BASE_URL=url,
                    ANTHROPIC_API_KEY="synthetic-generated-policy-fixture",
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            def observe(stream, captured):
                for line in stream:
                    captured.append(line)
                    value = json.loads(line)
                    if value.get("type") == "system" and value.get("subtype") == "init":
                        try:
                            registry.observe_claude_root(value["session_id"])
                        except ValueError as error:
                            observer_errors.append(str(error))

            thread = threading.Thread(target=observe, args=(process.stdout, output), daemon=True)
            thread.start()
            try:
                process.wait(timeout=45)
                thread.join(timeout=3)
                stderr = process.stderr.read()
                assert process.returncode == 0, (output[-10:], stderr)
                assert not observer_errors
                assert child_done.is_set(), (stages, output[-10:], stderr)
                assert verified == [(role, "await_assignment"), (role, "read_file")], (
                    "production-generated permission did not reach authenticated child dispatch",
                    resume,
                    stages,
                    output[-12:],
                    stderr,
                    [item.get("messages", []) for item in observed][-2:],
                )
                assert attempted == verified, "forbidden root/sibling request reached dispatch"
                print(
                    "generated policy native proof",
                    policy["executable"]["version"],
                    role,
                    "resume=" + str(resume),
                    "authenticated operations",
                    verified,
                    "all dispatch attempts",
                    attempted,
                )
                assert not (repo / "ESCAPE").exists() and not (repo / "ROOT_ESCAPE").exists()
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
                thread.join(timeout=3)
                process.stdout.close()
                process.stderr.close()
