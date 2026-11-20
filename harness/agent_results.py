"""Typed output requirements supplement, never replace, authored dependency provenance."""

from pathlib import Path

from harness import agent_contracts as C


def require_typed_result(execution, state, task_id):
    writers = [
        w
        for w in state["writers"].values()
        if w.get("task") == task_id and w.get("handoffs") and w["handoffs"][-1].get("contract")
    ]
    if not writers:
        return
    if execution is None:
        raise C.ContractError("/expected_outputs", kind="implementation-result")
    from harness.execution_artifacts import ArtifactStore

    store = ArtifactStore(
        Path(execution["handoff_path"]).parent / "operations/drafts", create=False
    )
    # This lock reads only local index metadata and is released before byte validation.
    with store.locked(read_only=True) as index:
        records = [
            dict(index["records"][ref])
            for rows in index.get("outputs", {}).values()
            for ref in rows.values()
        ]
    refs = [
        state["task_package"],
        *[a["artifact"] for a in state.get("amendments", [])],
        *state.get("recorded_artifacts", []),
    ]
    refs += [h["artifact"] for w in state["writers"].values() for h in w.get("handoffs", [])]

    cache = {}

    def load(reference):
        from harness.knowledge.sources import read_artifact
        from harness.policy.context import check_deadline

        check_deadline()
        actual = next((r for r in refs if C.ref(r) == reference), None)
        if actual:
            raw = read_artifact(Path(state["task_package"]["stored_path"]).parent, actual)
        elif reference["kind"] == "execution-artifact":
            raw = store.buffer(reference["id"], reference["sha256"])
        else:
            raise C.ContractError("/required_inputs", kind="implementation-result")
        check_deadline()
        return raw

    def resolve(reference):
        key = tuple(reference[k] for k in ("kind", "id", "sha256"))
        if key not in cache:
            cache[key] = load(reference)
        return cache[key]

    from harness.agent_exchange import specification

    for writer in writers:
        declared = writer["handoffs"][-1]["contract"]
        C.validate(declared, specification=specification(state))
        matching = [
            r
            for r in records
            if r.get("owner") == writer.get("agent")
            and r.get("contract")
            and r["graph_revision"] == state["integration"]["graph_revision"]
        ]
        found = False
        for record in matching:
            value = record["contract"]
            C.validate(value)
            if (
                value["kind"] != "implementation-result"
                or value["context"]["subtask"] != task_id
                or value["context"]["task_revision"]
                != state["integration"]["tasks"][task_id]["revision"]
                or value["context"]["producer"] != writer.get("agent")
                or value["context"]["execution"] != execution["id"]
                or value["context"]["epoch"] != execution.get("epoch_id")
                or value["context"]["authority_generation"] != execution.get("authority_generation")
                or value["source_specification"] != declared["source_specification"]
            ):
                continue
            if {"name": value["payload"]["record_id"], "kind": value["kind"]} not in declared[
                "expected_outputs"
            ]:
                continue
            required = [r for r in declared["required_inputs"] if r["required"]]
            if any(r not in value["required_inputs"] for r in required):
                continue
            C.validate(value, specification=specification(state), resolve=resolve)
            C.validate_dependencies(value, state, task_id, resolve)
            raw = store.buffer(record["artifact_id"], record["sha256"])
            if C.fingerprint(raw.decode("utf-8")) != value["payload"]["record_digest"]:
                raise C.ContractError("/payload", kind="implementation-result")
            found = True
        if not found:
            raise C.ContractError("/expected_outputs", kind="implementation-result")
