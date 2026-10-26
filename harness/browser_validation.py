"""Separate browser approval and one-shot execution with durable recovery receipts."""

from harness.browser_artifacts import (
    criterion_name,
    publish,
    read_receipt,
    receipt_binding,
    run_root,
    verify_evidence,
    verify_run_artifacts,
)
from harness.browser_capability import probe_browser
from harness.browser_driver import BrowserDriver
from harness.browser_models import evidence_origin
from harness.browser_plan import current_target, local_binding
from harness.browser_store import (
    ACTIVE,
    approval,
    browser_event,
    browser_view,
    claim_run,
    existing_run,
    get_plan,
    run_record,
    save_criterion,
)
from harness.db import transaction, utcnow
from harness.execution_authority import require_execution_authority
from harness.execution_contract import canonical, digest, identity
from harness.execution_logs import redact, redact_value


def _ready(plan):
    if plan["blockers"] or not plan["target"]["ready"] or not plan["target"]["url"]:
        raise ValueError("browser plan prerequisites unavailable")
    if any(c["classification"] == "unresolved" for c in plan["criteria"]):
        raise ValueError("unresolved browser criteria block approval")
    if not any(c["classification"] == "selected" for c in plan["criteria"]):
        raise ValueError("no browser criteria selected")


def approve_browser(controller, execution_id, plan_id, *, request_id, attempt, client=None):
    identity(request_id)
    if type(attempt) is not int or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    plan = get_plan(controller.conn, execution_id, plan_id)
    _ready(plan)
    refreshed, _ = current_target(
        controller, execution_id, client=client, mapping=plan["target"]["mapping"]
    )
    if refreshed != plan["binding"]:
        raise ValueError("browser plan binding stale; propose a new plan")
    with transaction(controller.conn):
        require_execution_authority(controller, execution_id, operation="delivery")
        if local_binding(controller, execution_id, plan["target"]["mapping"])[0] != refreshed:
            raise ValueError("delivery changed during approval")
        get_plan(controller.conn, execution_id, plan_id)
        prior = controller.conn.execute(
            "SELECT id,plan_id,attempt,binding FROM browser_approvals "
            "WHERE execution_id=? "
            "AND request_id=?",
            (execution_id, request_id),
        ).fetchone()
        if prior:
            if (
                prior["plan_id"] != plan_id
                or prior["attempt"] != attempt
                or prior["binding"] != canonical(refreshed)
            ):
                raise ValueError("approval request ID conflicts")
            return approval(controller.conn, execution_id, prior["id"])
        if controller.conn.execute(
            "SELECT 1 FROM browser_runs "
            "WHERE execution_id=? "
            "AND state IN ('Dispatching','Running','Uncertain')",
            (execution_id,),
        ).fetchone():
            raise ValueError("previous browser dispatch unresolved")
        aid = digest(
            {
                "execution_id": execution_id,
                "plan_id": plan_id,
                "request_id": request_id,
                "attempt": attempt,
                "binding": refreshed,
            }
        )
        controller.conn.execute(
            "INSERT INTO browser_approvals VALUES (?,?,?,?,?,?,?)",
            (aid, execution_id, plan_id, request_id, attempt, canonical(refreshed), utcnow()),
        )
        browser_event(
            controller.conn,
            execution_id,
            "browser.approved",
            {"approval_id": aid, "plan_id": plan_id, "attempt": attempt},
        )
    return approval(controller.conn, execution_id, aid)


def _currency(controller, eid, plan, saved, client):
    try:
        binding, _ = current_target(
            controller, eid, client=client, mapping=plan["target"]["mapping"]
        )
        require_execution_authority(controller, eid, operation="delivery")
        return {
            "currency": "current" if binding == saved["binding"] else "stale",
            "reason": "Deployment and authority checked at observation time"
            if binding == saved["binding"]
            else "Deployment or authority changed",
            "observed_at": utcnow(),
        }
    except (ValueError, OSError, LookupError):
        return {
            "currency": "unavailable",
            "reason": "Deployment or authority refresh unavailable",
            "observed_at": utcnow(),
        }


