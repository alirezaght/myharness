"""Local persistence fixtures; cloned facts are not producer integration evidence."""

import secrets

from harness.reliability import store


def seed(observed, count=1, *, created_at="2026-01-01T00:00:00+00:00"):
    operation, principal, controller = observed
    conn = controller.conn
    if not conn.execute("SELECT 1 FROM reliability_incidents").fetchone():
        oid = store.begin(operation, principal, "read_file")
        # Simulate the prior immutable writer's eight-field R1 publication, not the new hook.
        conn.execute(
            "UPDATE reliability_observations SET state='denied',code='AUTHORITY_REJECTED',"
            "finished_at=started_at WHERE observation_id=?",
            (oid,),
        )
        conn.execute(
            "INSERT INTO reliability_incidents VALUES (?,?,1,'operation_denied','open',?,?,1)",
            (secrets.token_hex(16), oid, created_at, created_at),
        )
    template = dict(conn.execute("SELECT * FROM reliability_observations LIMIT 1").fetchone())
    ids = []
    for _ in range(count):
        iid, oid, aid = (secrets.token_hex(16) for _ in range(3))
        row = template | {"observation_id": oid, "source_id": aid}
        conn.execute(
            "INSERT INTO operation_audit VALUES (?,?,'{}','guarded','{}',?)",
            (aid, operation.execution_id, created_at),
        )
        conn.execute(
            "INSERT INTO reliability_observations("
            + ",".join(row)
            + ") VALUES ("
            + ",".join("?" for _ in row)
            + ")",
            tuple(row.values()),
        )
        conn.execute(
            "INSERT INTO reliability_incidents VALUES (?,?,1,'operation_denied','open',?,?,1)",
            (iid, oid, created_at, created_at),
        )
        ids.append(iid)
    return ids


def selected(observed):
    operation, _, controller = observed
    task = controller.conn.execute(
        "SELECT task_id FROM executions WHERE id=?", (operation.execution_id,)
    ).fetchone()[0]
    return {"task": task, "execution": operation.execution_id, "epoch": operation.epoch_id}
