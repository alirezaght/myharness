# ruff: noqa: F811
"""All scopes intersect, pool replacements retain commitments and malformed rules refuse."""

import pytest

from harness.policy.contracts import policy_document
from tests.test_policy_admission import (  # noqa: F401
    attempt,
    configure,
    document,
    observed,
    patch,
    policy_runtime,
    processes,
)


def test_explicit_deny_in_global_scope_wins_execution_allow(policy_runtime):
    r = policy_runtime
    configure(r, document(r, maximum=5, rules={"allow_tools": ["apply_patch"]}))
    broad = document(r, maximum=5, policy="global", rules={"deny_tools": ["apply_patch"]})
    broad.update(scope_kind="global", scope_ref="all")
    configure(r, broad)
    with pytest.raises(ValueError, match="POLICY_DENIED"):
        r.operation.dispatch(r.principal, "apply_patch", patch(r))
    assert all(
        row[0] == 0 for row in r.controller.conn.execute("SELECT consumed FROM policy_pools")
    )


def test_limit_replacement_keeps_consumption_and_old_reservation_cannot_start(policy_runtime):
    r = policy_runtime
    configure(r, document(r, maximum=3))
    reserved = attempt(r)
    r.operation.dispatch(r.principal, "apply_patch", patch(r, "svc/consumed.txt"))
    configure(r, document(r, maximum=0, version=2), revision=1)
    pool = r.controller.conn.execute("SELECT * FROM policy_pools").fetchone()
    assert (pool["consumed"], pool["reserved"], pool["maximum"]) == (1, 1, 0)
    with pytest.raises(ValueError, match="AUTHORITY_STALE"):
        reserved.start()
    reserved.finish()
    assert r.controller.conn.execute("SELECT consumed,reserved FROM policy_pools").fetchone()[
        :
    ] == (1, 0)


@pytest.mark.parametrize(
    "change",
    [
        {"version": 0},
        {"scope_kind": "machine"},
        {"rules": {"expression": "True"}},
        {
            "limits": [
                {
                    "pool_id": "p",
                    "dimension": "mutating_operations",
                    "domain": "machine",
                    "owner_ref": "all",
                    "maximum": 1,
                }
            ]
        },
    ],
)
def test_closed_grammar_refuses_unsupported_fields(policy_runtime, change):
    with pytest.raises(ValueError):
        policy_document(document(policy_runtime) | change)


def test_workflow_membership_is_approved_immutable_and_uses_same_pool(policy_runtime):
    from harness.policy.configuration import register_workflow

    r = policy_runtime
    did = r.authorize(
        {"workflow_id": "registered-flow", "execution_id": r.operation.execution_id}, "workflow"
    )
    register_workflow(r.controller, r.operation.execution_id, did, "legacy-0", 0)
    doc = document(r)
    doc.update(scope_kind="workflow", scope_ref="registered-flow")
    doc["limits"][0].update(domain="workflow_instance", owner_ref="registered-flow")
    configure(r, doc)
    r.operation.dispatch(r.principal, "apply_patch", patch(r))
    assert r.controller.conn.execute("SELECT domain,consumed FROM policy_pools").fetchone()[:] == (
        "workflow_instance",
        1,
    )


def test_parent_role_restrictions_are_inherited_by_child_writer(policy_runtime):
    r = policy_runtime
    doc = document(r, rules={"deny_tools": ["apply_patch"]})
    doc.update(scope_kind="role", scope_ref="orchestrator")
    configure(r, doc)
    with pytest.raises(ValueError, match="POLICY_DENIED"):
        r.operation.dispatch(r.principal, "apply_patch", patch(r))


def test_historical_parent_bound_is_not_widened_by_shared_pool_replacement(policy_runtime):
    from pathlib import Path

    r = policy_runtime
    configure(r, document(r, policy="parent", maximum=1))
    child = document(r, policy="child", maximum=20)
    child["parents"] = ["parent-v1"]
    configure(r, child)
    r.operation.dispatch(r.principal, "apply_patch", patch(r, "svc/first.txt"))
    configure(r, document(r, policy="parent", maximum=10, version=2), revision=1)
    assert (
        r.controller.conn.execute(
            "SELECT consumed FROM policy_pools WHERE id='parent-pool'"
        ).fetchone()[0]
        == 1
    )
    rejected = None
    try:
        r.operation.dispatch(r.principal, "apply_patch", patch(r, "svc/forbidden-second.txt"))
    except ValueError as error:
        rejected = str(error)
    consumed = r.controller.conn.execute(
        "SELECT consumed FROM policy_pools WHERE id='parent-pool'"
    ).fetchone()[0]
    exists = (Path(r.operation.envelope["repository"]) / "svc/forbidden-second.txt").exists()
    assert rejected == "BUDGET_EXHAUSTED", (
        "R3-FRESH-001: unchanged historical parent allowed second mutation; "
        f"consumed={consumed}, target_exists={exists}"
    )
    assert consumed == 1 and not exists


def test_v2_only_consumer_can_use_increased_pool_without_reset(policy_runtime):
    from harness.execution_store import get_execution
    from harness.policy.composition import select

    r = policy_runtime
    configure(r, document(r, policy="parent", maximum=1))
    r.operation.dispatch(r.principal, "apply_patch", patch(r, "svc/first.txt"))
    configure(r, document(r, policy="parent", maximum=10, version=2), revision=1)
    docs, _ = select(
        r.controller.conn, get_execution(r.controller.conn, r.operation.execution_id), r.principal
    )
    assert [(d["policy_id"], d["version"]) for d in docs] == [("parent", 2)]
    assert (
        r.controller.conn.execute(
            "SELECT consumed FROM policy_pools WHERE id='parent-pool'"
        ).fetchone()[0]
        == 1
    )
    assert r.operation.dispatch(r.principal, "apply_patch", patch(r, "svc/second.txt"))[
        "changed"
    ] == ["svc/second.txt"]
    assert r.controller.conn.execute(
        "SELECT consumed,reserved FROM policy_pools WHERE id='parent-pool'"
    ).fetchone()[:] == (2, 0)
