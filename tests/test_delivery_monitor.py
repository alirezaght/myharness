"""Exact-candidate monitoring and bounded routing use the real durable controller."""

import copy

import pytest

from harness.delivery import publish_delivery
from harness.delivery_models import DeliveryConfig
from harness.delivery_monitor import poll_delivery
from harness.delivery_routing import route_failure
from harness.delivery_store import configure_delivery
from harness.execution_contract import canonical
from tests.execution_helpers import approved_execution

SHA = "a" * 40


class GitLab:
    def __init__(self):
        self.mr = {
            "iid": 14,
            "state": "opened",
            "source_branch": "candidate",
            "target_branch": "main",
            "sha": SHA,
            "web_url": "https://gitlab.example/group/project/-/merge_requests/14",
        }
        self.pipeline = {"id": 42, "sha": SHA, "status": "success"}
        self.deployment = {"id": 7, "sha": SHA, "status": "success", "environment": {"id": 3}}
        self.environment = {
            "id": 3,
            "name": "staging",
            "state": "available",
            "last_deployment": self.deployment.copy(),
        }
        self.creates = []

    def get(self, path):
        return copy.deepcopy(
            {
                "/repository/commits/" + SHA: {"id": SHA},
                "/merge_requests/14": self.mr,
                "/pipelines/42": self.pipeline,
                "/pipelines/42/test_report": {"total_count": 1},
                "/environments/3": self.environment,
            }[path]
        )

    def branch(self, name):
        return {"name": name, "commit": {"id": SHA}}

    def create_merge_request(self, source, target, title, marker):
        self.creates.append(marker)
        return copy.deepcopy(self.mr)

    def list_pages(self, path, **kwargs):
        return copy.deepcopy(
            {
                "/merge_requests/14/pipelines": [self.pipeline],
                "/pipelines/42/jobs": [
                    {"id": 8, "status": "failed", "failure_reason": "script_failure"}
                ],
                "/environments": [self.environment],
                "/deployments": [self.deployment],
            }[path]
        )


@pytest.fixture
def delivery(controller, sample_repo, monkeypatch):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    configure_delivery(
        controller,
        eid,
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
    monkeypatch.setattr("harness.delivery.publication_candidate", lambda *a: SHA)
    monkeypatch.setattr("harness.delivery_monitor.publication_candidate", lambda *a: SHA)
    return controller, eid, GitLab()


def test_publish_reconciles_and_success_requires_current_staging_deployment(delivery):
    controller, eid, api = delivery
    assert publish_delivery(controller, eid, client=api)["status"] == "Published"
    publish_delivery(controller, eid, client=api)
    assert len(api.creates) == 1
    assert poll_delivery(controller, eid, client=api)["status"] == "Success"
    api.environment["last_deployment"] = api.deployment | {"id": 99, "sha": "b" * 40}
    result = poll_delivery(controller, eid, client=api)
    assert result["status"] == "Monitoring" and not result["staging"]["ready"]


def test_failed_open_candidate_routes_once_but_closed_or_merged_require_review(
    delivery, monkeypatch
):
    controller, eid, api = delivery
    publish_delivery(controller, eid, client=api)
    api.pipeline["status"] = "failed"
    calls = []
    monkeypatch.setattr("harness.delivery_routing.route_failure", lambda *a: calls.append(a[2]))
    poll_delivery(controller, eid, client=api)
    assert calls == [api.pipeline]
    api.mr["state"] = "merged"
    assert poll_delivery(controller, eid, client=api)["status"] == "DeveloperReview"
    api.mr["state"] = "closed"
    assert poll_delivery(controller, eid, client=api)["status"] == "Blocked"
    assert len(calls) == 1


def test_technical_retry_budget_and_unknown_failure_are_durable(delivery):
    controller, eid, api = delivery
    publish_delivery(controller, eid, client=api)
    controller.conn.execute("UPDATE executions SET status='Completed' WHERE id=?", (eid,))
    controller.conn.execute(
        "UPDATE deliveries SET retries=1,snapshot=? WHERE execution_id=?",
        (canonical({"blockers": []}), eid),
    )
    controller.conn.execute(
        "INSERT INTO execution_entities(execution_id,family,id,data) VALUES (?,?,?,?)",
        (eid, "task", "A", canonical({"id": "A"})),
    )
    pipeline = api.pipeline | {"status": "failed"}
    known = [{"status": "failed", "failure_reason": "script_failure"}]
    result = route_failure(controller, eid, pipeline, known)
    assert result["status"] == "Blocked"
    assert "retry limit" in str(result["blockers"])
    unknown = [{"status": "failed", "failure_reason": "unknown_failure"}]
    assert route_failure(controller, eid, pipeline, unknown)["status"] == "DeveloperReview"
