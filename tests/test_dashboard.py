"""P2-01: the dashboard server and read model over the CLI's own state DB and spec files."""

import http.client
import inspect
import io
import json
import re
import shutil
import socket
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from harness.classification import Complexity
from harness.cli import main
from harness.controller import HarnessController
from harness.dashboard.server import LOCALHOST, DashboardServer
from harness.dashboard.view import NO_ACTIVE_TASK, open_state
from harness.db import connect
from tests.complete_task import settle
from tests.sample_repo import tree_snapshot

TASK_ID = re.compile(r"task (t-[0-9a-f]{8}) created")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@contextmanager
def serve(
    db_path: Path, task_id: str | None, specs: str | None = None
) -> Iterator[DashboardServer]:
    server = DashboardServer(db_path, task_id, specs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def fetch(
    server: DashboardServer, path: str, method: str = "GET", host: str | None = None
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection(LOCALHOST, server.port, timeout=10)
    try:
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host or f"{LOCALHOST}:{server.port}")
        conn.endheaders()
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def api(server: DashboardServer) -> dict:
    status, _, body = fetch(server, "/api/dashboard")
    assert status == 200, body
    return json.loads(body)


def cli(db_path: Path, *argv: str) -> str:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["--db", str(db_path), *argv])
    assert (code, err.getvalue()) == (0, ""), err.getvalue()
    return out.getvalue()


def start_task(db_path: Path, *words: str) -> str:
    return TASK_ID.search(cli(db_path, "start", "--complexity", "trivial", *words)).group(1)


def _db_dump(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def _non_loopback_ipv4() -> str | None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        address = probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()
    return None if address.startswith("127.") or address == "0.0.0.0" else address


def test_binds_loopback_only_on_a_free_port(db_path):
    params = list(inspect.signature(DashboardServer.__init__).parameters)
    assert params == ["self", "db_path", "task_id", "specs"]
    with serve(db_path, None) as first, serve(db_path, None) as second:
        assert first.server_address[0] == "127.0.0.1" and first.port > 0
        assert first.url == f"http://127.0.0.1:{first.port}/"
        assert first.port != second.port
        assert fetch(first, "/")[0] == 200
        other = _non_loopback_ipv4()
        if other is None:
            pytest.skip("no non-loopback IPv4 address to probe")
        with pytest.raises(OSError):
            socket.create_connection((other, first.port), timeout=2).close()


def test_dashboard_shows_the_active_task_from_the_cli_db(db_path):
    task_id = start_task(db_path, "csv", "export")
    conn = connect(db_path)
    try:
        task = HarnessController(conn).get_task(task_id)
    finally:
        conn.close()
    with serve(db_path, task_id) as server:
        data = api(server)
        status, headers, page = fetch(server, "/")
        script = fetch(server, "/static/app.js")
    assert data["notice"] is None and data["tasks"] == []
    shown = data["task"]
    assert (shown["id"], shown["request"], shown["phase"]) == (task.id, task.request, task.phase)
    assert (shown["complexity"], shown["complexity_source"]) == ("trivial", "developer")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert b'<script src="/static/app.js" defer></script>' in page
    assert script[0] == 200 and b"/api/dashboard" in script[2]


def test_cli_change_is_visible_on_reload(db_path):
    task_id = start_task(db_path, "csv", "export")
    with serve(db_path, task_id) as server:
        before = api(server)["task"]
        cli(db_path, "classify", task_id, "complex")
        cli(db_path, "phase", task_id, "next")
        after = api(server)["task"]
    assert (before["phase"], before["complexity"]) == ("investigation", "trivial")
    assert (after["phase"], after["complexity"]) == ("interview", "complex")


@needs_git
def test_reads_the_same_specs_as_the_cli(db_path, sample_repo, tmp_path):
    task_id = start_task(db_path, "csv", "export")
    cli(db_path, "investigate", task_id, "--repo", str(sample_repo))
    cli(db_path, "phase", task_id, "next")
    cli(db_path, "spec", "draft", task_id)
    conn = connect(db_path)
    try:
        settle(HarnessController(conn), task_id)
    finally:
        conn.close()
    cli(db_path, "phase", task_id, "next")
    cli(db_path, "spec", "approve", task_id)
    cli(db_path, "spec", "draft", task_id)
    listed = json.loads(cli(db_path, "spec", "list", task_id, "--json"))
    with serve(db_path, task_id) as server:
        spec = api(server)["task"]["spec"]
    with serve(db_path, task_id, str(tmp_path / "empty-specs")) as server:
        empty = api(server)["task"]["spec"]
    assert spec["directory"] == str(sample_repo / "docs" / "specs" / task_id)
    versions = [{"version": r["version"], "status": r["status"]} for r in listed if r["version"]]
    assert spec["versions"] == versions == [{"version": 1, "status": "approved"}]
    assert spec["draft"] is any(r["version"] is None for r in listed) is True
    assert (empty["versions"], empty["draft"]) == ([], False)


@needs_git
def test_writes_nothing_and_creates_nothing(db_path, sample_repo, isolated_home, tmp_path):
    task_id = start_task(db_path, "csv", "export")
    cli(db_path, "investigate", task_id, "--repo", str(sample_repo))
    dump, repo, home = _db_dump(db_path), tree_snapshot(sample_repo), tree_snapshot(isolated_home)
    names = sorted(p.name for p in db_path.parent.iterdir())
    with serve(db_path, task_id) as server:
        for path in ("/", "/static/app.js", "/static/app.css", "/api/dashboard", "/nope"):
            fetch(server, path)
        api(server)
    assert _db_dump(db_path) == dump
    assert (tree_snapshot(sample_repo), tree_snapshot(isolated_home)) == (repo, home)
    assert sorted(p.name for p in db_path.parent.iterdir()) == names

    missing = tmp_path / "absent" / "state.db"
    with serve(missing, task_id) as server:
        data = api(server)
    assert (data["task"], data["tasks"]) == (None, [])
    assert not missing.exists() and not missing.parent.exists()


def _stamp(db_path: Path, task_id: str, updated_at: str) -> None:
    conn = connect(db_path)
    try:
        conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (updated_at, task_id))
    finally:
        conn.close()


