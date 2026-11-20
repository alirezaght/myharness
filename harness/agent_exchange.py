"""Authenticated Harness adapters; external bytes resolve only in the reserved use phase."""

import json
from pathlib import Path

from harness import agent_contracts as C

SNAPSHOT_COVERAGE = {
    "immutable_snapshot_validated": True,
    "mutable_source_not_checked": True,
    "coordinated_source_consumption": False,
    "legacy_uncoordinated": None,
}


def context(execution, state, principal, task=None):
    binding = state.get("integration", {}).get("binding", {})
    values = dict(
        al_run=state["run_id"],
        al_session=state["session_id"],
        task=binding.get("task_id"),
        execution=execution["id"],
        subtask=task,
        graph_revision=state["integration"]["graph_revision"],
        task_revision=state["integration"]["tasks"].get(task, {}).get("revision"),
        producer=principal["thread_id"],
        epoch=execution.get("epoch_id"),
        authority_generation=execution.get("authority_generation"),
    )
    values["applicability"] = {k: "not-observed" for k, v in values.items() if v is None}
    return values


def specification(state):
    return {
        "reference": C.ref(state["task_package"]),
        "amendments": [C.ref(a["artifact"]) for a in state.get("amendments", [])],
    }


def task_for(state, principal):
    agent = state["integration"]["agents"].get(principal["thread_id"])
    return agent.get("task") if agent else None


def resolver(operation, execution, state):
    from harness.execution_hosts import al_source
    from harness.knowledge.sources import read_artifact

    refs = [
        state["task_package"],
        *[a["artifact"] for a in state.get("amendments", [])],
        *state.get("recorded_artifacts", []),
    ]
    refs += [r["artifact"] for r in state.get("agent_outputs", {}).values()]
    refs += [
        h["artifact"] for w in state.get("writers", {}).values() for h in w.get("handoffs", [])
    ]
    directory = (
        Path(al_source(execution, operation.envelope)).parent
        / execution["al_session_id"]
        / "artifacts"
    )

    cache = {}

    def load(reference):
        from harness.policy.context import check_deadline

        check_deadline()
        record = next((r for r in refs if C.ref(r) == reference), None)
        if record:
            raw = read_artifact(directory, record)
            check_deadline()
            return raw
        if reference["kind"] == "execution-artifact":
            raw = operation.artifacts.buffer(reference["id"], reference["sha256"])
            check_deadline()
            return raw
        raise C.ContractError("/required_inputs")

    def read(reference):
        key = json.dumps(reference, sort_keys=True)
        if key not in cache:
            cache[key] = load(reference)
        return cache[key]

    return read


def validate_output(operation, execution, state, principal, content, contract):
    task = task_for(state, principal)
    expected = context(execution, state, principal, task)
    read = resolver(operation, execution, state)
    C.validate(
        contract,
        context=expected,
        specification=specification(state),
        resolve=read,
    )
    if contract["kind"] != "implementation-result" or contract["payload"][
        "record_digest"
    ] != C.fingerprint(content):
        raise C.ContractError("/payload", kind="implementation-result")
    assignments = [
        h
        for w in state["writers"].values()
        if w.get("agent") == principal["thread_id"]
        for h in w.get("handoffs", [])
    ]
    if assignments and assignments[-1].get("contract"):
        declared = assignments[-1]["contract"]
        if {"name": contract["payload"]["record_id"], "kind": contract["kind"]} not in declared[
            "expected_outputs"
        ]:
            raise C.ContractError("/expected_outputs", kind="implementation-result")
        required = {
            json.dumps(r, sort_keys=True) for r in declared["required_inputs"] if r["required"]
        }
        supplied = {
            json.dumps(r, sort_keys=True) for r in contract["required_inputs"] if r["required"]
        }
        if not required <= supplied:
            raise C.ContractError("/required_inputs", kind="implementation-result")
    C.validate_dependencies(contract, state, task, read)
    package = json.loads(read(specification(state)["reference"]))
    criteria = state["integration"]["tasks"].get(task, {}).get("criteria", package["criteria"])
    if contract["acceptance_criteria"] != criteria:
        raise C.ContractError("/acceptance_criteria", kind="implementation-result")
    return contract


def validate_assignment(receipt, state, writer, resolve):
    contract = receipt.get("contract")
    if contract is None:
        return {"coverage": "legacy"}
    # Producer is the bound writer/native invocation, never the requesting envelope's assertion.
    from_context = dict(
        al_run=state["run_id"],
        al_session=state["session_id"],
        task=state.get("integration", {}).get("binding", {}).get("task_id"),
        execution=state.get("integration", {}).get("binding", {}).get("execution_id"),
        subtask=writer.get("task"),
        graph_revision=state["integration"]["graph_revision"],
        task_revision=state["integration"]["tasks"].get(writer.get("task"), {}).get("revision"),
        producer=writer.get("agent") or writer["id"],
        epoch=state.get("integration", {}).get("binding", {}).get("epoch_id"),
        authority_generation=state.get("integration", {})
        .get("binding", {})
        .get("authority_generation"),
    )
    from_context["applicability"] = {
        k: "not-observed" for k, v in from_context.items() if v is None
    }
    C.validate(contract, context=from_context, specification=specification(state), resolve=resolve)
    C.validate_dependencies(contract, state, writer.get("task"), resolve)
    original = {k: v for k, v in receipt.items() if k != "contract"}
    if contract["kind"] != "task-assignment" or contract["payload"][
        "record_digest"
    ] != C.fingerprint(original):
        raise C.ContractError("/payload", kind="task-assignment")
    return contract
