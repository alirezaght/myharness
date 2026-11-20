# ruff: noqa: F811
"""Real readonly HTTP scope and fixed operator errors; request secrets never enter facts."""

import hashlib
import json
from urllib.parse import urlencode

import pytest

from harness.dashboard.policy_actions import policy_action
from harness.dashboard.policy_view import read_policy
from tests.test_dashboard import fetch, serve
from tests.test_policy_admission import (  # noqa: F401
    configure,
    document,
    observed,
    patch,
    policy_runtime,
    processes,
)


def query(r, **extra):
    return urlencode(
        {
            "task": r.operation.envelope["task_id"],
            "execution": r.operation.execution_id,
            "epoch": "legacy-0",
            **extra,
        }
    )


def test_actual_http_budget_and_safe_decision_are_read_only(policy_runtime):
    r = policy_runtime
    configure(r)
    r.operation.dispatch(r.principal, "apply_patch", patch(r))
    secret = "Authorization: Bearer policy-private-5619"
    with pytest.raises(ValueError):
        r.operation.dispatch(r.principal, "apply_patch", patch(r, "svc/" + secret))
    before = r.controller.conn.total_changes
    with serve(r.operation.database, None) as server:
        status, _, body = fetch(server, "/api/execution/policy?" + query(r))
        assert status == 200
        value = json.loads(body)
        assert value["pools"][0]["consumed"] == 1 and value["pools"][0]["reserved"] == 0
        assert value["limitations"]["tokens"] is None
        assert (
            secret not in body.decode()
            and hashlib.sha256(secret.encode()).hexdigest() not in body.decode()
        )
        assert fetch(server, "/api/execution/policy?" + query(r, task="foreign"))[0] == 404
    assert before == r.controller.conn.total_changes


def test_operator_cannot_supply_approved_flag_or_foreign_revision(policy_runtime):
    r = policy_runtime
    assert (
        policy_action(r.operation.database, json.dumps({"approved": True}).encode(), "install")[0]
        == 409
    )
    did = r.authorize(document(r))
    body = {
        "task": r.operation.envelope["task_id"],
        "execution": r.operation.execution_id,
        "epoch": "legacy-0",
        "generation": 0,
        "decision": did,
        "revision": 0,
    }
    assert policy_action(r.operation.database, json.dumps(body).encode(), "install")[0] == 200
    assert policy_action(r.operation.database, json.dumps(body).encode(), "install")[0] == 200
    assert read_policy(r.operation.database, query(r) + "&unknown=secret")[0] == 400


def test_policy_header_guards_refuse_before_reading_request_body(policy_runtime):
    import http.client

    r = policy_runtime
    with serve(r.operation.database, None) as server:
        host = f"127.0.0.1:{server.port}"
        for length, origin, expected in [
            (4097, "http://" + host, 413),
            (10, "https://foreign.invalid", 403),
        ]:
            connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
            try:
                connection.putrequest("POST", "/api/policy/install")
                connection.putheader("Origin", origin)
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Content-Length", str(length))
                connection.endheaders()
                response = connection.getresponse()
                assert response.status == expected
                response.read()
            finally:
                connection.close()


def test_more_than_one_hundred_actual_capabilities_are_all_navigable_read_only(policy_runtime):
    from tests.test_policy_capabilities import grant

    r = policy_runtime
    configure(r)
    for index in range(105):
        grant(r, identifier=f"cap-{index:03d}")
    seen = set()
    cursor = None
    pages = 0
    before = r.controller.conn.total_changes
    with serve(r.operation.database, None) as server:
        while True:
            suffix = {"cap_after": cursor} if cursor else {}
            status, _, body = fetch(server, "/api/execution/policy?" + query(r, **suffix))
            assert status == 200
            value = json.loads(body)
            items = value["capabilities"]
            ids = {row["id"] for row in items}
            assert len(items) <= 25 and not seen & ids
            assert all(row["issuance_decision_ref"] for row in items)
            assert value["capabilities_coverage"]["status"] == "partial"
            seen |= ids
            pages += 1
            assert value["capabilities_has_more"] == bool(value["capabilities_next_cursor"])
            cursor = value["capabilities_next_cursor"]
            if not cursor:
                break
        assert (
            fetch(server, "/api/execution/policy?" + query(r, task="foreign", cap_after="cap-050"))[
                0
            ]
            == 404
        )
    assert pages == 5 and len(seen) == 105 and r.controller.conn.total_changes == before
