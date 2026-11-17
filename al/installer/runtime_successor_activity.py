"""Task operation guards supplement kernel attribution, never replace settlement."""

from pathlib import Path

from .activity_operations import check_blockers, reconciliation_validation, setup_checks
from .runtime_pin_transaction import runtime_directory, state_path
from .validator_origin import capture, digest


def require_operations(transaction, state):
    if state is None:
        # Missing-state callers must independently validate retained Harness settlement.
        return
    if any(w["state"] in {"ACTIVE", "UNRESOLVED"} for w in state["writers"].values()):
        raise ValueError("predecessor writer operations are unsettled")
    root = runtime_directory(transaction)
    for entry in state["evidence"]["entries"]:
        if entry["status"] == "RUNNING":
            raise ValueError("predecessor evidence operations are unsettled")
        provenance = {"runtime_root": str(root), "members": {}}
        reference = (entry.get("reconciliation") or {}).get("artifact")
        if reference:
            path = Path(reference["stored_path"])
            relative = path.relative_to(root).as_posix()
            raw, _ = capture(path)
            provenance["members"][relative] = {"type": "file", "sha256": digest(raw)}
        validation = (
            reconciliation_validation(
                entry, state, provenance, digest(capture(state_path(transaction))[0])
            )
            if entry.get("reconciliation") is not None or entry.get("interrupted")
            else None
        )
        if check_blockers(entry, validation):
            raise ValueError("predecessor evidence operations are unsettled")
    for check in setup_checks(state["evidence"]["entries"]):
        if check_blockers(check):
            raise ValueError("predecessor setup operations are unsettled")
    integration = state.get("integration", {})
    if integration.get("pending") or any(
        agent.get("status") not in {"Completed", "Failed", "Cancelled"}
        for agent in integration.get("agents", {}).values()
    ):
        raise ValueError("predecessor agent operations are unsettled")


def recorded_processes(state):
    if state is None:
        return []
    result = []
    for entry in [*state["evidence"]["entries"], *setup_checks(state["evidence"]["entries"])]:
        process = entry.get("process") or {}
        for pid, metadata in (
            (entry.get("pid"), process),
            (process.get("pid"), process),
            (entry.get("owner_pid"), entry.get("owner_identity") or {}),
        ):
            if pid is not None:
                result.append((pid, metadata.get("identity", metadata)))
    return result
