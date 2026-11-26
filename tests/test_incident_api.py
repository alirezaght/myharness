"""Actual local operator HTTP journeys; Host/Origin/body limits precede body reads."""

# ruff: noqa: F811
import http.client
import json
from pathlib import Path
from urllib.parse import urlencode

from harness.execution_store import get_execution
from harness.reliability import store
from tests.database_snapshot import unchanged_database
from tests.test_agent_contracts import output_contract
from tests.test_dashboard import fetch, serve
from tests.test_execution_operations import generated_handoff_fixture, operations  # noqa: F401
from tests.test_reliability_store import observed  # noqa: F401


def selected(observed):
    operation, _, controller = observed
    return {
        "task": get_execution(controller.conn, operation.execution_id)["task_id"],
        "execution": operation.execution_id,
        "epoch": operation.epoch_id,
    }


def post(server, action, value):
    body = json.dumps(value).encode()
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    try:
        conn.request(
            "POST",
            "/api/reliability/" + action,
            body,
            headers={
                "Origin": f"http://127.0.0.1:{server.port}",
                "Content-Type": "application/json",
            },
        )
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def test_actual_failure_read_correction_resolution_replay_and_readonly_get(observed):
    operation, principal, controller = observed
    for _ in range(3):
        oid = store.begin(operation, principal, "read_file")
        store.finish(operation, oid, "AUTHORITY_REJECTED")
    scope = selected(observed)
    iid = controller.conn.execute(
        "SELECT incident_id FROM reliability_incidents LIMIT 1"
    ).fetchone()[0]
    original = [tuple(r) for r in controller.conn.execute("SELECT * FROM reliability_observations")]
    with serve(operation.database, None) as server:
        status, _, raw = fetch(
            server, "/api/reliability/incidents?" + urlencode({"task": scope["task"], "limit": 2})
        )
        page = json.loads(raw)
        assert status == 200 and len(page["items"]) == 2 and page["next_cursor"]
        detail_url = "/api/reliability/detail?" + urlencode(
            {"task": scope["task"], "incident": iid}
        )
        with unchanged_database(operation.database):
            assert fetch(server, detail_url)[0] == 200
        body = {
            "schema_version": 1,
            "scope": {"task": scope["task"]},
            "incident": iid,
            "expected_revision": 0,
            "request_id": "operator-resolution",
            "changes": {
                "status": "resolved",
                "rationale": {
                    "code": "evidence_sufficient",
                    "evidence": [iid],
                    "operator_explanation": (
                        "The rejected request demonstrates that the configured "
                        "authority control held."
                    ),
                },
            },
        }
        first = post(server, "investigate", body)
        assert first[0] == 200
        assert post(server, "investigate", body) == first
        assert post(server, "investigate", body | {"changes": {"status": "open"}})[0] == 409
        status, _, raw = fetch(server, detail_url)
        assert (
            status == 200 and json.loads(raw)["investigation"]["document"]["status"] == "resolved"
        )
        suggestion = post(
            server,
            "suggest",
            {"schema_version": 1, "scope": {"task": scope["task"]}, "limit": 100, "cursor": None},
        )
        assert suggestion[0] == 200 and len(suggestion[1]["result"]["items"]) == 1
        gid = suggestion[1]["result"]["items"][0]["group_id"]
        decision = {
            "schema_version": 1,
            "scope": {"task": scope["task"]},
            "group": gid,
            "expected_revision": 0,
            "request_id": "human-group-reject",
            "decision": {
                "kind": "reject",
                "rationale": {
                    "code": "unrelated_symptoms",
                    "evidence": [iid],
                    "operator_explanation": (
                        "The same visible denial code came from independent requests."
                    ),
                },
            },
        }
        assert post(server, "group-decision", decision)[0] == 200
        assert (
            fetch(
                server, "/api/reliability/group?" + urlencode({"task": scope["task"], "group": gid})
            )[0]
            == 200
        )
    assert [
        tuple(r) for r in controller.conn.execute("SELECT * FROM reliability_observations")
    ] == original


