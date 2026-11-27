"""Real authenticated dispatcher/outbox seams; synthetic fixtures are not native acceptance."""

import json
import subprocess
import time
from pathlib import Path

import pytest

from harness.knowledge.contributions import ingest
from harness.knowledge.models import ContextRequest, canonical, digest
from harness.knowledge.policy import current_operator_scope
from harness.knowledge.query import query
from harness.knowledge.refresh import refresh
from tests.test_execution_operations import generated_handoff_fixture, observed_principal
from tests.test_execution_operations import operations as operations


def submit_authored_output(operation, controller, principal, name, content):
    """Submit a report using its current generated assignment exchange."""
    from harness.agent_contracts import make
    from harness.agent_exchange import context, specification

    execution, state = operation.authority(controller, principal, operation="read")
    writer = next(
        writer
        for writer in state["writers"].values()
        if writer.get("agent") == principal["thread_id"] and writer.get("handoffs")
    )
    declared = writer["handoffs"][-1]["contract"]
    output_name = next(
        row["name"]
        for row in declared["expected_outputs"]
        if row["kind"] == "implementation-result"
    )
    contract = make(
        "implementation-result",
        context(execution, state, principal, writer["task"]),
        specification(state),
        output_name,
        content,
        status="authored",
        inputs=declared["required_inputs"],
        criteria=declared["acceptance_criteria"],
        constraints=declared["constraints"],
    )
    return operation.dispatch(
        principal,
        "submit_output",
        {
            "name": name,
            "content": content,
            "expected_graph_revision": state["integration"]["graph_revision"],
            "contract": contract,
        },
    )


def seeded(operations):
    operation, _, controller, _, _, execution, _ = operations
    root = Path(operation.envelope["repository"])
    (root / "service.py").write_text("def serve():\n    return 1\n")
    subprocess.run(["git", "-C", str(root), "add", "service.py"], check=True)
    scope = current_operator_scope(controller.conn, root, task_id=execution["task_id"])
    refresh(controller.conn, scope)
    return scope


def test_read_only_registration_is_own_observation_not_writer_admission(operations):
    operation, supervisor, _, initialization, _, execution, _ = operations
    generated_handoff_fixture(operations)
    readonly = observed_principal(operation, "readonly-scout", "al-scout")
    path = Path(initialization["source"])
    state = json.loads(path.read_bytes())
    state["integration"]["tasks"]["sibling-private"] = {
        "id": "sibling-private",
        "title": "PRIVATE_CANARY",
        "status": "Running",
        "revision": 99,
        "files": ["PRIVATE_CANARY"],
    }
    state["integration"]["agents"][readonly["thread_id"]] = {
        "id": readonly["thread_id"],
        "name": "Observed read-only scout",
        "task": "handoff-task",
        "role": "al-scout",
        "status": "Running",
        "started_at": time.time(),
        "registration": "fixture",
    }
    path.write_text(json.dumps(state))
    result = operation.dispatch(readonly, "await_assignment", {"timeout": 1})
    assert result["status"] == "ReadOnly" and not result["writable"]
    assert not result["control_plane"]["al"]
    observed = result["registered_assignment"]
    assert observed == {
        "status": "available",
        "agent_id": readonly["thread_id"],
        "agent_status": "Running",
        "al_task_id": "handoff-task",
        "task_status": "Running",
        "task_revision": 1,
        "files": ["source"],
        "graph_revision": 1,
        "harness_task_id": execution["task_id"],
        "execution_id": execution["id"],
        "epoch_id": execution["epoch_id"],
    }
    assert "PRIVATE_CANARY" not in json.dumps(result)
    assert "admission" not in observed
    assert result["knowledge_contribution_contract"]["assertion_keys"]
    with pytest.raises(ValueError, match="no writable task assignment"):
        operation.dispatch(
            readonly,
            "apply_patch",
            {
                "patch": (
                    "*** Begin Patch\n*** Add File: source/prohibited.py\n"
                    "+forbidden = True\n*** End Patch\n"
                ),
                "task_id": "handoff-task",
                "expected_revision": "stale",
            },
        )
    assert (
        operation.dispatch(supervisor, "await_assignment", {"timeout": 1})["status"] == "Supervisor"
    )
    unknown = observed_principal(operation, "unregistered-scout", "al-scout")
    assert (
        operation.dispatch(unknown, "await_assignment", {"timeout": 1})["registered_assignment"][
            "status"
        ]
        == "not_registered"
    )