def _owned_update(conn, run, state, error=None, observation=None):
    if (
        conn.execute(
            "UPDATE browser_runs SET state=?,error=?,final_observation=?,finished_at=? "
            "WHERE id=? AND owner_token=? AND state IN ('Dispatching','Running')",
            (
                state,
                redact(error) if error else None,
                canonical(observation) if observation else None,
                utcnow() if state not in ACTIVE else None,
                run["id"],
                run["owner_token"],
            ),
        ).rowcount
        != 1
    ):
        raise ValueError("browser ownership or state changed")


def run_browser(
    controller, execution_id, approval_id, *, client=None, driver_factory=BrowserDriver
):
    saved = approval(controller.conn, execution_id, approval_id)
    prior = existing_run(controller.conn, approval_id)
    if prior:
        if prior["state"] in ACTIVE:
            raise ValueError("browser dispatch unresolved; reconcile without replay")
        return browser_view(controller.conn, execution_id)
    plan = get_plan(controller.conn, execution_id, saved["plan_id"])
    _ready(plan)
    if (
        current_target(controller, execution_id, client=client, mapping=plan["target"]["mapping"])[
            0
        ]
        != saved["binding"]
    ):
        raise ValueError("browser approval stale")
    capability = probe_browser(controller, execution_id)
    if not capability["available"]:
        raise ValueError("browser integration unavailable")
    run, claimed = claim_run(controller, approval_id)
    if not claimed:
        return browser_view(controller.conn, execution_id)
    root, driver = None, None
    try:
        root = run_root(controller.conn, run)
        if (
            current_target(
                controller, execution_id, client=client, mapping=plan["target"]["mapping"]
            )[0]
            != saved["binding"]
        ):
            _owned_update(
                controller.conn,
                run,
                "Blocked",
                "Approval stale before browser launch",
                {"currency": "stale"},
            )
            return browser_view(controller.conn, execution_id)
        require_execution_authority(controller, execution_id, operation="delivery")
        _owned_update(controller.conn, run, "Running")
        driver = driver_factory(plan, root)
        stop = None
        receipts = {}
        for criterion in (c for c in plan["criteria"] if c["classification"] == "selected"):
            if not stop:
                if (
                    _currency(controller, execution_id, plan, saved, client)["currency"]
                    != "current"
                ):
                    stop = "Deployment or authority changed/unavailable"
            result = (
                {"status": "not_run", "error": stop, "observations": [], "evidence": []}
                if stop
                else driver.run_criterion(criterion)
            )
            if result.get("status") not in {"passed", "failed", "blocked", "uncertain", "not_run"}:
                raise ValueError("invalid browser result")
            result = result | {
                "criterion_id": criterion["id"],
                "text": criterion["text"],
                "origin": evidence_origin(plan["target"]["url"])
                if driver_factory is BrowserDriver
                else "test_double",
            }
            result = redact_value(result)
            verify_evidence(root, result)
            receipt = {
                "binding": receipt_binding(run, saved),
                "criterion_hash": digest(criterion),
                "result": result,
            }
            name = criterion_name(criterion["id"])
            receipts[name] = publish(root, name, receipt)
            save_criterion(controller.conn, run["id"], result)
            if result["status"] != "passed":
                stop = (
                    "Previous criterion "
                    + result["status"]
                    + "; investigation or explicit recovery required"
                )
        driver.close()
        driver = None
        stored = run_record(controller.conn, run["id"])
        verify_run_artifacts(controller.conn, stored)
        observation = _currency(controller, execution_id, plan, saved, client)
        states = {r["status"] for r in stored["results"]}
        state = (
            "Uncertain"
            if "uncertain" in states
            else (
                "Failed"
                if "failed" in states
                else ("Completed" if states == {"passed"} else "Blocked")
            )
        )
        if observation["currency"] != "current" and state == "Completed":
            state = "Blocked"
        with transaction(controller.conn):
            try:
                require_execution_authority(controller, execution_id, operation="delivery")
            except (ValueError, OSError, LookupError):
                observation = {
                    "currency": "stale",
                    "reason": "Specification authority changed",
                    "observed_at": utcnow(),
                }
                if state == "Completed":
                    state = "Blocked"
            if (
                local_binding(controller, execution_id, plan["target"]["mapping"])[0]
                != saved["binding"]
            ):
                observation = {
                    "currency": "stale",
                    "reason": "Local authority or deployment changed",
                    "observed_at": utcnow(),
                }
                if state == "Completed":
                    state = "Blocked"
            completion = {
                "binding": receipt_binding(run, saved),
                "receipts": receipts,
                "state": state,
                "observation": observation,
            }
            publish(root, "completion.json", completion)
            _owned_update(controller.conn, run, state, observation=observation)
            browser_event(
                controller.conn,
                execution_id,
                "browser.finished",
                {"run_id": run["id"], "state": state},
            )
    except BaseException as error:
        # Even failure before an acknowledgement cannot release this consumed authorization.
        _owned_update(controller.conn, run, "Uncertain", redact(str(error)))
        if not isinstance(error, Exception):
            raise
    finally:
        if driver:
            driver.close()
    return browser_view(controller.conn, execution_id)