def test_reliability_header_refusal_happens_without_body_and_fixed_scope_errors(observed):
    operation, _, _ = observed
    with serve(operation.database, None) as server:
        host = f"127.0.0.1:{server.port}"
        for length, origin, expected in [
            (32769, "http://" + host, 413),
            (10, "https://foreign.invalid", 403),
        ]:
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
            try:
                conn.putrequest("POST", "/api/reliability/investigate")
                conn.putheader("Origin", origin)
                conn.putheader("Content-Type", "application/json")
                conn.putheader("Content-Length", str(length))
                conn.endheaders()
                response = conn.getresponse()
                assert response.status == expected
                response.read()
            finally:
                conn.close()
        assert fetch(server, "/api/reliability/incidents?task=foreign")[0] == 404
        assert (
            fetch(server, "/api/reliability/incidents?task=foreign", host="foreign.invalid")[0]
            == 403
        )
        assert post(server, "investigate", {"approved": True})[0] == 409


def test_real_published_output_operator_rejection_is_historical_and_idempotent(operations):
    operation, _, controller, initialization, _, execution, _ = operations
    child, _ = generated_handoff_fixture(operations)
    assignment = operation.dispatch(child, "await_assignment", {"timeout": 1})["handoff"]
    state = json.loads(Path(initialization["source"]).read_bytes())
    execution = get_execution(controller.conn, execution["id"])
    content = "Actual authored result for local operator assessment."
    contract = output_contract(operation, child, execution, state, assignment, content)
    output = operation.dispatch(
        child,
        "submit_output",
        {
            "name": "result.md",
            "content": content,
            "expected_graph_revision": 1,
            "contract": contract,
        },
    )
    scope = {
        "task": execution["task_id"],
        "execution": execution["id"],
        "epoch": execution["epoch_id"],
    }
    controller.conn.execute(
        "UPDATE executions SET status='Completed' WHERE id=?", (execution["id"],)
    )
    body = {
        "schema_version": 1,
        "scope": scope,
        "request_id": "operator-deliverable-reject",
        "target": {k: output[k] for k in ("artifact_id", "sha256")},
        "reason": {
            "code": "evidence_updated",
            "evidence": [],
            "operator_explanation": (
                "The authored deliverable needs a clearer account of its unresolved validation."
            ),
        },
        "evidence": [],
    }
    with serve(operation.database, None) as server:
        status, _, raw = fetch(server, "/api/reliability/outputs?" + urlencode(scope))
        descriptors = json.loads(raw)
        assert (
            status == 200
            and descriptors["source_available"]
            and not descriptors["content_revalidated"]
        )
        chosen = next(r for r in descriptors["items"] if r["artifact_id"] == output["artifact_id"])
        body["target"] = {k: chosen[k] for k in ("artifact_id", "sha256")}
        first = post(server, "verdict", body)
        assert first[0] == 200, first
        assert post(server, "verdict", body) == first
        incident = first[1]["result"]["incident_id"]
        status, _, raw = fetch(
            server,
            "/api/reliability/detail?"
            + urlencode({"task": execution["task_id"], "incident": incident}),
        )
        value = json.loads(raw)
        assert status == 200 and value["item"]["provenance"] == "operator_reported"
        assert value["source"]["reference"]["verdict_revision"] == 1
        directory = operation.artifacts.directory
        retained = directory.with_name("retained-drafts-negative-fixture")
        directory.rename(retained)
        try:
            with unchanged_database(operation.database):
                assert (
                    fetch(
                        server,
                        "/api/reliability/detail?"
                        + urlencode({"task": execution["task_id"], "incident": incident}),
                    )[0]
                    == 200
                )
                status, _, raw = fetch(server, "/api/reliability/outputs?" + urlencode(scope))
            missing = json.loads(raw)
            assert status == 200 and not missing["source_available"] and missing["items"] == []
            assert not directory.exists()
        finally:
            retained.rename(directory)
    assert get_execution(controller.conn, execution["id"])["status"] == "Completed"


