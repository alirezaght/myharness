"""Explicit authenticated capability release is distinct from host termination."""

from datetime import datetime

from harness.execution_contract import canonical, digest
from harness.execution_handoff import durable_file
from harness.native_identity import read_native_identity


def release_for_replacement(operation, state, agent, native):
    if operation.envelope["host"] != "codex":
        raise ValueError("fresh replacement creation provenance unavailable for this native host")
    previous = agent.get("replaces")
    reason = agent.get("release_reason")
    if not previous or not isinstance(reason, str) or len(reason.strip()) < 20:
        raise ValueError(
            "task reassignment requires explicit predecessor capability release and reason"
        )
    prior = operation.assignments.get(previous)
    old = state["integration"]["agents"].get(previous)
    if not prior or not old or prior["task"] != agent.get("task") or previous == agent["id"]:
        raise ValueError("replacement does not match the recorded predecessor assignment")
    if agent["id"] in state["integration"]["agents"]:
        existing = state["integration"]["agents"][agent["id"]]
        if existing.get("registration") != digest(agent):
            raise ValueError("replacement invocation was already used")
    identity = read_native_identity(operation.registry.native_root, agent["id"])
    try:
        created = datetime.fromisoformat(identity.created_at.replace("Z", "+00:00"))
        if created.tzinfo is None or created.timestamp() <= old["started_at"]:
            raise ValueError("replacement predates predecessor assignment")
    except (ValueError, TypeError, AttributeError):
        raise ValueError(
            "replacement requires genuinely fresh native SessionMeta creation provenance"
        ) from None
    task = state["integration"]["tasks"][prior["task"]]
    for writer in state["writers"].values():
        if writer.get("agent") == previous and writer["state"] not in {"FINISHED", "RECONCILED"}:
            raise ValueError("settle predecessor writer before releasing its capability")

    def overlap(a, b):
        return a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")

    for check in state["evidence"]["entries"]:
        if (
            check["status"] == "RUNNING"
            or check.get("interrupted")
            and not check.get("reconciliation")
        ) and any(overlap(a, b) for a in check["scope"] for b in task["files"]):
            raise ValueError("settle predecessor checks before releasing its capability")
    for record in operation.processes.records.values():
        if record["owner"] == previous and (
            record["status"] == "Running" or record["process"].poll() is None
        ):
            raise ValueError("settle predecessor processes before releasing its capability")
    receipt = {
        "kind": "native-capability-release-v1",
        "predecessor": previous,
        "successor": agent["id"],
        "task": prior["task"],
        "task_revision": task["revision"],
        "graph_revision": state["integration"]["graph_revision"],
        "assignment": prior,
        "native_metadata_digest": native["metadata_digest"],
        "reason": reason,
        "binding": state["integration"]["binding"],
        "host_termination_observed": False,
    }
    path = operation.registry.directory / "capability-releases" / (previous + ".json")
    durable_file(path, canonical(receipt).encode())
    archive = operation.directory / "assignment-history" / (digest(receipt).split(":")[1] + ".json")
    durable_file(archive, canonical(receipt).encode())
    return receipt
