"""P2-02: the live stream /api/stream pushes the read model whenever committed state changes."""

import http.client
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from harness.dashboard import server as server_module
from harness.dashboard.server import LOCALHOST, RETRY_MS, SECURITY_HEADERS, DashboardServer
from tests.test_dashboard import api, cli, fetch, serve, start_task


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(DashboardServer, "poll_seconds", 0.05)


@contextmanager
def stream(server: DashboardServer, query: str = "") -> Iterator[http.client.HTTPResponse]:
    conn = http.client.HTTPConnection(LOCALHOST, server.port, timeout=10)
    try:
        conn.request("GET", f"/api/stream{query}")
        yield conn.getresponse()
    finally:
        conn.close()


def frame(response: http.client.HTTPResponse) -> dict[str, str] | None:
    """The next server-sent event as {field: value}; None at the end of the stream."""
    fields: dict[str, str] = {}
    while True:
        line = response.fp.readline().decode("utf-8")
        if not line:
            return None
        line = line.rstrip("\n")
        if not line:
            return fields
        name, _, value = line.partition(":")
        fields[name] = value.removeprefix(" ")


def frames(response: http.client.HTTPResponse) -> list[dict[str, str]]:
    collected = []
    while (event := frame(response)) is not None:
        collected.append(event)
    return collected


def test_stream_pushes_the_view_on_every_change_only(db_path):
    task_id = start_task(db_path, "csv", "export")
    with serve(db_path, task_id) as server, stream(server) as response:
        assert response.status == 200
        assert response.getheader("Content-Type") == "text/event-stream; charset=utf-8"
        for name, value in SECURITY_HEADERS.items():
            assert response.getheader(name) == value
        assert frame(response) == {"retry": str(RETRY_MS)}
        first = frame(response)
        assert set(first) == {"data"} and json.loads(first["data"]) == api(server)
        cli(db_path, "phase", task_id, "next")
        pushed = frame(response)
        assert json.loads(pushed["data"])["task"]["phase"] == "interview"
        assert json.loads(pushed["data"]) == api(server)


def test_stream_ends_after_its_lifetime_without_duplicates(db_path, monkeypatch):
    monkeypatch.setattr(server_module, "KEEPALIVE_SECONDS", 0.2)
    other = start_task(db_path, "other", "task")
    task_id = start_task(db_path, "csv", "export")
    with serve(db_path, task_id) as server:
        server.stream_lifetime = 1.0
        with stream(server, f"?task={other}") as response:
            events = frames(response)
        expected = api(server, f"?task={other}")
    assert events[0] == {"retry": str(RETRY_MS)}
    data = [e for e in events if "data" in e]
    assert len(data) == 1 and json.loads(data[0]["data"]) == expected
    assert expected["task"]["id"] == other
    keepalives = [e for e in events if "data" not in e and "retry" not in e]
    assert keepalives and all(e == {"": "keep-alive"} for e in keepalives)


def test_stream_refuses_a_foreign_host(db_path):
    task_id = start_task(db_path, "secret", "request")
    with serve(db_path, task_id) as server:
        status, headers, body = fetch(server, "/api/stream", host="evil.example:80")
    assert status == 403 and b"secret" not in body
    assert headers["Content-Security-Policy"] == SECURITY_HEADERS["Content-Security-Policy"]


def test_read_failure_is_one_frame_and_recovers(db_path, tmp_path):
    task_id = start_task(db_path, "csv", "export")
    specs = tmp_path / "specs"
    broken = specs / task_id / "v1.md"
    with serve(db_path, task_id, str(specs)) as server, stream(server) as response:
        assert frame(response) == {"retry": str(RETRY_MS)}
        assert json.loads(frame(response)["data"])["task"]["id"] == task_id
        broken.parent.mkdir(parents=True)
        broken.write_text("not a specification\n", encoding="utf-8")
        failure = frame(response)
        assert failure["event"] == "failure" and set(json.loads(failure["data"])) == {"error"}
        broken.unlink()
        recovered = frame(response)
        assert set(recovered) == {"data"}
        assert json.loads(recovered["data"])["task"]["id"] == task_id


def test_server_close_ends_open_streams(db_path):
    task_id = start_task(db_path, "csv", "export")
    server = DashboardServer(db_path, task_id)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with stream(server) as response:
            assert frame(response) == {"retry": str(RETRY_MS)}
            assert "data" in frame(response)
            server.shutdown()
            server.server_close()
            assert frame(response) is None
    finally:
        thread.join(timeout=10)
    assert server.closing.is_set()
