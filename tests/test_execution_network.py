"""Real provider HTTP transport and policy denial, with simulated upstream only."""

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from harness.execution_isolation import contained_argv, prepare_isolation
from harness.execution_network import provider_transport

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="macOS whole-process compatibility sandbox"
)


@pytest.mark.parametrize("provider,path", [("codex", "/v1/responses"), ("claude", "/v1/messages")])
def test_provider_proxy_real_transport_and_containment(tmp_path, provider, path):
    observed = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            observed.append(
                (self.path, self.headers.get("Authorization"), self.headers.get("x-api-key"))
            )
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with provider_transport(
            {
                "provider": provider,
                "endpoint": f"http://127.0.0.1:{server.server_port}",
                "credential": "upstream-secret",
                "test_loopback": True,
            }
        ) as transport:
            request = Request(
                transport["url"] + path,
                data=b"{}",
                headers={"Authorization": "Bearer " + transport["token"]},
            )
            assert json.load(urlopen(request))["ok"]
            with pytest.raises(HTTPError):
                urlopen(
                    Request(
                        transport["url"] + "/arbitrary",
                        data=b"{}",
                        headers={"Authorization": "Bearer " + transport["token"]},
                    )
                )
            repo, sealed = tmp_path / "repo", tmp_path / "sealed"
            repo.mkdir()
            sealed.mkdir()
            db, handoff = sealed / "state.db", sealed / "handoff.json"
            db.touch()
            handoff.touch()
            runtime = repo / ".codex/al-runtime"
            runtime.mkdir(parents=True)
            source, lock = runtime / "exact.json", sealed / "binding.lock"
            source.touch()
            lock.touch()
            profile = prepare_isolation(
                {"handoff_path": str(handoff), "al_session_id": "exact"},
                {"repository": str(repo), "database": str(db)},
                {"source": str(source), "coordination_lock": str(lock)},
                transport,
            )
            script = (
                "import urllib.request,sys; "
                "r=urllib.request.Request(sys.argv[1],data=b'{}',"
                "headers={'Authorization':sys.argv[2]});"
                "print(urllib.request.urlopen(r).read().decode())"
            )
            result = subprocess.run(
                contained_argv(
                    profile,
                    [
                        sys.executable,
                        "-I",
                        "-c",
                        script,
                        transport["url"] + path,
                        "Bearer " + transport["token"],
                    ],
                ),
                capture_output=True,
            )
            assert result.returncode == 0, result.stderr
            assert b'"ok":true' in result.stdout
            denied = subprocess.run(
                contained_argv(
                    profile,
                    [
                        sys.executable,
                        "-I",
                        "-c",
                        script,
                        f"http://127.0.0.1:{server.server_port}" + path,
                        "Bearer arbitrary",
                    ],
                ),
                capture_output=True,
            )
            assert denied.returncode != 0
        assert len(observed) == 2
        assert observed[0][1 if provider == "codex" else 2] in {
            "Bearer upstream-secret",
            "upstream-secret",
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