def test_no_or_unknown_active_task_lists_the_tasks(db_path, tmp_path):
    newest = start_task(db_path, "newest", "task")
    tied = [start_task(db_path, "tied", "task", str(n)) for n in (1, 2)]
    cli(db_path, "phase", newest, "next")
    _stamp(db_path, newest, "2026-01-02T00:00:00+00:00")
    for task_id in tied:
        _stamp(db_path, task_id, "2026-01-01T00:00:00+00:00")
    with serve(db_path, "t-00000000") as server:
        unknown = api(server)
    with serve(db_path, None) as server:
        none = api(server)
    assert unknown["task"] is None and unknown["notice"] == "unknown task: t-00000000"
    assert [t["id"] for t in unknown["tasks"]] == [newest, *sorted(tied)]
    assert unknown["tasks"][0]["phase"] == "interview"
    assert set(unknown["tasks"][0]) == {"id", "phase", "request", "updated_at"}
    assert none["notice"] == NO_ACTIVE_TASK and none["tasks"] == unknown["tasks"]

    empty_db = tmp_path / "empty" / "state.db"
    connect(empty_db).close()
    with serve(empty_db, None) as server:
        assert api(server)["tasks"] == []
    with serve(tmp_path / "none" / "state.db", None) as server:
        assert api(server) == {"task": None, "notice": NO_ACTIVE_TASK, "tasks": []}


def test_foreign_host_header_is_refused(db_path):
    task_id = start_task(db_path, "secret", "request")
    with serve(db_path, task_id) as server:
        for path in ("/", "/api/dashboard"):
            status, _, body = fetch(server, path, host="evil.example:80")
            assert status == 403 and b"secret" not in body and task_id.encode() not in body
        assert fetch(server, "/api/dashboard", host=f"evil.example:{server.port}")[0] == 403
        assert fetch(server, "/api/dashboard", host=f"localhost:{server.port}")[0] == 200
        assert fetch(server, "/api/dashboard", host=f"127.0.0.1:{server.port}")[0] == 200


def test_routing_hygiene(db_path, tmp_path):
    task_id = start_task(db_path, "csv", "export")
    specs = tmp_path / "specs"
    with serve(db_path, task_id, str(specs)) as server:
        assert fetch(server, "/api/dashboard", method="POST")[0] == 501
        assert fetch(server, "/nope")[0] == 404
        assert fetch(server, "/static/../view.py")[0] == 404
        assert fetch(server, "/static/index.html")[0] == 404
        status, headers, _ = fetch(server, "/api/dashboard?x=1")
        assert status == 200
        assert headers["Content-Security-Policy"] == "default-src 'self'; frame-ancestors 'none'"
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
        (specs / task_id).mkdir(parents=True)
        (specs / task_id / "v1.md").write_text("not a specification\n", encoding="utf-8")
        status, headers, body = fetch(server, "/api/dashboard")
    assert status == 500 and headers["Content-Type"].startswith("application/json")
    assert set(json.loads(body)) == {"error"}


def test_state_seam_is_the_cli_writable_store(db_path):
    task_id = start_task(db_path, "csv", "export")
    with open_state(db_path) as controller:
        controller.set_complexity(task_id, Complexity.COMPLEX)
    shown = cli(db_path, "show", task_id)
    assert f"task {task_id} · phase investigation · complexity complex (developer)" in shown
