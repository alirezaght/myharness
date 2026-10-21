import http.client
import json
import threading

from harness.dashboard.server import DashboardServer
from harness.execution_logs import append_log
from harness.execution_store import get_execution
from tests.execution_helpers import approved_execution


def test_execution_get_does_not_launch_and_logs_are_scoped(
    controller, sample_repo, monkeypatch, db_path
):
    tid, eid = approved_execution(controller, sample_repo, monkeypatch)
    log = append_log(controller.conn, eid, "worker", "api_key=private diagnostics")
    server = DashboardServer(db_path, tid)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = http.client.HTTPConnection("127.0.0.1", server.port)
        client.request("GET", f"/api/execution?task={tid}&execution={eid}")
        response = client.getresponse()
        data = json.loads(response.read())
        assert response.status == 200 and data["id"] == eid
        client.request("GET", f"/api/execution/logs?task={tid}&execution={eid}&log={log}")
        response = client.getresponse()
        body = response.read()
        assert response.status == 200 and b"private" not in body and b"redacted" in body
        client.request(
            "POST",
            "/api/execution/reconnect",
            body="{}",
            headers={"Content-Type": "application/json", "Origin": "http://foreign.invalid"},
        )
        response = client.getresponse()
        response.read()
        assert response.status == 403
        client.request(
            "POST",
            "/api/execution/reconnect",
            body="{bad json",
            headers={"Content-Type": "application/json", "Origin": server.url.rstrip("/")},
        )
        response = client.getresponse()
        response.read()
        assert response.status == 400
        client.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert get_execution(controller.conn, eid)["status"] == "Reserved"
    assert controller.conn.execute("SELECT COUNT(*) FROM execution_attempts").fetchone()[0] == 0
