"""Uncertain ownership survives restart; durable receipts restore without replay."""

import pytest

from harness.browser_artifacts import run_root
from harness.browser_store import claim_run
from harness.browser_validation import approve_browser, reconcile_browser, run_browser
from harness.db import connect
from harness.execution_contract import loads
from tests import test_delivery_monitor
from tests.test_browser_validation import CountingDriver, prepared

delivery = test_delivery_monitor.delivery


def test_second_connection_cannot_reclaim_dispatch(delivery, monkeypatch):
    controller, eid, api, plan = prepared(delivery, monkeypatch)
    saved = approve_browser(
        controller, eid, plan["plan_id"], request_id="explicit", attempt=1, client=api
    )
    run, _ = claim_run(controller, saved["id"])
    database = controller.conn.execute("PRAGMA database_list").fetchone()["file"]
    from harness.controller import HarnessController

    other = connect(database)
    try:
        with pytest.raises(ValueError, match="unresolved"):
            claim_run(HarnessController(other), saved["id"])
    finally:
        other.close()
    view = reconcile_browser(controller, eid, run["id"], client=api)
    assert view["status"] == "Uncertain"
    with pytest.raises(ValueError, match="unresolved"):
        approve_browser(
            controller, eid, plan["plan_id"], request_id="new-explicit", attempt=2, client=api
        )
    assert CountingDriver.count == 0


def test_receipt_before_database_insert_recovers_without_driver(delivery, monkeypatch):
    controller, eid, api, plan = prepared(delivery, monkeypatch)
    saved = approve_browser(
        controller, eid, plan["plan_id"], request_id="explicit", attempt=1, client=api
    )
    real_save = __import__("harness.browser_validation", fromlist=["save_criterion"]).save_criterion
    monkeypatch.setattr(
        "harness.browser_validation.save_criterion",
        lambda *a: (_ for _ in ()).throw(OSError("crash window")),
    )
    view = run_browser(controller, eid, saved["id"], client=api, driver_factory=CountingDriver)
    assert view["status"] == "Uncertain"
    assert not view["results"]
    monkeypatch.setattr("harness.browser_validation.save_criterion", real_save)
    view = reconcile_browser(controller, eid, view["runs"][-1]["id"], client=api)
    assert view["results"][0]["status"] == "passed"
    assert view["status"] == "Uncertain"
    assert CountingDriver.count == 1


def test_completion_receipt_restores_terminal_commit(delivery, monkeypatch):
    controller, eid, api, plan = prepared(delivery, monkeypatch)
    saved = approve_browser(
        controller, eid, plan["plan_id"], request_id="explicit", attempt=1, client=api
    )
    import harness.browser_validation as validation

    update = validation._owned_update

    def crash_commit(conn, run, state, *args, **kwargs):
        if state == "Completed":
            raise OSError("lost final commit")
        return update(conn, run, state, *args, **kwargs)

    monkeypatch.setattr(validation, "_owned_update", crash_commit)
    view = run_browser(controller, eid, saved["id"], client=api, driver_factory=CountingDriver)
    assert view["status"] == "Uncertain"
    view = reconcile_browser(controller, eid, view["runs"][-1]["id"], client=api)
    assert view["status"] == "Completed"
    assert CountingDriver.count == 1
    root = run_root(controller.conn, view["runs"][-1])
    completion = loads((root / "completion.json").read_bytes())
    assert completion["binding"]["approval_id"] == saved["id"]


def test_concurrent_claims_use_one_durable_owner(delivery, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from harness.controller import HarnessController

    controller, eid, api, plan = prepared(delivery, monkeypatch)
    saved = approve_browser(
        controller, eid, plan["plan_id"], request_id="race", attempt=1, client=api
    )
    database = controller.conn.execute("PRAGMA database_list").fetchone()["file"]

    def claim():
        conn = connect(database)
        try:
            try:
                return claim_run(HarnessController(conn), saved["id"])[1]
            except ValueError as error:
                assert "unresolved" in str(error)
                return False
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: claim(), range(2))) == [False, True]
    assert controller.conn.execute("SELECT COUNT(*) FROM browser_runs").fetchone()[0] == 1
