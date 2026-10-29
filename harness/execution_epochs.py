"""Explicit execution epoch identities; successor mutations never default to current."""

import json

from harness.execution_models import ExecutionConflict
from harness.execution_store import get_execution

LEGACY = "legacy-0"


def get_epoch(conn, execution_id, epoch_id=None):
    execution = get_execution(conn, execution_id)
    epoch_id = execution["epoch_id"] if epoch_id is None else epoch_id
    row = conn.execute(
        "SELECT * FROM execution_epochs WHERE execution_id=? AND epoch_id=?",
        (execution_id, epoch_id),
    ).fetchone()
    if row is None:
        raise ExecutionConflict("unknown execution epoch")
    result = dict(row)
    result["identity"] = json.loads(result["identity"])
    return result


def require_active_epoch(
    conn,
    execution_id,
    *,
    expected_epoch_id=None,
    expected_authority_generation=None,
    mutation=False,
    protected=True,
):
    execution = get_execution(conn, execution_id)
    current = (execution["epoch_id"], execution["authority_generation"])
    supplied = (expected_epoch_id, expected_authority_generation)
    if supplied != (None, None):
        if type(expected_authority_generation) is not int or supplied != current:
            raise ExecutionConflict(
                "stale_epoch: execution authority changed; refresh recorded status"
            )
    elif mutation and current != (LEGACY, 0):
        raise ExecutionConflict("stale_epoch: explicit epoch and authority generation required")
    if current != (LEGACY, 0) and protected:
        epoch = get_epoch(conn, execution_id)
        if epoch["state"] != "ACTIVE":
            raise ExecutionConflict("runtime transition requires recovery before implementation")
        from harness.execution_runtime_recovery import verify_selected_authority

        verify_selected_authority(execution, epoch)
    return execution


def selected_epoch(conn, execution_id, epoch_id=None):
    execution = get_execution(conn, execution_id)
    selected = execution["epoch_id"] if epoch_id is None else epoch_id
    if selected != execution["epoch_id"]:
        get_epoch(conn, execution_id, selected)
    return selected


def history_epochs(conn, execution_id):
    return [
        dict(row)
        for row in conn.execute(
            "SELECT epoch_id,authority_generation,state,created_at FROM execution_epochs "
            "WHERE execution_id=? ORDER BY authority_generation",
            (execution_id,),
        )
    ]
