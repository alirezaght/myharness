"""Only an exact supervisor-admitted acknowledgement crosses terminal boundary."""

from harness.execution_contract import canonical, digest, loads


def acknowledge_correction(conn, execution, payload):
    row = conn.execute(
        "SELECT * FROM delivery_corrections WHERE id=? AND execution_id=?",
        (payload.get("request_id"), execution["id"]),
    ).fetchone()
    if not row or row["state"] != "Admitted":
        raise ValueError("unadmitted or duplicate terminal correction acknowledgement")
    request = loads(row["request"], max_bytes=4194304)
    expected = {
        "request_id": request["id"],
        "request_digest": digest(request),
        "generation": request["generation"],
        "prior_completion_digest": request["prior_completion_digest"],
    }
    if payload != expected or digest(execution["result"]) != request["prior_completion_digest"]:
        raise ValueError("correction acknowledgement does not match retained completion/request")
    delivery = conn.execute(
        "SELECT generation FROM deliveries WHERE execution_id=?", (execution["id"],)
    ).fetchone()
    if delivery["generation"] + 1 != request["generation"] or execution["status"] != "Completed":
        raise ValueError("correction acknowledgement generation/status mismatch")
    conn.execute(
        "INSERT INTO execution_completion_history VALUES (?,?,?)",
        (execution["id"], delivery["generation"], canonical(execution["result"])),
    )
    conn.execute(
        "UPDATE executions SET status='RecoveryRequired',result=NULL,error=NULL WHERE id=?",
        (execution["id"],),
    )
    conn.execute(
        "UPDATE deliveries SET generation=?,status='Correcting',snapshot='{}' WHERE execution_id=?",
        (request["generation"], execution["id"]),
    )
    conn.execute(
        "UPDATE delivery_corrections SET state='Acknowledged',acknowledgement=? WHERE id=?",
        (canonical(payload), request["id"]),
    )
