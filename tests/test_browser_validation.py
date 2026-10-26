"""Exercise the production authority/approval/controller with an isolated driver double."""

import pytest

from harness.browser_plan import propose_browser
from harness.browser_validation import approve_browser, run_browser
from harness.delivery import publish_delivery
from tests import test_delivery_monitor

delivery = test_delivery_monitor.delivery


class CountingDriver:
    count = 0
    status = "passed"

    def __init__(self, plan, runtime):
        type(self).count += 1

    def run_criterion(self, criterion):
        return {
            "status": self.status,
            "evidence": [],
            "observations": [{"ok": True}],
            "error": None,
        }

    def close(self):
        pass


def prepared(delivery, monkeypatch):
    controller, eid, api = delivery
    publish_delivery(controller, eid, client=api)
    api.environment["external_url"] = "https://staging.example/"
    first = propose_browser(controller, eid, client=api)
    mapping = {
        "evidence": {"screenshot": "none", "mask_selectors": []},
        "criteria": [
            {
                "id": c["id"],
                "classification": "selected",
                "reason": "Explicit fixture mapping",
                "steps": [
                    {"op": "navigate", "url": "https://staging.example/"},
                    {"op": "assert_visible", "selector": "h1"},
                ],
            }
            for c in first["plan"]["criteria"]
        ],
    }
    plan = propose_browser(controller, eid, mapping, client=api)
    monkeypatch.setattr("harness.browser_validation.probe_browser", lambda *a: {"available": True})
    CountingDriver.count, CountingDriver.status = 0, "passed"
    return controller, eid, api, plan


def test_separate_approval_and_no_repeat(delivery, monkeypatch):
    controller, eid, api, plan = prepared(delivery, monkeypatch)
    with pytest.raises(LookupError):
        run_browser(controller, eid, "automatic", client=api, driver_factory=CountingDriver)
    saved = approve_browser(
        controller, eid, plan["plan_id"], request_id="explicit", attempt=1, client=api
    )
    assert (
        approve_browser(
            controller, eid, plan["plan_id"], request_id="explicit", attempt=1, client=api
        )["id"]
        == saved["id"]
    )
    result = run_browser(controller, eid, saved["id"], client=api, driver_factory=CountingDriver)
    assert result["status"] == "Completed", result
    assert result["currency"] == "current"
    run_browser(controller, eid, saved["id"], client=api, driver_factory=CountingDriver)
    assert CountingDriver.count == 1


def test_target_change_invalidates_approval(delivery, monkeypatch):
    controller, eid, api, plan = prepared(delivery, monkeypatch)
    saved = approve_browser(
        controller, eid, plan["plan_id"], request_id="explicit", attempt=1, client=api
    )
    api.environment["external_url"] = "https://other.example/"
    with pytest.raises(ValueError, match="stale"):
        run_browser(controller, eid, saved["id"], client=api, driver_factory=CountingDriver)
    assert CountingDriver.count == 0


def test_failure_is_investigation_without_authority(delivery, monkeypatch):
    controller, eid, api, plan = prepared(delivery, monkeypatch)
    CountingDriver.status = "failed"
    saved = approve_browser(
        controller, eid, plan["plan_id"], request_id="explicit", attempt=1, client=api
    )
    view = run_browser(controller, eid, saved["id"], client=api, driver_factory=CountingDriver)
    assert view["status"] == "Failed", view
    assert view["investigations"][0]["status"] == "investigation_required"
    assert view["investigations"][0]["architecture_authorized"] is False
    assert controller.conn.execute("SELECT COUNT(*) FROM delivery_corrections").fetchone()[0] == 0


def test_unresolved_plan_cannot_be_approved(delivery):
    controller, eid, api = delivery
    publish_delivery(controller, eid, client=api)
    api.environment["external_url"] = "https://staging.example/"
    plan = propose_browser(controller, eid, client=api)
    with pytest.raises(ValueError, match="unresolved"):
        approve_browser(
            controller, eid, plan["plan_id"], request_id="explicit", attempt=1, client=api
        )


def test_missing_completion_receipt_is_unavailable_history(delivery, monkeypatch):
    from harness.browser_artifacts import run_root
    from harness.browser_store import browser_view

    controller, eid, api, plan = prepared(delivery, monkeypatch)
    saved = approve_browser(
        controller, eid, plan["plan_id"], request_id="evidence", attempt=1, client=api
    )
    view = run_browser(controller, eid, saved["id"], client=api, driver_factory=CountingDriver)
    assert view["status"] == "Completed"
    (run_root(controller.conn, view["runs"][-1]) / "completion.json").unlink()
    history = browser_view(controller.conn, eid)
    assert history["status"] == "Completed"
    assert history["currency"] == "unavailable"
    assert CountingDriver.count == 1


def test_plan_tampering_does_not_reuse_hash(delivery, monkeypatch):
    from harness.execution_contract import canonical

    controller, eid, api, plan = prepared(delivery, monkeypatch)
    document = plan["plan"]
    document["viewport"]["width"] = 1440
    controller.conn.execute(
        "UPDATE browser_plans SET document=? WHERE id=?", (canonical(document), plan["plan_id"])
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        approve_browser(
            controller, eid, plan["plan_id"], request_id="tampered", attempt=1, client=api
        )


def test_new_plan_marks_previous_results_historical(delivery, monkeypatch):
    from harness.browser_store import browser_view

    controller, eid, api, plan = prepared(delivery, monkeypatch)
    saved = approve_browser(
        controller, eid, plan["plan_id"], request_id="original", attempt=1, client=api
    )
    run_browser(controller, eid, saved["id"], client=api, driver_factory=CountingDriver)
    mapping = {
        "viewport": {"width": 1440, "height": 900},
        "evidence": {"screenshot": "none", "mask_selectors": []},
        "criteria": [
            {k: v for k, v in c.items() if k not in {"text", "q_ref"}}
            for c in plan["plan"]["criteria"]
        ],
    }
    propose_browser(controller, eid, mapping, client=api)
    view = browser_view(controller.conn, eid)
    assert view["status"] == "Completed"
    assert view["currency"] == "stale"
