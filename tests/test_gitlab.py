"""Real HTTP API contract checks without live credentials."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from harness.gitlab import GitLabClient, GitLabError


@pytest.fixture
def api_server():
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            observed.append((self.path, self.headers.get("PRIVATE-TOKEN")))
            parsed = urlsplit(self.path)
            if parsed.path.endswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", "/escape")
                self.end_headers()
                return
            if parsed.path.endswith("/denied"):
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"super-secret")
                return
            page = parse_qs(parsed.query).get("page", ["1"])[0]
            self.send_response(200)
            if page == "1":
                self.send_header("X-Next-Page", "2")
            self.end_headers()
            self.wfile.write(json.dumps([{"page": page, "text": "super-secret"}]).encode())

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"super-secret")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            {
                "endpoint": f"http://127.0.0.1:{server.server_port}",
                "project": "group/project",
                "test_loopback": True,
            },
            observed,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_protocol_encoding_pagination_and_secret_redaction(api_server):
    config, observed = api_server
    client = GitLabClient(config, lambda: "super-secret")
    rows = client.list_pages("/pipelines/7/jobs")
    assert [r["page"] for r in rows] == ["1", "2"]
    assert "super-secret" not in str(rows)
    assert all("group%2Fproject" in path and token == "super-secret" for path, token in observed)


def test_redirect_auth_and_ambiguous_mutation_do_not_retry(api_server):
    config, observed = api_server
    client = GitLabClient(config, lambda: "super-secret")
    for path in ("/redirect", "/denied"):
        with pytest.raises(GitLabError) as error:
            client.get(path)
        assert "super-secret" not in str(error.value)
    assert len(observed) == 2
    with pytest.raises(GitLabError) as error:
        client.create_branch("candidate", "a" * 40)
    assert error.value.uncertain
