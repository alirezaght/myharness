"""Small explicit persistence seams; callers own their transaction boundary."""

import json

from harness.db import utcnow
from harness.execution_contract import canonical
from harness.execution_models import ExecutionConflict


def get_execution(conn, execution_id):
    row = conn.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
    if row is None:
        raise LookupError("execution not found: " + str(execution_id))
    result = dict(row)
    for key in ("binding", "result"):
        result[key] = json.loads(result[key]) if result[key] else None
    legacy = conn.execute(
        "SELECT identity FROM execution_epochs WHERE execution_id=? AND epoch_id='legacy-0'",
        (execution_id,),
    ).fetchone()
    if legacy:
        from pathlib import Path

        result["authority_directory"] = str(
            Path(json.loads(legacy["identity"])["handoff_path"]).parent
        )
    return result


def latest_execution(conn, task_id):
    row = conn.execute(
        "SELECT id FROM executions WHERE task_id=? ORDER BY rowid DESC LIMIT 1", (task_id,)
    ).fetchone()
    return get_execution(conn, row["id"]) if row else None


def reserve_execution(
    conn,
    *,
    execution_id,
    task_id,
    approval_id,
    host,
    binding,
    handoff_path,
    handoff_hash,
    al_session_id,
):
    existing = conn.execute(
        "SELECT id FROM executions WHERE approval_id=?", (approval_id,)
    ).fetchone()
    if existing:
        saved = get_execution(conn, existing["id"])
        if saved["host"] != host or saved["binding"] != binding:
            raise ExecutionConflict("approval already reserved with another host/binding")
        return saved, False
    now = utcnow()
    conn.execute(
        "INSERT INTO executions "
        "(id,task_id,approval_id,host,binding,handoff_path,handoff_hash,status,"
        "al_session_id,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            execution_id,
            task_id,
            approval_id,
            host,
            canonical(binding),
            str(handoff_path),
            handoff_hash,
            "Reserved",
            al_session_id,
            now,
            now,
        ),
    )
    conn.execute(
        (
            "INSERT INTO "
            "execution_epochs(execution_id,epoch_id,authority_generation,state,identity,cre"
            "ated_at) VALUES (?,'legacy-0',0,'ACTIVE',?,?)"
        ),
        (
            execution_id,
            canonical(
                {
                    "al_session_id": al_session_id,
                    "al_run_id": None,
                    "handoff_path": str(handoff_path),
                    "handoff_hash": handoff_hash,
                }
            ),
            now,
        ),
    )
    return get_execution(conn, execution_id), True


def bind_host(conn, execution_id, native_id, token):
    row = get_execution(conn, execution_id)
    if row["worker_token"] != token or row["native_id"] not in (None, native_id):
        raise ExecutionConflict("worker/native identity conflict")
    conn.execute(
        "UPDATE executions SET native_id=?,updated_at=? WHERE id=? AND worker_token=?",
        (native_id, utcnow(), execution_id, token),
    )


def bind_al_run(conn, execution_id, session_id, run_id):
    row = get_execution(conn, execution_id)
    if row["al_session_id"] != session_id or row["al_run_id"] not in (None, run_id):
        raise ExecutionConflict("AL run identity conflict")
    conn.execute("UPDATE executions SET al_run_id=? WHERE id=?", (run_id, execution_id))
    epoch = conn.execute(
        "SELECT identity FROM execution_epochs WHERE execution_id=? AND epoch_id=?",
        (execution_id, row["epoch_id"]),
    ).fetchone()
    if epoch:
        identity = json.loads(epoch["identity"])
        if identity.get("al_run_id") is None:
            identity["al_run_id"] = run_id
            conn.execute(
                "UPDATE execution_epochs SET identity=? WHERE execution_id=? AND epoch_id=?",
                (canonical(identity), execution_id, row["epoch_id"]),
            )
        elif identity["al_run_id"] != run_id:
            raise ExecutionConflict("epoch AL source identity conflict")


def update_owned(conn, execution_id, token, **fields):
    allowed = {"status", "heartbeat", "source_contact", "error", "result"}
    if not fields or not fields.keys() <= allowed:
        raise ValueError("invalid worker update")
    if fields.get("error"):
        from harness.execution_logs import redact

        fields["error"] = redact(fields["error"])
    fields["updated_at"] = utcnow()
    if "result" in fields and fields["result"] is not None:
        fields["result"] = canonical(fields["result"])
    sql = (
        "UPDATE executions SET "
        + ",".join(k + "=?" for k in fields)
        + " WHERE id=? AND worker_token=?"
    )
    if conn.execute(sql, (*fields.values(), execution_id, token)).rowcount != 1:
        raise ExecutionConflict("worker ownership was lost")


def entity(conn, execution_id, family, identity, *, epoch_id=None):
    from harness.execution_epochs import selected_epoch

    epoch_id = selected_epoch(conn, execution_id, epoch_id)
    row = conn.execute(
        (
            "SELECT data FROM execution_entities WHERE execution_id=? AND family=? AND "
            "id=? AND epoch_id=?"
        ),
        (execution_id, family, identity, epoch_id),
    ).fetchone()
    return json.loads(row["data"]) if row else None


def put_entity(conn, execution_id, family, identity, value):
    from harness.execution_epochs import selected_epoch

    epoch_id = selected_epoch(conn, execution_id)
    conn.execute(
        "INSERT INTO execution_entities(execution_id,epoch_id,family,id,data) VALUES (?,?,?,?,?) "
        "ON CONFLICT(execution_id,epoch_id,family,id) DO UPDATE SET data=excluded.data",
        (execution_id, epoch_id, family, identity, canonical(value)),
    )


def entities(conn, execution_id, family, *, epoch_id=None):
    from harness.execution_epochs import selected_epoch

    epoch_id = selected_epoch(conn, execution_id, epoch_id)
    return [
        json.loads(row["data"])
        for row in conn.execute(
            (
                "SELECT data FROM execution_entities WHERE execution_id=? AND family=? AND "
                "epoch_id=? ORDER BY id"
            ),
            (execution_id, family, epoch_id),
        )
    ]


def pending_deliveries(conn, execution_id):
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM execution_decisions "
            "WHERE execution_id=? AND epoch_id=(SELECT epoch_id FROM executions WHERE id=?) "
            "AND state IN ('Ready','Delivered') ORDER BY rowid",
            (execution_id, execution_id),
        )
    ]


def process_alive(pid):
    import os

    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
