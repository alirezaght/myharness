"""Actual Claude must collect a Harness-owned command after attempting to finish."""

import json
import os
import secrets
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest

from harness.execution_bootstrap import authority_snapshot
from harness.execution_mcp import serve_mcp
from harness.execution_principals import PrincipalRegistry
from harness.execution_processes import ProcessManager
from harness.native_tool_policy import ROLES, build_claude_policy
from tests.native_model_fixture import anthropic_fixture, native_executable
from tests.test_claude_stop_guard import process_record


@pytest.mark.native
def test_actual_claude_stop_hook_keeps_owned_command_until_collected(tmp_path):
    session = str(uuid.uuid4())
    registry = PrincipalRegistry(tmp_path / "principals", tmp_path / "native", {}, "policy")
    context = {
        "execution_id": "native-stop-fixture",
        "session_id": session,
        "epoch": secrets.token_hex(16),
        "policy_digest": "policy",
        "signing_key": secrets.token_hex(32),
        "registry": str(registry.directory),
        "channels": {"harness": {"role": "orchestrator", "source": "dynamic"}},
    }
    processes = ProcessManager()
    children, output, events = [], [], []
    stage = 0

    def plan(request, index):
        nonlocal stage
        stage += 1
        if stage == 1:
            return ("mcp__harness__al_command", {"arguments": ["gates", "discover"]})
        if stage == 2:
            return None  # Reproduce the premature final response with a pending handle.
        if stage == 3:
            assert "process-discovery" in json.dumps(request["messages"])
            return ("mcp__harness__poll_process", {"handle": "process-discovery"})
        return None

    def dispatch(principal, name, arguments):
        assert principal["role"] == "orchestrator"
        if name == "al_command":
            child = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", "import time;time.sleep(.5);print('manifest')"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            children.append(child)
            processes.records["process-discovery"] = process_record(session)
            events.append("started")
            return {"handle": "process-discovery", "status": "Running"}
        if name == "read_file":
            assert arguments == {"path": "harness:pending-work"}
            pending = processes.pending()
            events.append("stop-pending" if pending else "stop-allowed")
            return {"pending": pending, "queued_setup": 0}
        assert name == "poll_process"
        stdout, stderr = children[0].communicate(timeout=5)
        assert children[0].returncode == 0 and not stderr
        record = processes.records["process-discovery"]
        record.update(status="Exited", returncode=0, output=stdout.decode())
        events.append("collected")
        return processes.poll(session, arguments["handle"])

    def verify(channel, name, arguments):
        return registry.resolve_claude(channel, name, arguments, context)

    with (
        serve_mcp(
            None, dispatch, verifier=verify, claim=registry.claim_attestation, channel="harness"
        ) as transport,
        anthropic_fixture(plan) as (provider_url, observed),
    ):
        context["stop_transport"] = transport
        context_path = tmp_path / "context.json"
        context_path.write_text(json.dumps(context))
        context_path.chmod(0o600)
        trusted = authority_snapshot(tmp_path / "trusted-runtime")
        program = (
            "import sys,runpy;sys.path.insert(0,"
            + repr(trusted)
            + ");runpy.run_module('harness.claude_identity_hook',run_name='__main__')"
        )
        hook = [sys.executable, "-I", "-B", "-c", program, "--context", str(context_path)]
        package = {
            "package": str(Path(__file__).resolve().parents[1] / "al/claude/skills/al"),
            "executable": native_executable("claude"),
        }
        channels = {role: transport for role in ("orchestrator", *ROLES)}
        policy = build_claude_policy(package, tmp_path / "profile", channels, hook, session)
        process = subprocess.Popen(
            [
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
                "Discover gates, then continue until the command is collected.",
            ],
            cwd=tmp_path,
            env=dict(
                os.environ,
                CLAUDE_CONFIG_DIR=str(tmp_path / "native-config"),
                ANTHROPIC_BASE_URL=provider_url,
                ANTHROPIC_API_KEY="synthetic-stop-fixture",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def observe():
            for line in process.stdout:
                output.append(line)
                value = json.loads(line)
                if value.get("type") == "system" and value.get("subtype") == "init":
                    registry.observe_claude_root(value["session_id"])

        thread = threading.Thread(target=observe, daemon=True)
        thread.start()
        try:
            process.wait(timeout=45)
            thread.join(timeout=3)
            stderr = process.stderr.read()
            assert process.returncode == 0, (output[-10:], stderr)
            assert events == ["started", "stop-pending", "collected", "stop-allowed"], (
                events,
                output[-12:],
                stderr,
            )
            assert not processes.pending() and children[0].poll() == 0
            assert len(observed) >= 4
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            thread.join(timeout=3)
            process.stdout.close()
            process.stderr.close()
            for child in children:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=5)
                if child.stdout:
                    child.stdout.close()
                if child.stderr:
                    child.stderr.close()
