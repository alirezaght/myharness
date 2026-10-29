"""Inherited acceptance is protected lineage, never a new successor delivery."""

import hashlib
import json
from pathlib import Path

from harness.execution_epochs import get_epoch
from harness.execution_models import ExecutionConflict
from harness.template_classification import _ordinary


def _read(reference, storage):
    path = Path(reference["stored_path"])
    if not path.is_relative_to(storage / "successors"):
        raise ExecutionConflict("inherited lineage reference escaped protected issuer storage")
    raw, mode = _ordinary(path, 32 * 1024 * 1024)
    if mode != 0o600 or hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise ExecutionConflict("inherited lineage record changed")
    return json.loads(raw)


def scoped_amendments(state, task_id):
    prefix = state.get("inherited_lineage", {}).get("prefix_length", 0)
    inherited = {
        r["id"] for r in state["integration"]["tasks"][task_id].get("inherited_scopes", [])
    }
    return [
        a
        for index, a in enumerate(state["amendments"])
        if (a["id"] in inherited if index < prefix else task_id in a.get("affected_tasks", []))
    ]


def validate_inherited_scope(conn, execution, state, task_id):
    if execution["epoch_id"] == "legacy-0":
        if state.get("inherited_lineage"):
            raise ExecutionConflict("legacy execution cannot acquire inherited authority")
        return set()
    epoch = get_epoch(conn, execution["id"])
    receipt = epoch["identity"]["preparation"]
    storage = Path(receipt["successor_transaction"]["initialization"]["stored_path"]).parent.parent
    lineage = _read(receipt["lineage_ref"], storage)
    requirements = _read(lineage["requirements_ref"], storage)
    prefix = requirements["successor_amendments"]
    if (
        state.get("inherited_lineage")
        != {"reference": receipt["lineage_ref"], "prefix_length": len(prefix)}
        or state["amendments"][: len(prefix)] != prefix
        or state["task_package"]["sha256"] != requirements["original_package"]["sha256"]
    ):
        raise ExecutionConflict("successor inherited requirement prefix changed")
    layers = []
    cursor = lineage
    seen = set()
    while True:
        previous = _read(cursor["predecessor_state_ref"], storage)
        if previous["run_id"] in seen or len(seen) >= 16:
            raise ExecutionConflict("cyclic inherited source lineage")
        seen.add(previous["run_id"])
        layers.append(previous)
        if "inherited_lineage" not in previous:
            break
        cursor = _read(previous["inherited_lineage"]["reference"], storage)
    task = state["integration"]["tasks"][task_id]

    def overlaps(a, b):
        return a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")

    mapped = []
    for amendment in requirements["amendments"]:
        if not amendment.get("scoped"):
            continue
        origin = next(
            (layer for layer in layers if amendment["id"] in layer["integration"]["deliveries"]),
            None,
        )
        if (
            origin is None
            or requirements["scoped_acceptance"].get(amendment["id"])
            != amendment["artifact"]["sha256"]
            or origin["integration"]["deliveries"][amendment["id"]]["decision_hash"]
            != amendment["delivery_hash"]
        ):
            raise ExecutionConflict("inherited acceptance lacks immutable acknowledgement")
        paths = sorted(
            {
                p
                for tid in amendment["affected_tasks"]
                for p in origin["integration"]["tasks"][tid]["files"]
            }
        )
        if set(task["criteria"]) & set(amendment["affected_criteria"]) or any(
            overlaps(a, b) for a in task["files"] for b in paths
        ):
            mapped.append(
                {
                    "id": amendment["id"],
                    "artifact_sha256": amendment["artifact"]["sha256"],
                    "predecessor_tasks": amendment["affected_tasks"],
                    "paths": paths,
                    "criteria": amendment["affected_criteria"],
                }
            )
    if task.get("inherited_scopes", []) != mapped:
        raise ExecutionConflict(
            "successor inherited scope mapping differs from actual paths/criteria"
        )
    return {row["id"] for row in mapped}