def test_task_history_groups_distinct_real_approval_reservations_without_identity_rewrite(
    observed, tmp_path
):
    import threading
    import uuid

    from harness.approval import ApprovalGate
    from harness.execution import start_execution
    from harness.execution_handoff import read_handoff
    from harness.execution_operations import OperationController
    from harness.execution_principals import PrincipalRegistry
    from harness.spec_store import SpecStore
    from tests.test_execution_operations import observed_principal

    first, principal, controller = observed
    oid = store.begin(first, principal, "read_file")
    store.finish(first, oid, "AUTHORITY_REJECTED")
    scope = selected(observed)
    repo = Path(first.envelope["repository"])
    # Real public approval producer issues a new immutable spec/approval, then a real reservation.
    ApprovalGate(
        controller, scope["task"], SpecStore(repo / "docs/specs" / scope["task"]), repo
    ).approve()
    reservation = start_execution(controller, scope["task"], host="codex", launch=False)
    execution = get_execution(controller.conn, reservation["execution"]["id"])
    assert execution["id"] != first.execution_id
    second = OperationController.__new__(OperationController)
    second.database, second.execution_id = first.database, execution["id"]
    second.epoch_id, second.authority_generation = (
        execution["epoch_id"],
        execution["authority_generation"],
    )
    second.envelope = read_handoff(execution["handoff_path"])
    second.lock = threading.RLock()
    second.registry = PrincipalRegistry(
        tmp_path / "historical-registry",
        first.registry.native_root,
        execution["binding"],
        "safe-policy",
    )
    second.registry.observe_root_start(str(uuid.uuid4()))
    other = observed_principal(second, second.registry.root, "orchestrator")
    # Synthetic native observation is explicit; no real provider/start/closure claim.
    oid = store.begin(second, other, "read_file")
    store.finish(second, oid, "AUTHORITY_REJECTED")
    with serve(first.database, None) as server:
        status, _, raw = fetch(
            server, "/api/reliability/incidents?" + urlencode({"task": scope["task"]})
        )
        page = json.loads(raw)
        assert status == 200 and {r["execution_id"] for r in page["items"]} == {
            first.execution_id,
            second.execution_id,
        }
        result = post(
            server,
            "suggest",
            {"schema_version": 1, "scope": {"task": scope["task"]}, "limit": 100, "cursor": None},
        )
        assert result[0] == 200
        group = result[1]["result"]["items"][0]
        assert {r["execution_id"] for r in group["member_metadata"].values()} == {
            first.execution_id,
            second.execution_id,
        }
        assert {r["spec_version"] for r in group["member_metadata"].values()} == {1, 2}
        assert group["safe_key"]["execution_id"] is None


def test_verified_component_uses_actual_knowledge_snapshot_not_live_file_or_claim(observed):
    from harness.knowledge.models import RefreshRequest
    from harness.knowledge.policy import operator_scope
    from harness.knowledge.refresh import refresh

    operation, principal, controller = observed
    repo = Path(operation.envelope["repository"])
    oid = store.begin(operation, principal, "read_file")
    store.finish(operation, oid, "AUTHORITY_REJECTED")
    iid = controller.conn.execute("SELECT incident_id FROM reliability_incidents").fetchone()[0]
    root_scope = operator_scope(repo, task_id=selected(observed)["task"])
    snapshot = refresh(controller.conn, root_scope, RefreshRequest()).snapshot_id
    row = controller.conn.execute(
        "SELECT s.locator,m.version_id FROM knowledge_snapshot_sources m "
        "JOIN knowledge_sources s USING(source_id) WHERE m.snapshot_id=? "
        "AND s.locator='README.md' AND m.membership_state='present'",
        (snapshot,),
    ).fetchone()
    assert row is not None
    component = {
        "reference": row["locator"],
        "verification": "verified",
        "source_version": row["version_id"],
        "snapshot": snapshot,
    }
    body = {
        "schema_version": 1,
        "scope": selected(observed),
        "incident": iid,
        "expected_revision": 0,
        "request_id": "verified-component",
        "changes": {"affected_components": [component]},
    }
    with serve(operation.database, None) as server:
        result = post(server, "investigate", body)
        assert result[0] == 200, result
        assert result[1]["result"]["document"]["affected_components"] == [component]
        bad = body | {
            "expected_revision": 1,
            "request_id": "foreign-component",
            "changes": {"affected_components": [component | {"source_version": "foreign"}]},
        }
        assert post(server, "investigate", bad)[0] == 409


