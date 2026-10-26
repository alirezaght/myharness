"""Keep each of the 30 V1 requirements tied to a concrete test location."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v1_acceptance_map_covers_all_criteria_with_resolvable_test_references():
    document = (ROOT / "docs/v1-acceptance.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\|\s*(\d+)\s*\|.*?`(tests/[^`]+)`.*?\|\s*([^|]+)\|$", document, re.M)
    assert len(rows) == 30
    assert [int(row[0]) for row in rows] == list(range(1, 31))
    for _, reference, _ in rows:
        path, test_name = reference.split("::", maxsplit=1)
        source = (ROOT / path).read_text(encoding="utf-8")
        assert re.search(rf"\bdef\s+{re.escape(test_name)}\s*\(", source), reference


def test_live_exit_and_browser_gaps_are_marked_outstanding():
    document = (ROOT / "docs/v1-acceptance.md").read_text(encoding="utf-8")
    assert "real staging run" in document
    assert "remain outstanding" in document
    assert "Fixture-provider HTTP tests" in document


def test_delivery_completion_summary_keeps_browser_status_and_currency(
    controller, sample_repo, monkeypatch
):
    from harness.browser_validation import approve_browser, run_browser
    from harness.delivery_models import DeliveryConfig
    from harness.delivery_store import configure_delivery, delivery_view
    from tests.execution_helpers import approved_execution
    from tests.test_browser_validation import CountingDriver, prepared
    from tests.test_delivery_monitor import GitLab

    _, execution_id = approved_execution(controller, sample_repo, monkeypatch)
    configure_delivery(
        controller,
        execution_id,
        DeliveryConfig(
            "https://gitlab.example",
            "group/project",
            "candidate",
            "main",
            "staging",
            publication_authorized=True,
            retry_limit=1,
        ),
    )
    monkeypatch.setattr("harness.delivery.publication_candidate", lambda *args: "a" * 40)
    monkeypatch.setattr("harness.delivery_monitor.publication_candidate", lambda *args: "a" * 40)
    api = GitLab()
    api.environment["external_url"] = "https://staging.example/"
    delivery = controller, execution_id, api
    controller, execution_id, api, plan = prepared(delivery, monkeypatch)
    approval = approve_browser(
        controller,
        execution_id,
        plan["plan_id"],
        request_id="completion-summary-browser-0001",
        attempt=1,
        client=api,
    )
    run_browser(
        controller,
        execution_id,
        approval["id"],
        client=api,
        driver_factory=CountingDriver,
    )
    view = delivery_view(controller.conn, execution_id)
    assert view["completion_summary"].get("browser") is not None
    assert view["completion_summary"]["browser"]["status"] == view["browser_validation"]["status"]
    assert (
        view["completion_summary"]["browser"]["currency"] == view["browser_validation"]["currency"]
    )
