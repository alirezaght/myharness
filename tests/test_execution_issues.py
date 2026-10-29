"""Operational repair events remain distinct from developer decision blockers."""

import json

import pytest

from harness.execution_projection import execution_view, ingest_event
from harness.execution_worker import replay_source
from tests.execution_helpers import approved_execution, event, started
from tests.test_execution_continuity import command, prepare, report, state
from tests.test_execution_operations import operations  # noqa: F401


def test_public_issue_reporting_preserves_independent_admission(operations):  # noqa: F811
    operation, principal, controller, _, _, execution, _ = operations
    prepare(operation, principal)
    artifact = operation.dispatch(
        principal,
        "write_artifact",
        {
            "name": "diagnostic.md",
            "content": "Synthetic provider is temporarily unavailable.",
            "expected_graph_revision": state(operation)["integration"]["graph_revision"],
        },
    )
    resource = "artifact:" + artifact["artifact_id"] + ":" + artifact["sha256"]
    result = command(
        operation,
        principal,
        "artifact",
        "record",
        "--file",
        resource,
        "--kind",
        "operational-diagnostic",
    )
    ref = json.loads(result["stdout"])
    issue = {
        "id": "provider",
        "revision": 1,
        "cause": "provider_unavailable",
        "task_ids": ["A"],
        "paths": ["work"],
        "capability_or_check_ref": "provider-health",
        "diagnostic_ref": ref,
        "state": "Repairing",
        "next_action": "Retry configured provider health check",
        "observed_binding": state(operation)["integration"]["binding"],
    }
    report(operation, principal, "issue-update", issue)
    command(operation, principal, "integration", "task-admit", "--task", "A")
    replay_source(controller.conn, execution["id"], "test-owner")
    view = execution_view(controller.conn, execution["id"])
    assert view["blockers"] == []
    assert view["operational_issues"][0]["cause"] == "provider_unavailable"
    assert view["operational_issues"][0]["state"] == "Repairing"
    report(
        operation,
        principal,
        "issue-update",
        issue | {"revision": 2, "cause": "invented"},
        success=False,
    )
    assert state(operation)["operational_issues"]["provider"] == issue


def test_contract_rejects_foreign_issue_event_before_projection(
    controller, sample_repo, monkeypatch
):
    task, eid = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, eid)
    issue = {
        "id": "tool",
        "revision": 1,
        "cause": "tool_unavailable",
        "task_ids": [],
        "paths": [],
        "capability_or_check_ref": "tool-health",
        "diagnostic_ref": {"sha256": "0" * 64},
        "state": "Open",
        "next_action": "Repair tool",
        "observed_binding": event(controller.conn, eid, 2, "issue.updated", {})["binding"]
        | {
            "al_run_id": "foreign",
        },
    }
    with pytest.raises(ValueError, match="foreign issue"):
        ingest_event(controller.conn, eid, event(controller.conn, eid, 2, "issue.updated", issue))
    assert execution_view(controller.conn, eid)["operational_issues"] == []
