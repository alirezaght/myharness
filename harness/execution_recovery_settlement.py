"""Independent native settlement; absent mutable AL state never implies completion."""

import json
from pathlib import Path

from harness.execution_contract import canonical, digest
from harness.execution_handoff import atomic_file
from harness.execution_models import ExecutionConflict
from harness.template_classification import _ordinary


def record_not_started(execution, token, *, stage):
    if stage not in {"worker-spawn", "native-setup", "native-spawn", "reconnect"}:
        raise ValueError("unsupported no-native disposition")
    receipt = {
        "kind": "native-not-started-v1",
        "execution_id": execution["id"],
        "epoch_id": execution["epoch_id"],
        "authority_generation": execution["authority_generation"],
        "token": token,
        "stage": stage,
        "native_started": False,
    }
    path = Path(execution["handoff_path"]).parent / "settlements" / ("unspawned-" + token + ".json")
    atomic_file(path, canonical(receipt).encode())


def _not_started(execution, attempt, *, required_stage=None):
    path = (
        Path(execution["handoff_path"]).parent
        / "settlements"
        / ("unspawned-" + attempt["token"] + ".json")
    )
    try:
        raw, mode = _ordinary(path, 65536)
    except FileNotFoundError:
        return False
    value = json.loads(raw)
    if not isinstance(value, dict):
        return False
    return (
        mode == 0o600
        and value.get("native_started") is False
        and type(value.get("authority_generation")) is int
        and (required_stage is None or value.get("stage") == required_stage)
        and value
        == {
            "kind": "native-not-started-v1",
            "execution_id": execution["id"],
            "epoch_id": execution["epoch_id"],
            "authority_generation": execution["authority_generation"],
            "token": attempt["token"],
            "stage": value.get("stage"),
            "native_started": False,
        }
        and value["stage"] in {"worker-spawn", "native-setup", "native-spawn", "reconnect"}
        and (value["stage"] == "reconnect") == (attempt["mode"] == "reconnect")
    )


def observe_recovery_settlement(controller, execution):
    """Caller holds the worker and exclusive epoch locks; issuer adds fresh kernel census."""
    from harness.execution_epochs import get_epoch
    from harness.execution_runtime_recovery import _settled
    from harness.execution_settlement import closed_context
    from harness.execution_store import process_alive

    _settled(controller, execution)
    attempts, obligations = [], []
    for row in controller.conn.execute(
        "SELECT * FROM execution_attempts WHERE execution_id=? ORDER BY rowid", (execution["id"],)
    ):
        attempt = dict(row)
        epoch = get_epoch(controller.conn, execution["id"], attempt["epoch_id"])
        identity = epoch["identity"]
        original = dict(
            execution,
            handoff_path=identity["handoff_path"],
            epoch_id=epoch["epoch_id"],
            authority_generation=epoch["authority_generation"],
        )
        disposition = "unknown"
        if process_alive(attempt["child_pid"]):
            disposition = "live"
        elif attempt["child_pid"] is not None:
            if attempt["status"] in {"Exited", "Reconciled"} and closed_context(original, attempt):
                disposition = "closed"
        elif attempt["status"] in {"PreSpawnFailed", "Exited", "Reconciled"} and _not_started(
            original, attempt
        ):
            disposition = "proven-not-started"
        if disposition in {"unknown", "live"}:
            obligations.append("native attempt " + attempt["id"] + " is " + disposition)
        attempts.append(
            {
                "attempt_id": attempt["id"],
                "token": attempt["token"],
                "epoch_id": attempt["epoch_id"],
                "child_pid": attempt["child_pid"],
                "status": attempt["status"],
                "disposition": disposition,
            }
        )
    return {
        "kind": "planning-recovery-settlement-v1",
        "execution_id": execution["id"],
        "epoch_id": execution["epoch_id"],
        "authority_generation": execution["authority_generation"],
        "attempts": attempts,
        "browser_operations": "settled",
        "delivery_operations": "settled",
        "census": "issuer-required",
        "coverage": "incomplete" if obligations else "native-contexts-complete",
        "obligations": obligations,
    }


def require_recovery_settlement(controller, execution):
    observation = observe_recovery_settlement(controller, execution)
    if observation["obligations"]:
        raise ExecutionConflict(
            "planning recovery requires settlement: " + "; ".join(observation["obligations"])
        )
    raw = canonical(observation).encode()
    path = (
        Path(execution["handoff_path"]).parent
        / "settlements"
        / (digest(raw).split(":")[1] + ".json")
    )
    atomic_file(path, raw)
    return {"stored_path": str(path), "sha256": digest(raw).split(":")[1]}
