"""Public guarded AL and native operations exercise continuity without product state."""

import json
import uuid
from pathlib import Path

import pytest

from harness.execution_contract import digest
from tests.test_execution_operations import operations  # noqa: F401


def state(operation):
    return json.loads(Path(operation.initialization["source"]).read_bytes())


def command(operation, principal, *args, success=True):
    result = operation.dispatch(principal, "al_command", {"arguments": list(args)})
    assert (result["returncode"] == 0) == success, result
    return result


def report(operation, principal, action, value, *, task=None, success=True):
    current = state(operation)
    artifact = operation.dispatch(
        principal,
        "write_artifact",
        {
            "name": str(uuid.uuid4()) + ".json",
            "content": json.dumps(value),
            "expected_graph_revision": current["integration"]["graph_revision"],
        },
    )
    reference = "artifact:" + artifact["artifact_id"] + ":" + artifact["sha256"]
    args = ["integration", action, "--file", reference]
    if task:
        args += ["--task", task]
    return command(operation, principal, *args, success=success)


def child(operation, role):
    directory = operation.registry.native_root / "sessions/2026/10/29"
    directory.mkdir(parents=True, exist_ok=True)
    aid = str(uuid.uuid4())
    for thread, source in [
        (operation.registry.root, "exec"),
        (
            aid,
            {
                "subagent": {
                    "thread_spawn": {
                        "parent_thread_id": operation.registry.root,
                        "agent_role": role,
                    },
                }
            },
        ),
    ]:
        payload = {"id": thread, "session_id": thread, "cli_version": "0.164.0", "source": source}
        (directory / ("rollout-" + thread + ".jsonl")).write_text(
            json.dumps({"type": "session_meta", "payload": payload}) + "\n"
        )
    return operation.registry.enroll(aid)


def prepare(operation, principal):
    command(operation, principal, "spec-gate", "SPEC_READY")
    command(
        operation,
        principal,
        "classify",
        "STANDARD",
        "--designer",
        "no",
        "--implementers",
        "al-engineer",
    )
    for tid, files, dependencies in [("A", ["work"], []), ("B", ["consumer"], ["A"])]:
        report(
            operation,
            principal,
            "task-register",
            {
                "id": tid,
                "title": tid,
                "files": files,
                "criteria": ["AC-01"],
                "dependencies": dependencies,
            },
        )
    command(operation, principal, "phase", "IMPLEMENTATION")


def test_public_scoped_write_while_proof_pending(operations):  # noqa: F811
    operation, principal, controller, _, _, execution, envelope = operations
    prepare(operation, principal)
    engineer = child(operation, "al-engineer")
    aid = engineer["thread_id"]
    report(
        operation,
        principal,
        "agent-register",
        {
            "id": aid,
            "name": "Observed fixture engineer",
            "role": "al-engineer",
            "task": "A",
        },
    )
    report(operation, principal, "agent-update", {"id": aid, "status": "Running"})
    command(operation, principal, "integration", "task-start", "--task", "A")
    command(
        operation,
        principal,
        "writer",
        "start",
        "--id",
        "source-writer",
        "--role",
        "al-engineer",
        "--files",
        "work",
        "--task",
        "A",
        "--agent",
        aid,
    )
    revision = operation.assignments[aid]["revision"]
    operation.dispatch(
        engineer,
        "apply_patch",
        {
            "patch": "*** Begin Patch\n*** Add File: work/result.py\n+answer = 42\n*** End Patch\n",
            "task_id": "A",
            "expected_revision": revision,
        },
    )
    assert (Path(envelope["repository"]) / "work/result.py").read_text() == "answer = 42\n"
    command(
        operation, principal, "writer", "finish", "--id", "source-writer", "--outcome", "success"
    )
    report(operation, principal, "agent-update", {"id": aid, "status": "Completed"})
    report(
        operation,
        principal,
        "task-authored",
        {
            "schema_version": 1,
            "task_id": "A",
            "output_refs": [{"kind": "source", "path": "work/result.py"}],
        },
        task="A",
    )
    command(operation, principal, "integration", "task-admit", "--task", "B")
    saved = state(operation)
    assert saved["integration"]["tasks"]["A"]["status"] != "Completed"
    assert saved["implementation_readiness"]["obligations"]
    from harness.execution_projection import execution_view
    from harness.execution_worker import replay_source

    replay_source(controller.conn, execution["id"], "test-owner")
    view = execution_view(controller.conn, execution["id"])
    assert view["authored_work"]["kind"] == "authored_work_recorded_verification_pending"
    assert view["authored_work"]["source_file_count"] == 1 and view["status"] != "Completed"
    assert view["verification"]["status"] == "incomplete"
    command(operation, principal, "integration", "task-finish", "--task", "A", success=False)
    command(operation, principal, "approved", success=False)
    from harness.execution_authority import require_dependency_outputs
    from harness.execution_models import ExecutionConflict

    (Path(envelope["repository"]) / "work/result.py").write_text("external drift\n")
    with pytest.raises(ExecutionConflict, match="bytes changed"):
        require_dependency_outputs(saved, "B", envelope["repository"])
    with pytest.raises(ValueError):
        operation.dispatch(
            engineer,
            "apply_patch",
            {
                "patch": "*** Begin Patch\n*** Add File: other.py\n+bad\n*** End Patch\n",
                "task_id": "A",
                "expected_revision": revision,
            },
        )


def test_readonly_observed_report_has_authored_milestone_without_writer(operations):  # noqa: F811
    operation, principal, controller, _, _, execution, envelope = operations
    prepare(operation, principal)
    architect = child(operation, "al-architect")
    aid = architect["thread_id"]
    report(
        operation,
        principal,
        "agent-register",
        {
            "id": aid,
            "name": "Observed fixture Architect",
            "role": "al-architect",
            "task": "A",
        },
    )
    report(operation, principal, "agent-update", {"id": aid, "status": "Running"})
    output = operation.dispatch(
        architect,
        "submit_output",
        {
            "name": "architecture.md",
            "content": "Concrete synthetic architecture output.",
            "expected_graph_revision": state(operation)["integration"]["graph_revision"],
        },
    )
    resource = "artifact:" + output["artifact_id"] + ":" + output["sha256"]
    report(
        operation,
        principal,
        "agent-update",
        {
            "id": aid,
            "status": "Completed",
            "latest_output": resource,
        },
    )
    report(
        operation,
        principal,
        "task-authored",
        {
            "schema_version": 1,
            "task_id": "A",
            "output_refs": [{"kind": "report", "resource": resource, "producer": aid}],
        },
        task="A",
    )
    current = state(operation)
    receipt = current["integration"]["tasks"]["A"]["authored_output"]
    assert receipt["writer_ids"] == [] and receipt["producer_invocation_ids"] == [aid]
    from harness.execution_projection import execution_view
    from harness.execution_worker import replay_source

    replay_source(controller.conn, execution["id"], "test-owner")
    assert execution_view(controller.conn, execution["id"])["authored_work"] is None
    assert current["integration"]["tasks"]["A"]["status"] != "Completed"
    command(operation, principal, "integration", "task-admit", "--task", "B")
    from harness.execution_authority import require_dependency_outputs

    require_dependency_outputs(state(operation), "B", envelope["repository"], execution)
    report(
        operation,
        principal,
        "task-authored",
        {
            "schema_version": 1,
            "task_id": "A",
            "output_refs": [
                {
                    "kind": "report",
                    "resource": resource.replace(output["sha256"], digest(b"foreign")),
                    "producer": aid,
                }
            ],
        },
        task="A",
        success=False,
    )
