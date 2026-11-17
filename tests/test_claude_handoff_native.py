"""Exact installed Claude receives complete handoffs through authenticated existing resources."""

import json
import os
import secrets
import subprocess
import sys
import threading
from contextlib import ExitStack, closing

import pytest

from harness.controller import HarnessController
from harness.db import connect
from harness.execution_bootstrap import authority_snapshot
from harness.execution_mcp import serve_mcp
from harness.native_tool_policy import ROLES, build_claude_policy
from tests.native_model_fixture import anthropic_fixture, native_executable
from tests.test_execution_operations import generated_handoff_fixture
from tests.test_execution_operations import operations as operations

pytestmark = pytest.mark.native


def strings(value):
    if isinstance(value, str):
        yield value
        if value.lstrip().startswith(("{", "[")):
            try:
                yield from strings(json.loads(value))
            except ValueError:
                pass
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


@pytest.mark.parametrize("operations", ["claude"], indirect=True)
@pytest.mark.parametrize("length", [1400, 6935])
def test_actual_claude_full_assignment_handoff_delivery(operations, tmp_path, monkeypatch, length):
    monkeypatch.setenv("HARNESS_TEST_CLAUDE_VERSION", "2.1.325")
    monkeypatch.setenv("HARNESS_REQUIRE_NATIVE_TESTS", "1")
    executable = native_executable("claude")
    operation, supervisor, _, _, package, execution, envelope = operations
    registry = operation.registry
    session = registry.root
    channel_names = {"orchestrator": "harness"} | {
        r: "harness_" + r.replace("-", "_") for r in ROLES
    }
    context = {
        "execution_id": execution["id"],
        "session_id": session,
        "epoch": secrets.token_hex(16),
        "policy_digest": registry.policy,
        "signing_key": secrets.token_hex(32),
        "registry": str(registry.directory),
        "channels": {
            name: {"role": role, "source": "dynamic" if role == "orchestrator" else "agent"}
            for role, name in channel_names.items()
        },
    }
    context_path = tmp_path / "native-context.json"
    context_path.write_text(json.dumps(context))
    trusted = authority_snapshot(tmp_path / "native-runtime")
    program = (
        "import sys,runpy;sys.path.insert(0,"
        + repr(trusted)
        + ");runpy.run_module('harness.claude_identity_hook',run_name='__main__')"
    )
    hook = [sys.executable, "-I", "-B", "-c", program, "--context", str(context_path)]
    tail = "FULL_HANDOFF_TAIL_" + str(length)
    message = "H" * (length - len(tail)) + tail
    assert len(message) == length
    shared = {
        "root_stage": 0,
        "pages": [],
        "delivered": set(),
        "direct": False,
        "expected": None,
        "resource": None,
        "offset": 0,
        "complete": False,
        "descriptor": None,
        "errors": [],
        "root_polls": 0,
        "message_result_ok": False,
    }
    done = threading.Event()
    observer_errors = []
    own = "mcp__harness_al_engineer__"

    def dispatch(principal, name, arguments):
        if (
            principal["role"] == "orchestrator"
            and name == "read_file"
            and arguments["path"] == "harness:pending-work"
        ):
            assert done.wait(25), "child did not receive the full authenticated handoff"
        try:
            result = operation.dispatch(principal, name, arguments)
        except Exception as error:
            shared["errors"].append(type(error).__name__ + ": " + str(error))
            done.set()
            raise ValueError(str(error)) from None
        if principal["role"] == "al-engineer":
            if (
                name == "await_assignment"
                and result["status"] == "Assigned"
                and result["handoff"]["status"] == "available"
            ):
                shared["descriptor"] = result["handoff"]
                shared["resource"] = result["handoff"]["resource"]
            elif name == "read_file":
                shared["pages"].append(result["text"])
                shared["offset"] = result["next_offset"]
                shared["complete"] = not result["truncated"]
        return result

    def verifier(channel, name, arguments):
        return registry.resolve_claude(channel, name, arguments, context)

    def plan(request, index):
        child = "Agent" not in [t["name"] for t in request.get("tools", [])]
        if child:
            if shared["errors"]:
                return None
            native_strings = list(strings(request.get("messages", [])))
            shared["direct"] |= any(message in part for part in native_strings)
            shared["delivered"].update(page for page in shared["pages"] if page in native_strings)
            if shared["complete"]:
                assert all(page in shared["delivered"] for page in shared["pages"])
                done.set()
                return None
            if shared["resource"]:
                return own + "read_file", {
                    "path": shared["resource"],
                    "offset": shared["offset"],
                    "limit": 613,
                }
            return own + "await_assignment", {"timeout": 1}
        stage = shared["root_stage"]
        if stage == 0:
            shared["root_stage"] = 1
            return "Agent", {
                "subagent_type": "al-engineer",
                "description": "Authenticated full handoff proof",
                "prompt": "Await your assignment and read every handoff.resource page "
                "before finishing.",
                "run_in_background": True,
            }
        if stage == 1:
            children = list(registry.directory.glob("claude-agent-*.json"))
            if not children:
                shared["root_polls"] += 1
                if shared["root_polls"] > 20:
                    shared["errors"].append(
                        "native child enrollment unavailable: "
                        + str(list(strings(request.get("messages", [])))[-2:])
                    )
                    done.set()
                    return None
                return "mcp__harness__read_file", {"path": "harness:capabilities"}
            assert len(children) == 1
            child_id = json.loads(children[0].read_bytes())["agent_id"]
            with closing(connect(operation.database)) as conn:
                local = (*operations[:2], HarnessController(conn), *operations[3:])
                _, expected = generated_handoff_fixture(local, child_id=child_id, body=message)
            shared["expected"] = expected
            shared["root_stage"] = 2
            return "SendMessage", {
                "to": child_id,
                "message": message,
                "summary": "Read the complete authenticated assignment",
            }
        if stage == 2:
            results = [
                block
                for item in request.get("messages", [])
                for block in item.get("content", [])
                if isinstance(block, dict) and block.get("type") == "tool_result"
            ]
            shared["message_result_ok"] = bool(results and not results[-1].get("is_error"))
            shared["root_stage"] = 3
            return "mcp__harness__read_file", {"path": "harness:pending-work"}
        return None

    with ExitStack() as stack:
        channels = {
            role: stack.enter_context(
                serve_mcp(
                    None,
                    dispatch,
                    verifier=verifier,
                    claim=registry.claim_attestation,
                    channel=name,
                )
            )
            for role, name in channel_names.items()
        }
        context["stop_transport"] = channels["orchestrator"]
        context_path.write_text(json.dumps(context))
        policy = build_claude_policy(
            package | {"executable": executable},
            tmp_path / "native-policy",
            channels,
            hook,
            session,
        )
        url, observed = stack.enter_context(anthropic_fixture(plan))
        process = subprocess.Popen(
            [
                executable,
                "--print",
                "--verbose",
                "--output-format",
                "stream-json",
                *policy["options"],
                "--session-id",
                session,
                "--model",
                "sonnet",
                "Deliver the authenticated handoff",
            ],
            cwd=envelope["repository"],
            env=dict(
                os.environ,
                CLAUDE_CONFIG_DIR=str(tmp_path / "native-config"),
                ANTHROPIC_BASE_URL=url,
                ANTHROPIC_API_KEY="synthetic-handoff-fixture",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        output = []

        def observe():
            for line in process.stdout:
                output.append(line)
                value = json.loads(line)
                if value.get("type") == "system" and value.get("subtype") == "init":
                    try:
                        registry.observe_claude_root(value["session_id"])
                    except ValueError as error:
                        observer_errors.append(str(error))

        observer = threading.Thread(target=observe, daemon=True)
        observer.start()
        try:
            process.wait(timeout=90)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            observer.join(timeout=3)
        stderr = process.stderr.read()
        process.stdout.close()
        process.stderr.close()
        assert process.returncode == 0 and not observer_errors, (
            observer_errors,
            output[-5:],
            stderr[-2000:],
        )
        assert not shared["errors"], shared["errors"]
        assert shared["message_result_ok"], (output[-5:], stderr[-2000:])
        assert done.is_set(), (shared, output[-5:], stderr[-2000:])
        assert "".join(shared["pages"]).encode() == shared["expected"]
        assert tail in "".join(shared["pages"])
        assert "AL MANAGED WRITER EVIDENCE END" in "".join(shared["pages"])
        print(
            "Claude2.1.325 full handoff length",
            length,
            "tail delivered through authenticated assignment resource;",
            "direct SendMessage full delivery:",
            shared["direct"],
            "native model requests:",
            len(observed),
        )