def reconcile_browser(controller, execution_id, run_id, *, client=None):
    run = run_record(controller.conn, run_id)
    if run["execution_id"] != execution_id:
        raise ValueError("foreign browser run")
    saved = approval(controller.conn, execution_id, run["approval_id"])
    plan = get_plan(controller.conn, execution_id, saved["plan_id"])
    root = run_root(controller.conn, run)
    expected = receipt_binding(run, saved)
    selected = [c for c in plan["criteria"] if c["classification"] == "selected"]
    for criterion in selected:
        path = root / criterion_name(criterion["id"])
        if not path.exists():
            continue
        receipt = read_receipt(root, path.name, expected)
        if (
            receipt.get("criterion_hash") != digest(criterion)
            or receipt["result"]["criterion_id"] != criterion["id"]
        ):
            raise ValueError("criterion receipt mismatch")
        verify_evidence(root, receipt["result"])
        save_criterion(controller.conn, run_id, receipt["result"])
    if run["state"] not in ACTIVE:
        return browser_view(controller.conn, execution_id)
    if not (root / "completion.json").exists():
        controller.conn.execute(
            "UPDATE browser_runs SET state='Uncertain', "
            "error='Completion receipt absent; actions must not replay' "
            "WHERE id=? AND state IN ('Dispatching','Running')",
            (run_id,),
        )
        return browser_view(controller.conn, execution_id)
    completion = read_receipt(root, "completion.json", expected)
    if completion["state"] not in {"Completed", "Failed", "Blocked", "Uncertain"}:
        raise ValueError("invalid completion receipt state")
    results = run_record(controller.conn, run_id)["results"]
    if completion["state"] == "Completed" and any(r["status"] != "passed" for r in results):
        raise ValueError("completion conflicts with criterion outcomes")
    for criterion in selected:
        name = criterion_name(criterion["id"])
        if completion["receipts"].get(name) != digest((root / name).read_bytes()):
            raise ValueError("completion criterion receipt mismatch")
    verify_run_artifacts(controller.conn, run_record(controller.conn, run_id))
    observation = _currency(controller, execution_id, plan, saved, client)
    with transaction(controller.conn):
        controller.conn.execute(
            "UPDATE browser_runs SET state=?,finished_at=?,final_observation=? "
            "WHERE id=? "
            "AND state IN ('Dispatching','Running','Uncertain')",
            (completion["state"], utcnow(), canonical(observation), run_id),
        )
    return browser_view(controller.conn, execution_id)
