"""Explicit recovery never infers absence of execution from a stale PID."""

from harness.db import transaction, utcnow
from harness.execution_authority import require_execution_authority
from harness.execution_models import TERMINAL, ExecutionConflict
from harness.execution_projection import execution_view
from harness.execution_store import get_execution, process_alive
from harness.execution_worker import launch_worker, worker_lock


def reconnect_execution(controller, execution_id):
    conn = controller.conn
    saved = get_execution(conn, execution_id)
    if saved["status"] in TERMINAL:
        return execution_view(conn, execution_id)
    require_execution_authority(controller, execution_id)
    try:
        launch_worker(conn, execution_id, mode="reconnect")
    except ExecutionConflict:
        pass  # a live owner already polls; reconnect only reads its persisted projection
    return execution_view(conn, execution_id)


def resume_execution(controller, execution_id):
    conn = controller.conn
    saved = get_execution(conn, execution_id)
    if saved["status"] in TERMINAL:
        return execution_view(conn, execution_id)
    require_execution_authority(controller, execution_id, operation="resume")
    if not saved["native_id"]:
        raise ExecutionConflict("native identity unknown; reconcile uncertain spawn before retry")
    # The worker lock proves no competing supervisor; a known child must also be settled.
    rows = conn.execute(
        "SELECT child_pid,status FROM execution_attempts WHERE execution_id=?", (execution_id,)
    ).fetchall()
    for row in rows:
        if row["child_pid"] and (
            process_alive(row["child_pid"]) or row["status"] not in {"Exited", "Reconciled"}
        ):
            raise ExecutionConflict(
                "recorded native process is live or its outcome requires explicit reconciliation"
            )
    launch_worker(conn, execution_id, mode="resume")
    return execution_view(conn, execution_id)


def reconcile_execution(controller, execution_id, *, outcome, evidence, native_id=None):
    if (
        outcome not in {"not-started", "exited"}
        or not isinstance(evidence, str)
        or len(evidence.strip()) < 20
    ):
        raise ValueError(
            "reconciliation requires explicit outcome and substantive inspected evidence"
        )
    conn = controller.conn
    saved = get_execution(conn, execution_id)
    if saved["status"] in TERMINAL:
        return execution_view(conn, execution_id)
    require_execution_authority(controller, execution_id)
    with worker_lock(saved), transaction(conn):
        rows = conn.execute(
            "SELECT * FROM execution_attempts WHERE execution_id=? ORDER BY rowid", (execution_id,)
        ).fetchall()
        if any(process_alive(row["child_pid"]) for row in rows):
            raise ExecutionConflict(
                "recorded native child is still alive; reconciliation cannot resume competing work"
            )
        if outcome == "not-started" and any(
            row["status"] not in {"Reserved", "PreSpawnFailed", "Reconciled"} for row in rows
        ):
            raise ExecutionConflict(
                "persisted Launching/Running cannot be declared unstarted; "
                "inspect native identity/outcome"
            )
        if native_id is not None:
            import uuid

            try:
                native_id = str(uuid.UUID(native_id))
            except (ValueError, TypeError, AttributeError) as error:
                raise ValueError("reconciliation requires an exact native session UUID") from error
            if outcome != "exited" or saved["native_id"] not in (None, native_id):
                raise ExecutionConflict(
                    "reconciliation cannot retarget native conversation identity"
                )
            conn.execute("UPDATE executions SET native_id=? WHERE id=?", (native_id, execution_id))
            saved["native_id"] = native_id
        if outcome == "exited" and not saved["native_id"]:
            raise ExecutionConflict(
                "uncertain unknown native conversation cannot be safely resumed; "
                "supply its inspected exact native ID"
            )
        conn.execute(
            "UPDATE execution_attempts SET status='Reconciled',updated_at=? WHERE execution_id=?",
            (utcnow(), execution_id),
        )
        status = "Reserved" if outcome == "not-started" else "Interrupted"
        conn.execute(
            "UPDATE executions SET status=?,worker_token=NULL,error=?,updated_at=? WHERE id=?",
            (status, "Reconciled: " + evidence, utcnow(), execution_id),
        )
        controller.workspace(saved["task_id"]).record_event(
            "execution.reconciled",
            {
                "execution_id": execution_id,
                "outcome": outcome,
                "evidence": evidence,
                "native_id": saved["native_id"],
            },
        )
    return execution_view(conn, execution_id)