def test_missing_source_get_does_not_initialize_store_or_missing_database(observed, tmp_path):
    from harness.dashboard.incident_view import read_catalog

    operation, principal, controller = observed
    oid = store.begin(operation, principal, "read_file")
    store.finish(operation, oid, "AUTHORITY_REJECTED")
    iid = controller.conn.execute("SELECT incident_id FROM reliability_incidents").fetchone()[0]
    query = urlencode({"task": selected(observed)["task"], "incident": iid})
    with unchanged_database(operation.database), serve(operation.database, None) as server:
        assert fetch(server, "/api/reliability/detail?" + query)[0] == 200
        assert (
            fetch(
                server,
                "/api/reliability/detail?"
                + urlencode({"task": selected(observed)["task"], "incident": "missing-source"}),
            )[0]
            == 404
        )
    missing = tmp_path / "missing-database-directory" / "state.db"
    assert read_catalog(missing, Path(operation.envelope["repository"]), query, "detail")[0] == 503
    assert not missing.parent.exists()


def test_explicit_operator_projection_preserves_legacy_receipts_and_get_never_indexes(observed):
    from tests.incident_helpers import seed

    seed(observed, 2)
    scope = selected(observed)
    controller = observed[2]
    with serve(observed[0].database, None) as server:
        status, _, raw = fetch(server, "/api/reliability/projection?" + urlencode(scope))
        assert status == 200 and json.loads(raw)["checkpoint_revision"] == 0
        assert (
            controller.conn.execute("SELECT count(*) FROM reliability_incident_catalog").fetchone()[
                0
            ]
            == 0
        )
        body = {
            "schema_version": 1,
            "scope": scope,
            "request_id": "bounded-projection",
            "expected_revision": 0,
            "limit": 1,
        }
        first = post(server, "project", body)
        assert first[0] == 200 and first[1]["result"]["projected_count"] == 1
        assert first[1]["result"]["more_as_of_commit"]
        assert post(server, "project", body) == first
        assert post(server, "project", body | {"limit": 2})[0] == 409
        second = post(
            server,
            "project",
            body | {"request_id": "remaining-projection", "expected_revision": 1, "limit": 100},
        )
        assert second[0] == 200 and second[1]["result"]["projected_count"] == 2
        status, _, raw = fetch(server, "/api/reliability/incidents?" + urlencode(scope))
        assert status == 200 and all(i["indexing"] == "indexed" for i in json.loads(raw)["items"])


def test_bounded_output_descriptors_traverse_more_than_100_without_reading_content(operations):
    operation, _, controller, _, _, execution, _ = operations
    child, _ = generated_handoff_fixture(operations)
    # Publisher/index fixtures test pagination. The preceding case proves
    # public submit→select→verdict.
    identifiers = set()
    for n in range(105):
        record = operation.artifacts.submit_output(
            child, f"result-{n}.txt", "Observed output.", 1, 1
        )
        identifiers.add(record["artifact_id"])
    scope = {
        "task": execution["task_id"],
        "execution": execution["id"],
        "epoch": execution["epoch_id"],
    }
    seen, cursor = set(), None
    with serve(operation.database, None) as server:
        while True:
            query = scope | {"limit": 20} | (cursor or {})
            status, _, raw = fetch(server, "/api/reliability/outputs?" + urlencode(query))
            page = json.loads(raw)
            assert status == 200 and page["source_available"]
            assert len(page["items"]) <= 20 and not page["content_revalidated"]
            current = {r["artifact_id"] for r in page["items"]}
            assert not seen & current
            seen |= current
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert seen == identifiers
        assert (
            fetch(server, "/api/reliability/outputs?" + urlencode(scope | {"epoch": "foreign"}))[0]
            == 404
        )
        assert (
            fetch(server, "/api/reliability/outputs?" + urlencode({"task": execution["task_id"]}))[
                0
            ]
            == 400
        )