def test_knowledge_consumers_tool_answers_who_imports_a_module(operations):
    """#31: knowledge_consumers sits next to knowledge_query for native agents."""
    operation, supervisor, controller, _, _, _, _ = operations
    root = Path(operation.envelope["repository"])
    (root / "client.py").write_text("from service import serve\n")
    subprocess.run(["git", "-C", str(root), "add", "client.py"], check=True)
    seeded(operations)
    reader = observed_principal(operation, "consumer-reader", "al-scout")
    result = operation.dispatch(reader, "knowledge_consumers", {"target": "service.py"})
    assert [c["locator"] for c in result["consumers"]] == ["client.py"]
    with pytest.raises(ValueError):
        operation.dispatch(reader, "knowledge_consumers", {"target": "service.py", "x": 1})


def test_dispatcher_receipts_identify_each_exact_query_and_explanation(operations):
    operation, supervisor, controller, _, _, _, _ = operations
    seeded(operations)
    reader = observed_principal(operation, "receipt-reader", "al-scout")
    first = operation.dispatch(reader, "knowledge_query", {"query": "serve"})
    second = operation.dispatch(reader, "knowledge_query", {"query": "serve"})
    assert first["bundle_hash"] == second["bundle_hash"]
    assert first["receipt_id"] != second["receipt_id"]
    payload = {k: v for k, v in first.items() if k not in {"bundle_hash", "receipt_id"}}
    assert digest(canonical(payload).encode()) == first["bundle_hash"]
    explanation = operation.dispatch(
        reader,
        "knowledge_explain",
        {
            "bundle_hash": first["bundle_hash"],
            "assertion_id": first["assertions"][0]["assertion_id"],
        },
    )
    assert explanation["receipt_id"] not in {first["receipt_id"], second["receipt_id"]}
    assert explanation["bundle_hash"] == first["bundle_hash"]
    for result in (first, second, explanation):
        row = controller.conn.execute(
            "SELECT * FROM knowledge_receipts WHERE receipt_id=?", (result["receipt_id"],)
        ).fetchone()
        assert row["bundle_hash"] == first["bundle_hash"] and row["purpose"] == "planning"


def test_invalid_original_and_corrected_output_preserve_bytes_and_provenance_guards(operations):
    operation, supervisor, controller, initialization, _, _, _ = operations
    scope = seeded(operations)
    parent = query(controller.conn, scope, ContextRequest("serve")).payload["assertions"][0][
        "assertion_id"
    ]
    child, _ = generated_handoff_fixture(operations)
    invalid = {
        "assertions": [
            {
                "subject": "module:service",
                "predicate": "handles",
                "text": "Authored STATIC observation",
                "status": "STATIC",
                "parents": ["service.py:1"],
            }
        ]
    }

    def submit(name, document):
        text = json.dumps(document, indent=2)
        result = submit_authored_output(operation, controller, child, name, text)
        resource = "artifact:" + result["artifact_id"] + ":" + result["sha256"]
        stored = operation.dispatch(supervisor, "read_file", {"path": resource})
        assert stored["text"] == text
        return result, resource

    original, original_ref = submit("original.json", invalid)
    assert original["knowledge_contribution"]["status"] == "invalid"
    assert {"unsupported-assertion-field", "invalid-parent-reference"} <= {
        i["code"] for i in original["knowledge_contribution"]["issues"]
    }
    corrected = {
        "assertions": [
            {
                "subject": "module:service",
                "predicate": "handles",
                "text": "Authored STATIC observation",
                "parents": [parent],
            }
        ]
    }
    ready, ready_ref = submit("corrected.json", corrected)
    assert ready["knowledge_contribution"]["status"] == "ready"
    with pytest.raises(ValueError, match="completed observed producer"):
        ingest(operation, controller, supervisor, "handoff-task", ready_ref)
    path = Path(initialization["source"])
    state = json.loads(path.read_bytes())
    agent = state["integration"]["agents"][child["thread_id"]]
    agent.update(status="Completed", ended_at=time.time(), latest_output=ready_ref)
    path.write_text(json.dumps(state))
    assert (
        ingest(operation, controller, supervisor, "handoff-task", ready_ref)["status"]
        == "published"
    )
    result = query(controller.conn, scope, ContextRequest("Authored STATIC observation"))
    claim = next(a for a in result.payload["assertions"] if a["predicate"] == "handles")
    assert claim["derivation_parents"] == [parent]
    assert claim["origin"] == "attributed-inference" and claim["verification"] == "unverified"
    assert operation.dispatch(supervisor, "read_file", {"path": original_ref})[
        "text"
    ] == json.dumps(invalid, indent=2)


