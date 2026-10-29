"""Immutable browser plans and non-reclaimable one-shot dispatch claims."""

import uuid

from harness.browser_models import validate_plan
from harness.db import transaction, utcnow
from harness.execution_contract import canonical, digest, loads
from harness.execution_logs import redact_value

ACTIVE = {"Dispatching", "Running", "Uncertain"}


def browser_event(conn, execution_id, kind, payload):
    from harness.controller import HarnessController
    from harness.execution_store import get_execution

    controller = HarnessController(conn)
    task_id = get_execution(conn, execution_id)["task_id"]
    controller.workspace(task_id).record_event(
        kind, redact_value({"execution_id": execution_id, **payload})
    )


def get_plan(conn, execution_id, plan_id):
    row = conn.execute(
        "SELECT document FROM browser_plans WHERE id=? AND execution_id=?", (plan_id, execution_id)
    ).fetchone()
    if row is None:
        raise LookupError("browser plan not found")
    plan = validate_plan(loads(row["document"], max_bytes=4 * 1024 * 1024))
    if digest(plan) != plan_id:
        raise ValueError("browser plan hash mismatch")
    return plan


def approval(conn, execution_id, approval_id):
    row = conn.execute(
        "SELECT * FROM browser_approvals WHERE id=? AND execution_id=?", (approval_id, execution_id)
    ).fetchone()
    if row is None:
        raise LookupError("browser approval not found")
    value = dict(row)
    value["binding"] = loads(value["binding"], max_bytes=4 * 1024 * 1024)
    expected = {
        k: value[k] for k in ("execution_id", "plan_id", "request_id", "attempt", "binding")
    }
    if digest(expected) != value["id"]:
        raise ValueError("browser approval content changed")
    return value


