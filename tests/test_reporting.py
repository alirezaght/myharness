"""Completion reads expose missing data honestly and never dispatch providers."""

from harness.browser_validation import approve_browser, run_browser
from harness.execution_store import get_execution
from harness.reporting import completion_report
from tests import test_delivery_monitor
from tests.execution_helpers import approved_execution
from tests.test_browser_validation import CountingDriver, prepared

delivery = test_delivery_monitor.delivery


def test_missing_delivery_and_test_counts_are_unavailable(controller, sample_repo, monkeypatch):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    before = get_execution(controller.conn, eid)
    report = completion_report(controller.conn, eid)
    assert report["al_tests"]["status"] == "unavailable"
    assert report["ci"]["tests"]["counts_available"] is False
    assert report["live_acceptance"] == "outstanding"
    assert get_execution(controller.conn, eid) == before


def test_reports_show_each_check_result_provenance(controller, sample_repo, monkeypatch):
    """#31: ran now, or reused from run X on the same input tree."""
    from harness.execution_projection import execution_view
    from harness.execution_store import put_entity

    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    ran = {"id": "E1", "area": "unit", "status": "PASS", "scope": ["src"], "validity": "VALID"}
    reused = dict(
        ran,
        id="E2",
        area="lint",
        provenance={
            "mode": "reused",
            "origin": {"run": "run-0", "entry": "E9", "session": "s"},
            "tree": "ab" * 20,
            "key": "k",
        },
    )
    put_entity(controller.conn, eid, "test", "E1", ran)
    put_entity(controller.conn, eid, "test", "E2", reused)
    report = completion_report(controller.conn, eid)
    texts = {r["id"]: r["provenance"]["text"] for r in report["al_tests"]["records"]}
    assert texts["E1"].startswith("ran now")
    assert texts["E2"] == "reused from run run-0 (E9) on the same input tree " + "ab" * 6
    assert report["al_tests"]["provenance"] == {"ran": 1, "reused": 1, "running": 0}
    view = execution_view(controller.conn, eid)
    assert {c["id"]: c["provenance"]["mode"] for c in view["checks"]} == {
        "E1": "ran",
        "E2": "reused",
    }
    assert view["check_provenance"]["reused"] == 1
    assert view["al_live"] is None and view["question_answers"] == []


def test_report_preserves_browser_and_provider_sources(delivery, monkeypatch):
    controller, eid, api, plan = prepared(delivery, monkeypatch)
    saved = approve_browser(
        controller, eid, plan["plan_id"], request_id="explicit", attempt=1, client=api
    )
    run_browser(controller, eid, saved["id"], client=api, driver_factory=CountingDriver)
    monkeypatch.setattr(
        "harness.delivery_monitor.poll_delivery",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("read must not poll")),
    )
    report = completion_report(controller.conn, eid)
    assert report["browser"]["status"] == "Completed"
    assert report["ci"]["tests"]["total_count"] == 1
    assert report["al_tests"]["status"] == "unavailable"


def test_delivery_summary_is_concise_and_preserves_browser_facts(delivery, monkeypatch):
    from harness.delivery_store import delivery_view

    controller, eid, api, plan = prepared(delivery, monkeypatch)
    CountingDriver.status = "failed"
    saved = approve_browser(
        controller, eid, plan["plan_id"], request_id="summary", attempt=1, client=api
    )
    run_browser(controller, eid, saved["id"], client=api, driver_factory=CountingDriver)
    monkeypatch.setattr(
        "harness.delivery_monitor.poll_delivery",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("read must not poll")),
    )
    view = delivery_view(controller.conn, eid)
    summary = view["completion_summary"]["browser"]
    assert summary == {
        key: view["browser_validation"][key]
        for key in ("status", "currency", "reason", "observed_at", "evidence_origin", "blockers")
    }
    assert summary["status"] == "Failed"
    assert summary["currency"] == "current"
    assert summary["evidence_origin"] == ["test_double"]
    assert "runs" not in summary and "plan" not in summary
    assert completion_report(controller.conn, eid)["browser"]["runs"]
