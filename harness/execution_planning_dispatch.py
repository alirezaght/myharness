"""Durable progress for one owned fresh-native dispatch; retries never mint another."""

import json

from harness.db import transaction, utcnow
from harness.execution_contract import canonical
from harness.execution_models import ExecutionConflict
from harness.execution_store import get_execution


def observe_dispatch(conn, execution_id, token, state, *, native_id=None):
    if state not in {"launching", "observed"}:
        raise ValueError("unsupported fresh dispatch observation")
    with transaction(conn):
        execution = get_execution(conn, execution_id)
        if execution["worker_token"] != token:
            raise ExecutionConflict("stale worker cannot publish fresh dispatch progress")
        for row in conn.execute(
            "SELECT request_id,activation FROM execution_runtime_transitions "
            "WHERE execution_id=? AND state='ACTIVE' AND activation IS NOT NULL",
            (execution_id,),
        ):
            value = json.loads(row["activation"])
            dispatch = value.get("fresh_dispatch")
            if not dispatch or dispatch["token"] != token:
                continue
            if value["epoch_id"] != execution["epoch_id"]:
                raise ExecutionConflict("fresh dispatch belongs to a historical epoch")
            if dispatch["state"] == "observed":
                if state == "observed" and dispatch.get("native_id") != native_id:
                    raise ExecutionConflict("fresh dispatch native identity changed")
                return
            dispatch["state"] = state
            if state == "observed":
                if not native_id or native_id != execution["native_id"]:
                    raise ExecutionConflict("fresh dispatch lacks observed native identity")
                dispatch["native_id"] = native_id
            conn.execute(
                "UPDATE execution_runtime_transitions SET activation=?,updated_at=? "
                "WHERE request_id=?",
                (canonical(value), utcnow(), row["request_id"]),
            )
            return


def dispatch_view(conn, execution_id, activation):
    dispatch = activation.get("fresh_dispatch")
    if not dispatch:
        return None
    row = conn.execute(
        "SELECT token,epoch_id,status,child_pid,native_id FROM execution_attempts "
        "WHERE execution_id=? AND id=?",
        (execution_id, dispatch["attempt_id"]),
    ).fetchone()
    if not row or row["token"] != dispatch["token"] or row["epoch_id"] != activation["epoch_id"]:
        raise ExecutionConflict("fresh dispatch reservation differs from its owned attempt")
    result = dict(dispatch, attempt_status=row["status"], child_pid=row["child_pid"])
    if row["status"] == "PreSpawnFailed":
        result["state"] = "pre-spawn-failed"
    elif row["status"] in {"Exited", "Reconciled"} and dispatch["state"] != "observed":
        result["state"] = "uncertain"
    return result
