"""GitHub transport, independent workflows, retries and deployment currency."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from harness.delivery_models import DeliveryConfig
from harness.delivery_providers import delivery_client
from harness.github import GitHubClient, review_request
from harness.gitlab import GitLabError

SHA = "a" * 40


def client():
    return GitHubClient(
        {"endpoint": "https://github.com", "project": "example/repo"}, lambda: "synthetic-token"
    )


def run(identifier, workflow, conclusion="success", attempt=1, sha=SHA):
    return {
        "id": identifier,
        "workflow_id": workflow,
        "head_sha": sha,
        "status": "completed",
        "conclusion": conclusion,
        "run_attempt": attempt,
    }


def test_all_latest_workflows_count_and_a_retry_changes_pipeline_identity(monkeypatch):
    api = client()
    runs = [
        run(100, 1, "failure"),
        run(101, 1),
        run(102, 2, "failure"),
        run(103, 3, "success", sha="b" * 40),
    ]
    monkeypatch.setattr(api, "pages", lambda *a, **kw: runs)
    failed = api.pipeline(SHA)
    assert failed["status"] == "failed" and len(failed["workflows"]) == 2
    runs[2] = run(102, 2, "success", attempt=2)
    fixed = api.pipeline(SHA)
    assert fixed["status"] == "success" and fixed["id"] != failed["id"]
    with pytest.raises(ValueError, match="attempts changed"):
        api.group(failed["id"])
    runs[2]["status"] = "in_progress"
    assert api.pipeline(SHA)["status"] == "running"


def test_current_latest_deployment_and_status_cannot_fall_back_to_old_success(monkeypatch):
    api = client()
    api.environments = {3: {"id": 3, "name": "staging"}}
    monkeypatch.setattr(api, "raw_get", lambda path: {"id": 3, "name": "staging"})
    deployments = [
        {"id": 7, "sha": SHA, "environment": "staging"},
        {"id": 9, "sha": "b" * 40, "environment": "staging"},
    ]
    statuses = [{"id": 10, "state": "success"}, {"id": 11, "state": "inactive"}]
    monkeypatch.setattr(
        api, "pages", lambda path, **kw: deployments if path == "/deployments" else statuses
    )
    observed = api.environment(3)["last_deployment"]
    assert observed["id"] == 9 and observed["sha"] != SHA and observed["status"] == "inactive"
    assert api.list_pages("/deployments", environment="staging", status="success") == []


def test_workflow_run_number_orders_separate_requests_independently_of_response_order(monkeypatch):
    api = client()
    runs = [run(102, 1) | {"run_number": 1}, run(101, 1, "failure") | {"run_number": 2}]
    monkeypatch.setattr(api, "pages", lambda *a, **kw: runs)
    first = api.pipeline(SHA)
    runs.reverse()
    second = api.pipeline(SHA)
    assert first["status"] == second["status"] == "failed"
    assert first["id"] == second["id"]


def test_provider_selection_and_review_request_states():
    config = DeliveryConfig(
        "https://github.com", "example/repo", "candidate", "main", "staging", provider="github"
    ).to_dict()
    assert config["credential_env"] == "GITHUB_TOKEN"
    assert isinstance(delivery_client(config), GitHubClient)
    value = {
        "number": 4,
        "head": {"sha": SHA, "ref": "candidate"},
        "base": {"ref": "main"},
        "state": "open",
        "html_url": "https://github.com/x/y/pull/4",
    }
    assert review_request(value)["state"] == "opened"
    assert review_request(value | {"state": "closed", "merged": True})["state"] == "merged"
    with pytest.raises(ValueError):
        DeliveryConfig(
            "https://example", "repo", "branch", "main", "staging", provider="other"
        ).to_dict()


def test_http_auth_encoding_pagination_and_non_retrying_mutations():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            if parse_qs(urlsplit(self.path).query).get("page") == ["1"]:
                # The untrusted link is only a continuation signal, never a credential destination.
                self.send_header("Link", '<https://untrusted.example/steal?page=2>; rel="next"')
            self.end_headers()
            self.wfile.write(json.dumps([{"text": "synthetic-token"}]).encode())

        def do_POST(self):
            seen.append((self.path, self.headers.get("Authorization")))
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(500)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        api = GitHubClient(
            {
                "endpoint": f"http://127.0.0.1:{server.server_port}",
                "project": "example/repo",
                "test_loopback": True,
            },
            lambda: "synthetic-token",
        )
        rows = api.pages("/pulls")
        assert len(rows) == 2 and "synthetic-token" not in str(rows)
        assert all(path.startswith("/api/v3/repos/example/repo/") for path, _ in seen)
        assert all(auth == "Bearer synthetic-token" for _, auth in seen)
        with pytest.raises(GitLabError) as error:
            api.create_branch("candidate", SHA)
        assert error.value.uncertain and len(seen) == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