@pytest.mark.parametrize("outcome", ["invalid", "valid", "storage_failure"])
def test_actual_al_completion_keeps_optional_knowledge_outcome_closed(
    operations, monkeypatch, outcome
):
    import sqlite3
    from importlib import import_module

    operation, supervisor, controller, _, _, execution, _ = operations
    seeded(operations)
    child, _ = generated_handoff_fixture(operations)
    document = (
        {
            "assertions": [
                {"subject": "x", "predicate": "observes", "text": "safe", "status": "STATIC"}
            ]
        }
        if outcome == "invalid"
        else {"summary": "Completed authored sourced observation"}
    )
    output = submit_authored_output(
        operation, controller, child, "final.json", json.dumps(document)
    )
    resource = "artifact:" + output["artifact_id"] + ":" + output["sha256"]
    update = operation.dispatch(
        supervisor,
        "write_artifact",
        {
            "name": "completion.json",
            "content": json.dumps(
                {
                    "id": child["thread_id"],
                    "status": "Completed",
                    "latest_output": resource,
                }
            ),
            "expected_graph_revision": 1,
        },
    )
    if outcome == "storage_failure":
        module = import_module("harness.knowledge.contributions")

        def unavailable(*args):
            raise sqlite3.OperationalError("STORAGE_CANARY")

        monkeypatch.setattr(module, "ingest", unavailable)
    result = operation.dispatch(
        supervisor,
        "al_command",
        {
            "arguments": [
                "integration",
                "agent-update",
                "--file",
                "artifact:" + update["artifact_id"] + ":" + update["sha256"],
            ]
        },
    )
    assert result["returncode"] == 0, result
    state = operation.state(execution)
    agent = state["integration"]["agents"][child["thread_id"]]
    assert agent["status"] == "Completed" and agent["ended_at"]
    assert agent["latest_output"] == resource
    contribution = result["knowledge_contributions"][0]
    assert (
        contribution["status"]
        == {"invalid": "invalid", "valid": "published", "storage_failure": "unavailable"}[outcome]
    )
    if outcome == "invalid":
        assert contribution["feedback"] == output["knowledge_contribution"]
    assert "STORAGE_CANARY" not in json.dumps(result)
    assert controller.conn.execute("SELECT count(*) FROM knowledge_contributions").fetchone()[
        0
    ] == (outcome == "valid")


_SEALED_HTTP_PROGRAM = r"""
import json, sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

assert sys.flags.isolated and sys.flags.no_site
assert not any(n == "harness" or n.startswith("harness.") for n in sys.modules)
snapshot = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(snapshot))
sys.path.append(sys.argv[2])
context = json.loads(Path(sys.argv[3]).read_bytes())
from harness.execution_mcp import serve_mcp
from harness.execution_operations import OperationController
from harness.execution_principals import PrincipalRegistry

registry = PrincipalRegistry(
    context["registry"],
    context["native_root"],
    context["principal"]["binding"],
    context["principal"]["policy"],
)
principal = context["principal"]
operation = OperationController(
    context["database"],
    context["execution"],
    context["envelope"],
    context["package"],
    context["initialization"],
    registry,
    context["directory"],
)


def resolve(meta):
    if meta != {"fixture_principal": principal["thread_id"]}:
        raise ValueError("fixture principal metadata differs")
    registry.require_live(principal)
    return principal


try:
    with serve_mcp(resolve, operation.dispatch) as endpoint:
        headers = {
            "Authorization": "Bearer " + endpoint["token"],
            "Content-Type": "application/json",
        }
        opener = build_opener(ProxyHandler({}))

        def call(identifier, method, params):
            body = json.dumps(
                {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}
            ).encode()
            with opener.open(
                Request(endpoint["url"], body, headers=headers), timeout=15
            ) as response:
                assert response.status == 200
                headers["Mcp-Session-Id"] = response.headers["Mcp-Session-Id"]
                value = json.loads(response.read())
            assert value["jsonrpc"] == "2.0" and value["id"] == identifier
            return value["result"]

        def tool(identifier, name, arguments):
            result = call(
                identifier,
                "tools/call",
                {
                    "name": name,
                    "arguments": arguments,
                    "_meta": {"fixture_principal": principal["thread_id"]},
                },
            )
            assert result["isError"] is False, result
            return json.loads(result["content"][0]["text"])

        call(1, "initialize", {})
        assignment = tool(2, "await_assignment", {"timeout": 1})
        assert assignment["status"] == "ReadOnly" and not assignment["writable"]
        observed = assignment["registered_assignment"]
        assert observed["status"] == "available"
        assert observed["agent_id"] == principal["thread_id"]
        assert observed["al_task_id"] == "handoff-task" and observed["files"] == ["source"]
        assert observed["execution_id"] == context["execution"]["id"]
        import hashlib
        delivery = assignment["knowledge_context"]
        assert delivery["status"] == "available", delivery
        received = json.dumps(delivery["context"], sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False).encode()
        assert len(received) <= 32768
        assert delivery["presentation"]["size"] == len(received)
        assert delivery["presentation"]["sha256"] == hashlib.sha256(received).hexdigest()
        assert delivery["presentation"]["model_use"] == "unobserved"
        estimate = delivery["presentation"]["size_estimate"]
        assert type(estimate["value"]) is int and estimate["value"] > 0
        assert estimate["unit"] == "estimated tokens"
        assert "not-tokenizer-exact" in estimate["method"]
        assert delivery["context"]["context"]["request"]["stage"] == "planning"
        assert delivery["context"]["citations"]
        bundle = tool(3, "knowledge_query", {"query": "sealed donor continuity"})
        claim = next(a for a in bundle["assertions"] if a["predicate"] == "handles")
        assert claim["verification"] == "unverified"
        assert claim["origin"] == "attributed-inference"
        explanation = tool(
            4,
            "knowledge_explain",
            {"bundle_hash": bundle["bundle_hash"], "assertion_id": claim["assertion_id"]},
        )
        assert explanation["derivation"]["status"] == "complete"
        assert explanation["derivation"]["direct_parent_ids"] == [context["parent"]]
        assert explanation["receipt_id"] != bundle["receipt_id"]
        content = json.dumps({"summary": "exact sealed HTTP output"}, indent=2)
        output = tool(
            5,
            "submit_output",
            {
                "name": "sealed-report.json",
                "content": content,
                "expected_graph_revision": assignment["graph_revision"],
            },
        )
        assert output["knowledge_contribution"]["status"] == "ready"
        resource = "artifact:" + output["artifact_id"] + ":" + output["sha256"]
        assert tool(6, "read_file", {"path": resource})["text"] == content
        try:
            request = Request(
                endpoint["url"], b"{}", headers={**headers, "Authorization": "Bearer invalid"}
            )
            opener.open(request, timeout=15)
            raise AssertionError("invalid bearer accepted")
        except HTTPError as error:
            assert error.code == 403
    for name, module in tuple(sys.modules.items()):
        if name == "harness" or name.startswith("harness."):
            assert Path(module.__file__).resolve().is_relative_to(snapshot / "harness"), name
    print(
        json.dumps(
            {"status": "complete", "output": output, "bundle_hash": bundle["bundle_hash"]}
        )
    )
finally:
    operation.close()
"""


