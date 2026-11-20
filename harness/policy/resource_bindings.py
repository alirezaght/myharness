"""Private scopes derived from admitted writers and authenticated dependency receipts."""

from pathlib import Path

from harness.execution_contract import canonical
from harness.policy.claims import claim


def derive(operation, principal, state, *, task_id, resources, mutating, boundary):
    repository = operation._policy_repository
    execution = operation.execution_id
    writers = [
        w["id"]
        for w in state.get("writers", {}).values()
        if w.get("task") == task_id
        and w.get("agent") == principal["thread_id"]
        and w["state"] == "ACTIVE"
    ]
    task = state.get("integration", {}).get("tasks", {}).get(task_id)
    common = {
        "execution": execution,
        "task": task_id,
        "revision": task["revision"] if task else None,
        "principals": (principal["thread_id"],),
        "writers": writers,
    }
    values = [
        claim(
            repository,
            value.removeprefix("file:"),
            "write" if mutating else "read_dependency",
            **common,
        )
        for value in resources
        if value.startswith("file:")
    ]
    if "control:repository_audit" in resources:
        values.append(claim(repository, ".", "read_dependency", **common))
    if set(resources) & {"control:typed_assignment", "control:typed_output"}:
        values.append(
            claim(
                str(operation.artifacts.directory),
                ".",
                "write" if mutating else "read_dependency",
                **common,
            )
        )
        values.append(
            claim(
                str(
                    Path(operation.initialization["source"]).parent
                    / state["session_id"]
                    / "artifacts"
                ),
                ".",
                "read_dependency",
                **common,
            )
        )
    if mutating and not values:
        values.append(claim(repository, ".", "unknown", **common))
    if task and not set(resources) & {"control:typed_assignment", "control:typed_output"}:
        values.extend(dependency_claims(repository, execution, state, task_id))
    return tuple({canonical(value): value for value in values}.values())


def dependency_claims(repository, execution, state, task_id):
    """Select the exact receipt variant used by the authoritative dependency consumer."""
    from harness.agent_contracts import dependency_records, fingerprint

    values = []
    for dep, selected in dependency_records(state, task_id).items():
        producer = state["integration"]["tasks"][dep]
        receipt = selected["receipt"]
        outputs = receipt.get("output_refs_and_hashes", receipt.get("outputs", []))
        source = [o for o in outputs if o.get("kind") == "source"]
        if not source:
            continue
        scopes = set(producer["files"]) | {o["path"] for o in source}
        for path in sorted(scopes):
            value = claim(
                repository,
                path,
                "read_dependency",
                execution=execution,
                task=dep,
                revision=producer["revision"],
                principals=receipt.get("producer_invocation_ids", ()),
                writers=receipt.get("writer_ids", ()),
            )
            value.update(purpose="mutable_dependency", producer_receipt=fingerprint(selected))
            values.append(value)
    return tuple({canonical(value): value for value in values}.values())