def run_record(conn, run_id):
    row = conn.execute("SELECT * FROM browser_runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise LookupError("browser run not found")
    result = dict(row)
    result.pop("owner_token")
    result["final_observation"] = (
        loads(result["final_observation"]) if result["final_observation"] else None
    )
    result["results"] = [
        loads(r["result"])
        for r in conn.execute(
            "SELECT result FROM browser_results WHERE run_id=? ORDER BY rowid", (run_id,)
        )
    ]
    result["investigations"] = [
        loads(r["record"])
        for r in conn.execute(
            "SELECT record FROM browser_investigations WHERE run_id=? ORDER BY rowid", (run_id,)
        )
    ]
    return result


def existing_run(conn, approval_id):
    row = conn.execute("SELECT id FROM browser_runs WHERE approval_id=?", (approval_id,)).fetchone()
    return run_record(conn, row["id"]) if row else None


def claim_run(controller, approval_id):
    from harness.browser_plan import local_binding
    from harness.execution_authority import require_execution_authority

    conn = controller.conn
    row = conn.execute(
        "SELECT execution_id FROM browser_approvals WHERE id=?", (approval_id,)
    ).fetchone()
    if not row:
        raise LookupError("browser approval not found")
    eid = row["execution_id"]
    from harness.execution_epoch_lock import epoch_action_lock
    from harness.execution_store import get_execution

    with epoch_action_lock(get_execution(conn, eid)), transaction(conn):
        prior = existing_run(conn, approval_id)
        if prior:
            if prior["state"] in ACTIVE:
                raise ValueError("browser dispatch unresolved; reconcile without replay")
            return prior, False
        saved = approval(conn, eid, approval_id)
        plan = get_plan(conn, eid, saved["plan_id"])
        require_execution_authority(controller, eid, operation="delivery")
        binding, _ = local_binding(controller, eid, plan["target"]["mapping"])
        if binding != saved["binding"] or binding != plan["binding"]:
            raise ValueError("browser approval binding stale")
        if conn.execute(
            "SELECT 1 FROM browser_runs "
            "WHERE execution_id=? "
            "AND state IN ('Dispatching','Running','Uncertain')",
            (eid,),
        ).fetchone():
            raise ValueError("previous browser dispatch unresolved")
        rid, token = uuid.uuid4().hex, uuid.uuid4().hex
        conn.execute(
            "INSERT INTO browser_runs VALUES (?,?,?,?,?,?,?,?,?)",
            (rid, eid, approval_id, "Dispatching", token, utcnow(), None, None, None),
        )
        browser_event(conn, eid, "browser.dispatching", {"run_id": rid, "approval_id": approval_id})
        result = run_record(conn, rid)
        result["owner_token"] = token
        return result, True


def save_criterion(conn, run_id, result):
    result = redact_value(result)
    raw = canonical(result)
    with transaction(conn):
        prior = conn.execute(
            "SELECT result FROM browser_results WHERE run_id=? AND criterion_id=?",
            (run_id, result["criterion_id"]),
        ).fetchone()
        if prior:
            if prior["result"] != raw:
                raise ValueError("immutable browser result conflict")
            return
        state = conn.execute("SELECT state FROM browser_runs WHERE id=?", (run_id,)).fetchone()
        if not state or state["state"] not in ACTIVE:
            raise ValueError("terminal browser result inventory is immutable")
        conn.execute(
            "INSERT INTO browser_results VALUES (?,?,?)", (run_id, result["criterion_id"], raw)
        )
        eid = conn.execute(
            "SELECT execution_id FROM browser_runs WHERE id=?", (run_id,)
        ).fetchone()["execution_id"]
        browser_event(
            conn,
            eid,
            "browser.criterion",
            {"run_id": run_id, "criterion_id": result["criterion_id"], "status": result["status"]},
        )
        if result["status"] == "failed":
            record = result | {"status": "investigation_required", "architecture_authorized": False}
            conn.execute(
                "INSERT INTO browser_investigations VALUES (?,?,?,?)",
                (
                    digest([run_id, result["criterion_id"]]),
                    run_id,
                    result["criterion_id"],
                    canonical(record),
                ),
            )


def browser_view(conn, execution_id):
    plans = [
        {"plan_id": r["id"], "plan": loads(r["document"], max_bytes=4 * 1024 * 1024)}
        for r in conn.execute(
            "SELECT * FROM browser_plans WHERE execution_id=? ORDER BY rowid", (execution_id,)
        )
    ]
    approvals = [
        dict(r)
        for r in conn.execute(
            "SELECT id,plan_id,request_id,attempt,created_at FROM browser_approvals "
            "WHERE execution_id=? ORDER BY rowid",
            (execution_id,),
        )
    ]
    runs = [
        run_record(conn, r["id"])
        for r in conn.execute(
            "SELECT id FROM browser_runs WHERE execution_id=? ORDER BY rowid", (execution_id,)
        )
    ]
    capability = conn.execute(
        "SELECT observation FROM browser_capabilities "
        "WHERE execution_id=? "
        "ORDER BY rowid DESC LIMIT 1",
        (execution_id,),
    ).fetchone()
    latest = runs[-1] if runs else None
    currency, reason = "unavailable", "No completed browser observation"
    if latest:
        final = latest.get("final_observation") or {}
        currency, reason = (
            final.get("currency", "unavailable"),
            final.get("reason", "Run unresolved"),
        )
        try:
            from harness.browser_artifacts import verify_run_artifacts
            from harness.browser_plan import local_binding

            saved = approval(conn, execution_id, latest["approval_id"])
            plan = get_plan(conn, execution_id, saved["plan_id"])

            class Context:
                pass

            context = Context()
            context.conn = conn
            from harness.controller import HarnessController
            from harness.execution_authority import require_execution_authority

            try:
                require_execution_authority(
                    HarnessController(conn), execution_id, operation="delivery"
                )
            except (ValueError, OSError, LookupError):
                currency, reason = (
                    "stale",
                    "Original specification authority superseded or unavailable",
                )
            if (
                local_binding(context, execution_id, plan["target"]["mapping"])[0]
                != saved["binding"]
            ):
                currency, reason = "stale", "Local specification or delivery binding changed"
            if plans and saved["plan_id"] != plans[-1]["plan_id"]:
                currency, reason = "stale", "A newer browser plan is proposed"
            verify_run_artifacts(conn, latest)
        except (ValueError, OSError, LookupError):
            currency, reason = "unavailable", "Required evidence or authority unavailable"
    selected = (
        [c for c in plans[-1]["plan"]["criteria"] if c["classification"] == "selected"]
        if plans
        else []
    )
    return {
        "schema_version": 1,
        "coverage": {
            kind: sum(c["classification"] == kind for c in plans[-1]["plan"]["criteria"])
            if plans
            else 0
            for kind in ("selected", "excluded", "unresolved")
        },
        "status": latest["state"] if latest else ("proposed" if plans else "not_run"),
        "currency": currency,
        "reason": reason,
        "observed_at": (latest or {}).get("finished_at"),
        "plan": plans[-1] if plans else None,
        "plans": plans,
        "approvals": approvals,
        "runs": runs,
        "results": latest["results"] if latest else [],
        "investigations": latest["investigations"] if latest else [],
        "capability": loads(capability["observation"]) if capability else None,
        "evidence_origin": sorted({r.get("origin", "unavailable") for r in latest["results"]})
        if latest
        else [],
        "blockers": ([] if selected else ["No browser criteria selected"])
        + ([reason] if currency != "current" else []),
        "limitations": plans[-1]["plan"]["limitations"] if plans else [],
    }
