"""Real operation/policy producers; both AL suites exercise accepted immutable sources."""

# ruff: noqa: F811
import json
from pathlib import Path

import pytest

from harness.execution_store import get_execution
from harness.reliability import catalog, investigations, source_queries
from tests.test_agent_contracts import output_contract
from tests.test_execution_operations import generated_handoff_fixture, operations  # noqa: F401
from tests.test_policy_admission import configure, observed, patch, policy_runtime, processes  # noqa: F401


def test_real_typed_dispatch_rejection_is_separate_atomic_source_and_safe_metadata(operations):
    operation, root, controller, initialization, _, execution, _ = operations
    child, _ = generated_handoff_fixture(operations)
    assignment = operation.dispatch(child, "await_assignment", {"timeout": 1})["handoff"]
    state = json.loads(Path(initialization["source"]).read_bytes())
    execution = get_execution(controller.conn, execution["id"])
    value = output_contract(operation, child, execution, state, assignment, "safe result")
    value["status"] = "SECRET_SENTINEL"
    with pytest.raises(ValueError):
        operation.dispatch(
            child,
            "submit_output",
            {
                "name": "typed.md",
                "content": "safe result",
                "expected_graph_revision": 1,
                "contract": value,
            },
        )
    row = controller.conn.execute("SELECT * FROM contract_rejection_receipts").fetchone()
    assert row["boundary"] == "dispatch" and row["contract_family"] == "implementation-result"
    assert row["observation_id"] is not None
    receipts = [
        tuple(r) for r in controller.conn.execute("SELECT * FROM contract_rejection_receipts")
    ]
    assert "SECRET_SENTINEL" not in str(receipts)
    assert (
        controller.conn.execute("SELECT count(*) FROM incident_contract_sources").fetchone()[0] == 1
    )
    assert not controller.conn.execute("PRAGMA foreign_key_check").fetchall()
    with pytest.raises(ValueError):
        operation.dispatch(root, "read_file", {"path": "../private"})
    assert (
        controller.conn.execute("SELECT count(*) FROM incident_contract_sources").fetchone()[0] == 1
    )

    generic = controller.conn.execute(
        "SELECT * FROM reliability_incident_catalog WHERE symptom_code='DISPATCH_REJECTED' "
        "ORDER BY created_at DESC,incident_id DESC LIMIT 1"
    ).fetchone()
    scoped = {
        "task": generic["task_id"],
        "execution": generic["execution_id"],
        "epoch": generic["epoch_id"],
    }
    head = investigations.current(controller.conn, scoped, generic["incident_id"])
    assert head["document"]["controls"][0]["state"] == "unknown"


def test_real_budget_exhaustion_has_actual_policy_relation_null_attempt(policy_runtime):
    runtime = policy_runtime
    configure(runtime)
    runtime.operation.dispatch(runtime.principal, "apply_patch", patch(runtime))
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        runtime.operation.dispatch(
            runtime.principal, "apply_patch", patch(runtime, "svc/again.txt")
        )
    conn = runtime.controller.conn
    row = conn.execute(
        "SELECT d.* FROM incident_policy_sources s JOIN policy_decisions "
        "d ON d.id=s.policy_decision_id"
    ).fetchone()
    assert row["code"] == "BUDGET_EXHAUSTED" and row["attempt_id"] is None
    selected = {
        "task": runtime.operation.envelope["task_id"],
        "execution": runtime.operation.execution_id,
        "epoch": "legacy-0",
    }
    item = next(
        i
        for i in catalog.list_incidents(conn, selected)["items"]
        if i["source_kind"] == "budget_decision"
    )
    assert (
        source_queries.source_relation(conn, selected, item["incident_id"])["reference"][
            "policy_decision_id"
        ]
        == row["id"]
    )
    assert "pool" not in item and "dimension" not in item


def test_real_assignment_validator_rejection_has_receipt_without_duplicate_dispatch(operations):
    operation, _, controller, initialization, _, _, _ = operations
    child, _ = generated_handoff_fixture(operations)
    path = Path(initialization["source"])
    state = json.loads(path.read_bytes())
    state["writers"]["handoff-writer"]["handoffs"][-1]["contract"]["status"] = (
        "invalid-negative-fixture"
    )
    path.write_text(json.dumps(state))
    before = controller.conn.execute("SELECT count(*) FROM reliability_observations").fetchone()[0]
    result = operation.dispatch(child, "await_assignment", {"timeout": 1})
    assert result["handoff"]["status"] == "unavailable" and "contract_error" in result["handoff"]
    row = controller.conn.execute(
        "SELECT * FROM contract_rejection_receipts WHERE boundary='assignment_wait'"
    ).fetchone()
    assert row is not None and row["observation_id"] is None
    assert (
        controller.conn.execute("SELECT count(*) FROM reliability_observations").fetchone()[0]
        == before
    )
    assert "invalid-negative-fixture" not in str(tuple(row))
