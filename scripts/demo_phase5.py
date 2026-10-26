"""Run an isolated Phase 5 workflow with a synthetic provider and test driver."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from harness.browser_plan import propose_browser
from harness.browser_validation import approve_browser, run_browser
from harness.controller import HarnessController
from harness.db import connect
from harness.delivery import publish_delivery
from harness.delivery_models import DeliveryConfig
from harness.delivery_monitor import poll_delivery
from harness.delivery_store import configure_delivery
from harness.reporting import completion_report
from tests.execution_helpers import approved_execution
from tests.sample_repo import make_sample_repo
from tests.test_delivery_monitor import GitLab

CANDIDATE = "a" * 40


class FixtureDriver:
    """A deterministic driver double; it never starts Chrome or requests a URL."""

    def __init__(self, plan, runtime):
        self.plan = plan

    def run_criterion(self, criterion):
        return {
            "status": "passed",
            "observations": [{"fixture": "synthetic assertion"}],
            "evidence": [],
            "error": None,
        }

    def close(self):
        pass


def run():
    with tempfile.TemporaryDirectory(prefix="harness-phase5-demo-") as temporary:
        root = Path(temporary)
        os.environ["HARNESS_HOME"] = str(root / "private-home")
        os.environ.pop("HARNESS_DB", None)
        repo = make_sample_repo(root / "repository")
        controller = HarnessController(connect(root / "state.db"))
        patch = pytest.MonkeyPatch()
        try:
            task_id, execution_id = approved_execution(controller, repo, patch)
            configure_delivery(
                controller,
                execution_id,
                DeliveryConfig(
                    "https://gitlab.example.invalid",
                    "fixture/project",
                    "candidate",
                    "main",
                    "staging",
                    publication_authorized=True,
                    retry_limit=1,
                ),
            )
            patch.setattr("harness.delivery.publication_candidate", lambda *args: CANDIDATE)
            patch.setattr("harness.delivery_monitor.publication_candidate", lambda *args: CANDIDATE)
            provider = GitLab()
            provider.environment["external_url"] = "https://staging.fixture.invalid/"
            publish_delivery(controller, execution_id, client=provider)
            poll_delivery(controller, execution_id, client=provider)

            initial = propose_browser(controller, execution_id, client=provider)
            mapping = {
                "evidence": {"screenshot": "none", "mask_selectors": []},
                "criteria": [
                    {
                        "id": criterion["id"],
                        "classification": "selected",
                        "reason": "Synthetic fixture demo mapping",
                        "steps": [
                            {"op": "navigate", "url": "https://staging.fixture.invalid/"},
                            {"op": "assert_visible", "selector": "h1"},
                        ],
                    }
                    for criterion in initial["plan"]["criteria"]
                ],
            }
            plan = propose_browser(controller, execution_id, mapping, client=provider)
            patch.setattr(
                "harness.browser_validation.probe_browser",
                lambda *args: {
                    "available": True,
                    "integration": "test-double; not a real capability probe",
                },
            )
            approval = approve_browser(
                controller,
                execution_id,
                plan["plan_id"],
                request_id="phase5-demo-request-0001",
                attempt=1,
                client=provider,
            )
            browser = run_browser(
                controller,
                execution_id,
                approval["id"],
                client=provider,
                driver_factory=FixtureDriver,
            )
            report = completion_report(controller.conn, execution_id)
            return {
                "status": "demo_complete",
                "task_id": task_id,
                "execution_id": execution_id,
                "provider_origin": "test_double; no provider HTTP request",
                "driver_origin": "test_double; Chrome not started and target not contacted",
                "browser_status": browser["status"],
                "browser_evidence_origin": browser["evidence_origin"],
                "capability_origin": "test-double; not a real capability probe",
                "live_acceptance": report["live_acceptance"],
                "report": report,
            }
        finally:
            patch.undo()
            controller.conn.close()


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
