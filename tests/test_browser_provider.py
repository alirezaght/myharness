"""Bounded real HTTP retries and observation-only provider polling."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from harness.delivery import publish_delivery
from harness.delivery_monitor import poll_delivery
from harness.gitlab import GitLabClient, GitLabError
from tests import test_delivery_monitor

delivery = test_delivery_monitor.delivery


def test_transient_get_retries_auth_and_mutation_stop_once():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            requests.append(self.path)
            self.send_response(
                403 if self.path.endswith("/denied") else (503 if len(requests) < 3 else 200)
            )
            self.end_headers()
            self.wfile.write(b"{}")

        def do_POST(self):
            requests.append("POST")
            self.send_response(503)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = GitLabClient(
            {
                "endpoint": f"http://127.0.0.1:{server.server_port}",
                "project": "x",
                "test_loopback": True,
            },
            lambda: "fixture-secret",
        )
        assert client.get("/transient") == {}
        assert len(requests) == 3
        with pytest.raises(GitLabError) as error:
            client.get("/denied")
        assert error.value.status == 403
        assert len(requests) == 4
        with pytest.raises(GitLabError) as error:
            client.request("POST", "/effect", {})
        assert error.value.uncertain
        assert requests.count("POST") == 1
    finally:
        server.shutdown()
        server.server_close()


def test_browser_prerequisite_poll_never_routes_correction(delivery, monkeypatch):
    controller, eid, api = delivery
    publish_delivery(controller, eid, client=api)
    api.pipeline["status"] = "failed"
    monkeypatch.setattr(
        "harness.delivery_routing.route_failure",
        lambda *a: (_ for _ in ()).throw(AssertionError("must not route")),
    )
    view = poll_delivery(controller, eid, client=api, route_failures=False)
    assert view["pipeline"]["status"] == "failed"
