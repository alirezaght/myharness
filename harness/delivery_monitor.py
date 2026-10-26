"""Exact-candidate observations never grant AL implementation approval."""

from harness.db import utcnow
from harness.delivery import _validate_mr, publication_candidate
from harness.delivery_providers import delivery_client
from harness.delivery_store import delivery_view, get_delivery, save_snapshot
from harness.execution_authority import require_execution_authority
from harness.gitlab import GitLabError


def poll_delivery(controller, execution_id, *, client=None):
    require_execution_authority(controller, execution_id, operation="delivery")
    delivery = get_delivery(controller.conn, execution_id)
    if not delivery["mr"] or not delivery["candidate_sha"]:
        raise ValueError("publish before monitoring")
    api, sha = client or delivery_client(delivery["config"]), delivery["candidate_sha"]
    iid = delivery["mr"]["iid"]
    mr = api.get(f"/merge_requests/{iid}")
    _validate_mr(mr, delivery["config"], sha)
    branch = api.branch(delivery["config"]["source_branch"])
    if branch.get("commit", {}).get("id") != sha:
        raise ValueError("published candidate no longer current")
    pipelines = api.list_pages(f"/merge_requests/{iid}/pipelines")
    candidates = [p for p in pipelines if p.get("sha") == sha and type(p.get("id")) is int]
    pipeline = (
        api.get(f"/pipelines/{max(candidates, key=lambda p: p['id'])['id']}")
        if candidates
        else None
    )
    jobs, report, blockers = [], None, []
    if pipeline:
        if pipeline.get("sha") != sha:
            raise ValueError("pipeline candidate mismatch")
        pid = pipeline["id"]
        jobs = api.list_pages(f"/pipelines/{pid}/jobs", include_retried="true")
        try:
            report = api.get(f"/pipelines/{pid}/test_report")
        except GitLabError as error:
            if error.status != 404:
                raise
            blockers.append("test report unavailable")
    environments = api.list_pages("/environments", name=delivery["config"]["staging_environment"])
    exact = [e for e in environments if e.get("name") == delivery["config"]["staging_environment"]]
    staging = {
        "ready": False,
        "status": "unknown",
        "environment": delivery["config"]["staging_environment"],
    }
    if len(exact) == 1:
        environment = api.get(f"/environments/{exact[0]['id']}")
        if environment.get("id") != exact[0]["id"] or environment.get("name") != exact[0]["name"]:
            raise ValueError("staging environment identity changed")
        latest_deployment = environment.get("last_deployment") or {}
        deployments = api.list_pages("/deployments", environment=exact[0]["name"], status="success")
        matching = [
            d
            for d in deployments
            if d.get("sha") == sha
            and d.get("status") == "success"
            and d.get("environment", {}).get("id") == exact[0]["id"]
            and d.get("id") == latest_deployment.get("id")
            and latest_deployment.get("sha") == sha
            and latest_deployment.get("status") == "success"
            and environment.get("state") == "available"
        ]
        if matching:
            staging = {
                "ready": True,
                "status": "success",
                "environment": exact[0]["name"],
                "deployment_id": max(matching, key=lambda d: d["id"])["id"],
                "sha": sha,
            }
    # Recheck remote identities after paginated observations to close candidate movement windows.
    latest = api.get(f"/merge_requests/{iid}")
    _validate_mr(latest, delivery["config"], sha)
    if api.branch(delivery["config"]["source_branch"]).get("commit", {}).get("id") != sha:
        raise ValueError("candidate moved during monitoring")
    if staging["ready"]:
        latest_environment = api.get(f"/environments/{exact[0]['id']}")
        if latest_environment.get("state") != "available" or latest_environment.get(
            "last_deployment"
        ) != environment.get("last_deployment"):
            staging = {
                "ready": False,
                "status": "changed during observation",
                "environment": exact[0]["name"],
            }
    current = False
    try:
        current = publication_candidate(controller, execution_id) == sha
    except (ValueError, OSError):
        blockers.append("current AL candidate readiness unavailable or superseded")
    if not current:
        staging = {
            "ready": False,
            "status": "stale",
            "environment": delivery["config"]["staging_environment"],
        }
    status = "Monitoring" if current else "Stale"
    if current and pipeline and pipeline.get("status") == "success" and staging["ready"]:
        status = "Success"
    if latest.get("state") == "closed":
        status = "Blocked"
        blockers.append("Review request is closed.")
    elif latest.get("state") == "merged" and pipeline and pipeline.get("status") == "failed":
        status = "DeveloperReview"
        blockers.append(
            "Merged candidate failed; developer review required before further changes."
        )
    snapshot = {
        "pipeline": pipeline,
        "jobs": jobs,
        "test_report": report,
        "staging": staging,
        "blockers": blockers,
        "observed_at": utcnow(),
    }
    save_snapshot(
        controller,
        execution_id,
        snapshot,
        status,
        expected_candidate=sha,
        expected_generation=delivery["generation"],
    )
    if (
        current
        and pipeline
        and pipeline.get("status") == "failed"
        and latest.get("state") == "opened"
    ):
        from harness.delivery_routing import route_failure

        route_failure(controller, execution_id, pipeline, jobs)
    return delivery_view(controller.conn, execution_id)
