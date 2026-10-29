"""Durable delivery state and non-reclaimable external operation intents."""

from harness.db import transaction, utcnow
from harness.delivery_models import DeliveryConfig
from harness.execution_contract import canonical, digest, loads
from harness.execution_epoch_lock import epoch_action_lock
from harness.execution_logs import redact_value
from harness.execution_store import get_execution


def get_delivery(conn, execution_id):
    row = conn.execute("SELECT * FROM deliveries WHERE execution_id=?", (execution_id,)).fetchone()
    if row is None:
        raise LookupError("delivery not configured")
    value = dict(row)
    for key in ("binding", "config", "mr", "snapshot"):
        value[key] = loads(value[key]) if value[key] else None
    return value


def require_delivery_epoch(conn, delivery):
    from harness.execution_epochs import require_active_epoch

    current = require_active_epoch(conn, delivery["execution_id"])
    if delivery["epoch_id"] != current["epoch_id"]:
        raise ValueError(
            "stale_epoch: delivery belongs to a historical runtime; inspect its recorded epoch"
        )
    return current


def configure_delivery(controller, execution_id, config):
    from harness.delivery_providers import delivery_client
    from harness.execution_authority import require_execution_authority

    require_execution_authority(controller, execution_id, operation="delivery")
    config = (
        config.to_dict()
        if isinstance(config, DeliveryConfig)
        else DeliveryConfig(**config).to_dict()
    )
    delivery_client(config)
    execution = get_execution(controller.conn, execution_id)
    with epoch_action_lock(execution), transaction(controller.conn):
        from harness.execution_epochs import require_active_epoch

        execution = require_active_epoch(
            controller.conn,
            execution_id,
            expected_epoch_id=execution["epoch_id"],
            expected_authority_generation=execution["authority_generation"],
        )
        require_execution_authority(controller, execution_id, operation="delivery")
        prior = controller.conn.execute(
            "SELECT 1 FROM deliveries WHERE execution_id=?", (execution_id,)
        ).fetchone()
        if prior:
            require_delivery_epoch(controller.conn, get_delivery(controller.conn, execution_id))
            if get_delivery(controller.conn, execution_id)["config"] != config:
                raise ValueError("delivery configuration is immutable; reconcile existing delivery")
        else:
            controller.conn.execute(
                (
                    "INSERT INTO "
                    "deliveries(execution_id,binding,config,updated_at,epoch_id) VALUES "
                    "(?,?,?,?,?)"
                ),
                (
                    execution_id,
                    canonical(execution["binding"]),
                    canonical(config),
                    utcnow(),
                    execution["epoch_id"],
                ),
            )
    return delivery_view(controller.conn, execution_id)


def claim_operation(conn, delivery, kind, candidate, request):
    identity = {
        "execution_id": delivery["execution_id"],
        "generation": delivery["generation"],
        "kind": kind,
        "candidate": candidate,
        "binding": delivery["binding"],
        "request": request,
    }
    operation_id = digest(identity)
    with epoch_action_lock(get_execution(conn, delivery["execution_id"])), transaction(conn):
        require_delivery_epoch(conn, delivery)
        row = conn.execute(
            "SELECT * FROM delivery_operations WHERE id=?", (operation_id,)
        ).fetchone()
        if row:
            if row["state"] == "Confirmed":
                return operation_id, loads(row["result"])
            raise ValueError("external operation unresolved; reconcile before retry")
        conn.execute(
            (
                "INSERT INTO "
                "delivery_operations(id,execution_id,generation,kind,candidate_sha,request,"
                "state,result,updated_at,epoch_id) VALUES (?,?,?,?,?,?,?,?,?,?)"
            ),
            (
                operation_id,
                delivery["execution_id"],
                delivery["generation"],
                kind,
                candidate,
                canonical(request),
                "Dispatching",
                None,
                utcnow(),
                delivery["epoch_id"],
            ),
        )
    return operation_id, None


def finish_operation(conn, operation_id, state, result=None):
    with transaction(conn):
        row = conn.execute(
            "SELECT execution_id,epoch_id FROM delivery_operations WHERE id=?", (operation_id,)
        ).fetchone()
        if not row:
            raise LookupError("delivery operation not found")
        require_delivery_epoch(conn, dict(row))
        conn.execute(
            "UPDATE delivery_operations SET state=?,result=?,updated_at=? WHERE id=?",
            (state, canonical(redact_value(result)), utcnow(), operation_id),
        )


def save_snapshot(
    controller, execution_id, snapshot, status, *, expected_candidate=None, expected_generation=None
):
    snapshot = redact_value(snapshot)
    with transaction(controller.conn):
        prior = get_delivery(controller.conn, execution_id)
        require_delivery_epoch(controller.conn, prior)
        if (expected_candidate is not None and prior["candidate_sha"] != expected_candidate) or (
            expected_generation is not None and prior["generation"] != expected_generation
        ):
            raise ValueError("delivery candidate/generation moved during observation")
        controller.conn.execute(
            "UPDATE deliveries SET snapshot=?,status=?,updated_at=? WHERE execution_id=?",
            (canonical(snapshot), status, utcnow(), execution_id),
        )
        if prior["snapshot"] != snapshot or prior["status"] != status:
            task = get_execution(controller.conn, execution_id)["task_id"]
            controller.workspace(task).record_event(
                "delivery.updated",
                {"execution_id": execution_id, "status": status, "snapshot": snapshot},
            )


def delivery_view(conn, execution_id):
    from harness.browser_store import browser_view
    from harness.reporting import assemble_report

    row = get_delivery(conn, execution_id)
    snapshot = row["snapshot"] or {}
    view = {
        "execution_id": execution_id,
        "provider": row["config"].get("provider", "gitlab"),
        "generation": row["generation"],
        "status": row["status"],
        "candidate_sha": row["candidate_sha"],
        "mr": row["mr"],
        "pipeline": snapshot.get("pipeline"),
        "jobs": snapshot.get("jobs", []),
        "test_report": snapshot.get("test_report"),
        "staging": snapshot.get("staging", {"ready": False, "status": "unknown"}),
        "browser_validation": browser_view(conn, execution_id),
        "retries": {"used": row["retries"], "limit": row["config"]["retry_limit"]},
        "blockers": snapshot.get("blockers", []),
        "observed_at": snapshot.get("observed_at"),
    }

    summary = assemble_report(conn, execution_id, view)
    summary["browser"] = {
        key: view["browser_validation"][key]
        for key in ("status", "currency", "reason", "observed_at", "evidence_origin", "blockers")
    }
    view["completion_summary"] = summary
    return view
