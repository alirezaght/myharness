"""A native turn cannot abandon owned commands or their uncollected results."""

import json
import secrets
import uuid
from types import SimpleNamespace

import pytest

from harness.claude_identity_hook import handle_hook
from harness.execution_mcp import serve_mcp
from harness.execution_principals import PrincipalRegistry
from harness.execution_processes import ProcessManager


def process_record(owner, status="Running", output=""):
    return {
        "owner": owner,
        "status": status,
        "output": output,
        "cursor": 0,
        "returncode": 0 if status == "Exited" else None,
        "error": "cancelled" if status == "Failed" else None,
        "exit_observed": False,
    }


@pytest.mark.parametrize("status", ["Exited", "Failed"])
def test_terminal_process_remains_pending_until_owner_collects_all_pages(status):
    processes = ProcessManager()
    processes.records["process-result"] = process_record("root", status, "abcd")
    assert processes.pending()
    with pytest.raises(ValueError, match="not owned"):
        processes.poll("foreign", "process-result")
    assert processes.pending()
    assert processes.poll("root", "process-result", limit=2)["more"]
    assert processes.pending()
    result = processes.poll("root", "process-result", limit=2)
    assert not result["more"] and result["status"] == status
    assert processes.pending() == []


def test_polling_a_running_process_does_not_observe_its_later_exit():
    processes = ProcessManager()
    record = process_record("root")
    processes.records["process-running"] = record
    processes.poll("root", "process-running")
    record["status"] = "Exited"
    assert processes.pending()
    processes.poll("root", "process-running")
    assert not processes.pending()


def test_pending_work_resource_is_supervisor_only(tmp_path):
    from harness.execution_operations import OperationController

    operation = SimpleNamespace(
        envelope={"repository": str(tmp_path)}, processes=ProcessManager(), setup_pending={}
    )
    principal = {"thread_id": "root", "role": "orchestrator"}
    assert OperationController._dispatch(
        operation, None, principal, "read_file", {"path": "harness:pending-work"}, {}
    ) == {"pending": [], "queued_setup": 0}
    with pytest.raises(ValueError, match="only the supervisor"):
        OperationController._dispatch(
            operation,
            None,
            principal | {"role": "al-engineer"},
            "read_file",
            {"path": "harness:pending-work"},
            {},
        )


@pytest.fixture
def supervised_stop(tmp_path):
    session = str(uuid.uuid4())
    registry = PrincipalRegistry(tmp_path / "principals", tmp_path / "unused", {}, "policy")
    context = {
        "execution_id": "fixture-execution",
        "session_id": session,
        "epoch": secrets.token_hex(16),
        "policy_digest": "policy",
        "signing_key": secrets.token_hex(32),
        "registry": str(registry.directory),
        "channels": {"harness": {"role": "orchestrator", "source": "dynamic"}},
    }
    handle_hook({"hook_event_name": "SessionStart", "session_id": session}, context)
    registry.observe_claude_root(session)
    processes = ProcessManager()
    observations = []
    setup = []

    def verify(channel, name, arguments):
        return registry.resolve_claude(channel, name, arguments, context)

    def dispatch(principal, name, arguments):
        assert principal["role"] == "orchestrator"
        assert name == "read_file" and arguments == {"path": "harness:pending-work"}
        observations.append(principal["thread_id"])
        return {"pending": processes.pending(), "queued_setup": len(setup)}

    with serve_mcp(
        None, dispatch, verifier=verify, claim=registry.claim_attestation, channel="harness"
    ) as transport:
        context["stop_transport"] = transport
        yield context, processes, observations, setup


def test_attested_stop_blocks_running_and_uncollected_work_then_allows_exit(supervised_stop):
    context, processes, observations, _ = supervised_stop
    event = {"hook_event_name": "Stop", "session_id": context["session_id"]}
    record = process_record(context["session_id"])
    processes.records["process-discovery"] = record
    first = handle_hook(event, context)
    assert first["decision"] == "block" and "process-discovery" in first["reason"]
    record["status"] = "Exited"
    # A second stop still has to collect the completed command, even with the
    # native continuation flag set. Tool calls reset Claude's continuation cap.
    assert handle_hook(event | {"stop_hook_active": True}, context)["decision"] == "block"
    processes.poll(context["session_id"], "process-discovery")
    assert handle_hook(event, context) == {}
    assert observations == [context["session_id"]] * 3


def test_stop_waits_for_child_results_and_queued_setup_without_leaking_output(supervised_stop):
    context, processes, _, setup = supervised_stop
    event = {"hook_event_name": "Stop", "session_id": context["session_id"]}
    setup.append("fixture-unit")
    assert handle_hook(event, context)["decision"] == "block"
    setup.clear()
    processes.records["process-child"] = process_record("child", "Failed", "secret-output")
    blocked = handle_hook(event, context)
    assert blocked["decision"] == "block"
    assert "secret-output" not in json.dumps(blocked) and "process-child" not in blocked["reason"]
    processes.poll("child", "process-child")
    assert handle_hook(event, context) == {}


def test_stop_refuses_foreign_sessions_and_non_loopback_transport(supervised_stop):
    context, _, observations, _ = supervised_stop
    with pytest.raises(ValueError, match="another session"):
        handle_hook({"hook_event_name": "Stop", "session_id": "foreign"}, context)
    with pytest.raises(ValueError, match="only the supervised"):
        handle_hook(
            {"hook_event_name": "Stop", "session_id": context["session_id"], "agent_id": "child"},
            context,
        )
    context["stop_transport"] = {"url": "https://example.com/mcp", "token": "synthetic"}
    with pytest.raises(ValueError, match="loopback"):
        handle_hook({"hook_event_name": "Stop", "session_id": context["session_id"]}, context)
    assert not observations
