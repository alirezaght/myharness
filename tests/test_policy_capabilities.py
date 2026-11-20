# ruff: noqa: F811
"""Protected immutable approval resources and exact one-use capability binding."""

import time

import pytest

from harness.execution_contract import canonical
from harness.policy import capabilities
from harness.policy.composition import select
from harness.policy.configuration import install
from tests.test_policy_admission import (  # noqa: F401
    attempt,
    configure,
    document,
    observed,
    patch,
    policy_runtime,
    processes,
)


def grant(r, *, uses=1, identifier="grant"):
    execution = __import__("harness.execution_store", fromlist=["get_execution"]).get_execution(
        r.controller.conn, r.operation.execution_id
    )
    _, pd = select(r.controller.conn, execution, r.principal)
    doc = {
        "id": identifier,
        "execution_id": r.operation.execution_id,
        "epoch_id": "legacy-0",
        "generation": 0,
        "principal_id": r.principal["thread_id"],
        "role": r.principal["role"],
        "operation": "apply_patch",
        "resources": ["file:svc/new.txt"],
        "binding": canonical(execution["binding"]),
        "policy_digest": pd,
        "maximum_uses": uses,
        "maximum_units": uses,
        "expires_at": int(time.time()) + 300,
    }
    did = r.authorize(doc, "capability")
    capabilities.issue(r.controller, r.operation.execution_id, did, "legacy-0", 0)
    return doc


def test_exact_protected_grant_consumes_once_and_never_refunds(policy_runtime):
    r = policy_runtime
    configure(r, document(r, maximum=5, rules={"required_approvals": ["grant"]}))
    grant(r)
    a = attempt(r)
    a.finish()
    assert r.controller.conn.execute("SELECT uses FROM policy_capabilities").fetchone()[0] == 1
    with pytest.raises(ValueError, match="CAPABILITY_INVALID"):
        attempt(r)
    assert r.controller.conn.execute("SELECT reserved FROM policy_pools").fetchone()[0] == 0
    denied = r.controller.conn.execute(
        "SELECT verdict,code FROM policy_decisions ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert denied[:] == ("deny", "CAPABILITY_INVALID")


def test_revocation_between_admission_and_start_blocks_effect_without_refund(policy_runtime):
    r = policy_runtime
    configure(r, document(r, maximum=5, rules={"required_approvals": ["grant"]}))
    grant(r)
    a = attempt(r)
    did = r.authorize({"capability_id": "grant", "revocation_generation": 0}, "revoke")
    revoked = capabilities.revoke(r.controller, r.operation.execution_id, did, 0, "legacy-0", 0)
    actions = [
        dict(row)
        for row in r.controller.conn.execute(
            "SELECT * FROM policy_capability_actions ORDER BY rowid"
        )
    ]
    assert [row["action"] for row in actions] == ["issued", "revoked"]
    assert actions[-1]["decision_ref"] == did and actions[-1]["action_id"] == revoked["action_ref"]
    assert (
        capabilities.revoke(r.controller, r.operation.execution_id, did, 0, "legacy-0", 0)
        == revoked
    )
    assert (
        r.controller.conn.execute("SELECT COUNT(*) FROM policy_capability_actions").fetchone()[0]
        == 2
    )
    assert (
        r.controller.conn.execute(
            "SELECT source_id FROM reliability_timeline_links "
            "WHERE event_kind='policy.capability_revoked'"
        ).fetchone()[0]
        == revoked["action_ref"]
    )
    with pytest.raises(ValueError, match="CAPABILITY_INVALID"):
        a.start()
    a.finish()
    assert r.controller.conn.execute("SELECT uses,state FROM policy_capabilities").fetchone()[
        :
    ] == (1, "revoked")


def test_positive_prose_and_unacknowledged_delivery_cannot_authorize_configuration(policy_runtime):
    r = policy_runtime
    did = r.authorize(document(r))
    r.state["integration"]["deliveries"].clear()
    r.save()
    with pytest.raises(ValueError, match="APPROVAL_REQUIRED"):
        install(r.controller, r.operation.execution_id, did, 0, "legacy-0", 0)
    assert r.controller.conn.execute("SELECT COUNT(*) FROM policy_versions").fetchone()[0] == 0


def test_two_contenders_cannot_spend_last_capability_use(policy_runtime):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import closing
    from types import SimpleNamespace

    from harness.controller import HarnessController
    from harness.db import connect

    r = policy_runtime
    configure(r, document(r, maximum=10, rules={"required_approvals": ["grant"]}))
    grant(r)

    def compete(_):
        with closing(connect(r.operation.database)) as conn:
            local = SimpleNamespace(**r.__dict__)
            local.controller = HarnessController(conn)
            try:
                return attempt(local)
            except ValueError as error:
                return str(error)

    with ThreadPoolExecutor(2) as executor:
        result = list(executor.map(compete, range(2)))
    assert sum(not isinstance(v, str) for v in result) == 1
    assert "CAPABILITY_INVALID" in result
    assert r.controller.conn.execute("SELECT uses FROM policy_capabilities").fetchone()[0] == 1
    assert r.controller.conn.execute("SELECT reserved FROM policy_pools").fetchone()[0] == 1
