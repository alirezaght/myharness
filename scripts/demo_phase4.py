"""Exercise the real GitLab client against the local Postman simulation only."""

import argparse
import json
import uuid
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from harness.gitlab import GitLabClient, GitLabError


def local_only(endpoint):
    if urlsplit(endpoint).scheme != "http" or urlsplit(endpoint).hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("demo requires a local synthetic HTTP simulation")


def run(endpoint="http://127.0.0.1:4900/gitlab"):
    local_only(endpoint)
    config = {"endpoint": endpoint, "project": "example-group/my-project", "test_loopback": True}
    api = GitLabClient(config, lambda: "synthetic-local-demo-token")
    assert api.get("")["id"] == 731
    branch = "demo-" + uuid.uuid4().hex[:12]
    sha = "a" * 40
    assert api.create_branch(branch, sha)["commit"]["id"] == sha
    mr = api.create_merge_request(branch, "main", "Local delivery demo", "synthetic")
    assert api.get(f"/merge_requests/{mr['iid']}")["source_branch"] == branch
    try:
        api.create_merge_request(branch, "main", "Duplicate", "synthetic")
    except GitLabError as error:
        assert error.status == 409
    else:
        raise AssertionError("duplicate MR must be refused")
    assert any(row["iid"] == mr["iid"] for row in api.list_pages("/merge_requests"))
    pipelines = api.list_pages("/merge_requests/14/pipelines")
    latest = max(pipelines, key=lambda p: p["id"])
    assert api.get(f"/pipelines/{latest['id']}")["sha"] == latest["sha"]
    assert api.list_pages(f"/pipelines/{latest['id']}/jobs")
    assert api.get(f"/pipelines/{latest['id']}/test_report")["total_count"] > 0
    environment = api.get("/environments/310")
    deployments = api.list_pages("/deployments", environment="staging", status="success")
    assert environment["last_deployment"]["id"] in [d["id"] for d in deployments]
    return {
        "status": "passed",
        "transport": "local Postman simulation",
        "checks": [
            "project",
            "branch",
            "MR creation and reconciliation",
            "duplicate refusal",
            "pipelines",
            "jobs",
            "test report",
            "environment",
            "deployment",
        ],
    }


def run_github(endpoint="http://127.0.0.1:4901/github"):
    from harness.github import GitHubClient

    local_only(endpoint)

    def mode(value):
        request = Request(
            endpoint + "/__test/mode",
            data=json.dumps(value).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            assert response.status == 200

    mode({})

    api = GitHubClient(
        {"endpoint": endpoint, "project": "example/harness", "test_loopback": True},
        lambda: "synthetic-local-demo-token",
    )
    assert api.get("")["id"] == 731
    branch = "demo-" + uuid.uuid4().hex[:12]
    assert api.create_branch(branch, "a" * 40)["commit"]["id"] == "a" * 40
    request = api.create_merge_request(branch, "main", "Local GitHub demo", "synthetic")
    assert api.get(f"/merge_requests/{request['iid']}")["source_branch"] == branch
    pipeline = api.list_pages("/merge_requests/14/pipelines")[0]
    assert pipeline["status"] == "success" and len(pipeline["workflows"]) == 2
    assert api.list_pages(f"/pipelines/{pipeline['id']}/jobs")
    report = api.get(f"/pipelines/{pipeline['id']}/test_report")
    assert report["checks"] and not report["counts_available"]
    api.list_pages("/environments", name="staging")
    assert api.get("/environments/3")["last_deployment"]["sha"] == "a" * 40
    assert api.list_pages("/deployments", environment="staging", status="success")
    try:
        mode({"failure": True})
        failed = api.list_pages("/merge_requests/14/pipelines")[0]
        assert failed["status"] == "failed"
        assert any(
            j["status"] == "failed" for j in api.list_pages(f"/pipelines/{failed['id']}/jobs")
        )
        mode({"attempt": 2, "deployment_sha": "b" * 40})
        retry = api.list_pages("/merge_requests/14/pipelines")[0]
        assert retry["status"] == "success" and retry["id"] != failed["id"]
        assert api.get("/environments/3")["last_deployment"]["sha"] != "a" * 40
    finally:
        mode({})
    return {
        "status": "passed",
        "provider": "github",
        "transport": "local Postman simulation",
        "checks": [
            "branch",
            "PR creation/read",
            "multiple Actions workflows",
            "jobs",
            "check summaries",
            "environment",
            "latest deployment",
            "failed workflow",
            "retry identity",
            "stale staging revision",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint")
    parser.add_argument("--provider", choices=["github", "gitlab"], default="gitlab")
    args = parser.parse_args()
    selected = run_github if args.provider == "github" else run
    print(json.dumps(selected(args.endpoint) if args.endpoint else selected(), indent=2))