def test_sealed_bridge_http_reaches_assignment_contribution_and_outbox(operations, tmp_path):
    from harness.execution_bridge import bridge_root
    from tests.test_execution_bootstrap_closure import isolated_snapshot_child

    operation, supervisor, controller, initialization, package, execution, envelope = operations
    root = Path(operation.envelope["repository"])
    (root / "source").mkdir(exist_ok=True)
    (root / "source/context.py").write_text("def bounded_fixture():\n    return 1\n")
    subprocess.run(["git", "-C", str(root), "add", "source/context.py"], check=True)
    scope = seeded(operations)
    parent = query(controller.conn, scope, ContextRequest("serve")).payload["assertions"][0][
        "assertion_id"
    ]
    donor, _ = generated_handoff_fixture(operations, child_id="sealed-donor")
    document = {
        "assertions": [
            {
                "subject": "module:service",
                "predicate": "handles",
                "text": "sealed donor continuity",
                "parents": [parent],
            }
        ]
    }
    output = submit_authored_output(
        operation, controller, donor, "sealed-donor.json", json.dumps(document)
    )
    resource = "artifact:" + output["artifact_id"] + ":" + output["sha256"]
    path = Path(initialization["source"])
    state = json.loads(path.read_bytes())
    state["integration"]["agents"][donor["thread_id"]].update(
        status="Completed",
        ended_at=time.time(),
        latest_output=resource,
    )
    recipient = observed_principal(operation, "sealed-recipient", "al-scout")
    state["integration"]["agents"][recipient["thread_id"]] = {
        "id": recipient["thread_id"],
        "name": "Sealed recipient",
        "role": "al-scout",
        "task": "handoff-task",
        "status": "Running",
        "started_at": time.time(),
        "registration": "fixture",
    }
    path.write_text(json.dumps(state))
    assert (
        ingest(operation, controller, supervisor, "handoff-task", resource)["status"] == "published"
    )
    context = {
        "database": str(operation.database),
        "execution": execution,
        "envelope": envelope,
        "package": package,
        "initialization": initialization,
        "principal": recipient,
        "registry": str(operation.registry.directory),
        "native_root": str(operation.registry.native_root),
        "directory": str(operation.directory),
        "parent": parent,
    }
    context_path = tmp_path / "sealed-http-context.json"
    context_path.write_text(json.dumps(context))
    result = isolated_snapshot_child(
        bridge_root(execution),
        _SEALED_HTTP_PROGRAM,
        [str(context_path)],
        tmp_path / "isolated-http",
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["status"] == "complete"
    published = operation.artifacts.resolve(
        observed["output"]["artifact_id"], observed["output"]["sha256"]
    )
    assert (
        published.read_bytes()
        == json.dumps({"summary": "exact sealed HTTP output"}, indent=2).encode()
    )
