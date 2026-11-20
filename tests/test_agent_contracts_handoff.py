# ruff: noqa: F811
"""Public immutable lifecycle journey; legacy process uncertainty is retained, not qualified."""

import sys
from pathlib import Path

from harness import agent_contracts as C
from harness.agent_exchange import context, specification
from harness.agent_results import require_typed_result
from harness.execution_store import get_execution
from tests.test_execution_continuity import child, command, prepare, report, state
from tests.test_execution_operations import operations  # noqa: F401


def test_real_generated_assignment_submission_and_authored_record_preserve_legacy(operations):
    operation, supervisor, controller, _, _, execution, envelope = operations
    prepare(operation, supervisor)
    engineer = child(operation, "al-engineer")
    aid = engineer["thread_id"]
    report(
        operation,
        supervisor,
        "agent-register",
        {"id": aid, "name": "Observed Engineer", "role": "al-engineer", "task": "A"},
    )
    report(operation, supervisor, "agent-update", {"id": aid, "status": "Running"})
    command(operation, supervisor, "integration", "task-start", "--task", "A")
    command(
        operation,
        supervisor,
        "writer",
        "start",
        "--id",
        "typed-source",
        "--role",
        "al-engineer",
        "--files",
        "work",
        "--task",
        "A",
        "--agent",
        aid,
    )
    command(
        operation,
        supervisor,
        "evidence",
        "plan",
        "--area",
        "declared-source-check",
        "--kind",
        "check",
        "--tier",
        "focused",
        "--scope",
        ".",
        "--",
        sys.executable,
        "-c",
        "raise RuntimeError('presentation must not execute this check')",
    )
    current = state(operation)
    graph = current["integration"]["graph_revision"]
    body = operation.dispatch(
        supervisor,
        "write_artifact",
        {
            "name": "assignment.md",
            "content": "Implement actual scoped source; preserve verification debt.",
            "expected_graph_revision": graph,
        },
    )
    resource = "artifact:" + body["artifact_id"] + ":" + body["sha256"]
    command(
        operation, supervisor, "writer", "handoff", "--id", "typed-source", "--body-file", resource
    )
    assignment = operation.dispatch(engineer, "await_assignment", {"timeout": 1})["handoff"]
    assert assignment["status"] == "available"
    page = operation.dispatch(engineer, "read_file", {"path": assignment["resource"]})
    assert "actual scoped source" in page["text"] and page["coverage"]["mutable_source_not_checked"]
    before = {
        r["attempt_id"]
        for r in controller.conn.execute("SELECT attempt_id FROM policy_legacy_admissions")
    }
    assert before
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
    current = state(operation)
    actual = get_execution(controller.conn, execution["id"])
    content = "Authored exact scoped source; verification remains pending."
    declared = assignment["contract"]
    value = C.make(
        "implementation-result",
        context(actual, current, engineer, "A"),
        specification(current),
        "implementation-result",
        content,
        status="authored",
        inputs=declared["required_inputs"],
        criteria=declared["acceptance_criteria"],
        constraints=declared["constraints"],
    )
    output = operation.dispatch(
        engineer,
        "submit_output",
        {
            "name": "implementation.md",
            "content": content,
            "expected_graph_revision": graph,
            "contract": value,
        },
    )
    output_resource = "artifact:" + output["artifact_id"] + ":" + output["sha256"]
    command(
        operation, supervisor, "writer", "finish", "--id", "typed-source", "--outcome", "success"
    )
    report(
        operation,
        supervisor,
        "agent-update",
        {"id": aid, "status": "Completed", "latest_output": output_resource},
    )
    report(
        operation,
        supervisor,
        "task-authored",
        {
            "schema_version": 1,
            "task_id": "A",
            "output_refs": [{"kind": "source", "path": "work/result.py"}],
        },
        task="A",
    )
    final = state(operation)
    receipt = final["integration"]["tasks"]["A"]["authored_output"]
    assert receipt["producer_invocation_ids"] == [aid]
    assert receipt["writer_ids"] == ["typed-source"]
    assert final["integration"]["tasks"]["A"]["status"] != "Completed"
    require_typed_result(get_execution(controller.conn, execution["id"]), final, "A")
    assert operation.artifacts.read(output["artifact_id"], output["sha256"])["text"] == content
    after = {
        r["attempt_id"]
        for r in controller.conn.execute("SELECT attempt_id FROM policy_legacy_admissions")
    }
    assert before <= after
    assert (Path(envelope["repository"]) / "work/result.py").read_text() == "answer = 42\n"
    assert get_execution(controller.conn, execution["id"])["status"] != "Completed"
