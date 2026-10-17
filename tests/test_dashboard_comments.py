"""P2-05: POST /api/comments, the comments in the read model and the request hygiene around them."""

import http.client
import json
import re
import socket
import sqlite3
from pathlib import Path

import pytest

from harness.comments import CommentService
from harness.dashboard import comments_view
from harness.dashboard.server import LOCALHOST, SECURITY_HEADERS, STATIC_DIR, DashboardServer
from harness.db import transaction
from harness.design_models import DesignSection
from tests.test_dashboard import _db_dump, api, fetch, serve, start_task
from tests.test_dashboard_diagrams import _sample, _state, _with
from tests.test_dashboard_stream import frame, stream

EDGE = "connection:browser->local-server"
RID = "req-00000001"
DECLARED = re.compile(r"^(?:const|let|var|(?:async )?function)\s+(\w+)", re.MULTILINE)


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(DashboardServer, "poll_seconds", 0.05)


def _payload(task_id: str, **changes) -> dict:
    body = {"task": task_id, "version": 1, "element": EDGE, "kind": "correction",
            "text": "<b>wrong</b> arrow", "request_id": RID}  # fmt: skip
    return body | changes


def post(server: DashboardServer, body, headers: dict | None = None, shut: bool = False,
         path: str = "/api/comments", method: str = "POST") -> tuple[int, dict, bytes]:  # fmt: skip
    """A raw request; headers override the defaults (None drops one, a list repeats it)."""
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    sent = {
        "Host": f"{LOCALHOST}:{server.port}",
        "Origin": f"http://{LOCALHOST}:{server.port}",
        "Content-Type": "application/json",
        "Content-Length": str(len(raw)),
    } | (headers or {})
    lines = [f"{method} {path} HTTP/1.1"]
    for name, value in sent.items():
        for one in value if isinstance(value, list) else [value]:
            if one is not None:
                lines.append(f"{name}: {one}")
    sock = socket.create_connection((LOCALHOST, server.port), timeout=5)
    try:
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + raw)
        if shut:
            sock.shutdown(socket.SHUT_WR)
        response = http.client.HTTPResponse(sock)
        response.begin()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        sock.close()


def _json(result: tuple[int, dict, bytes]) -> tuple[int, dict]:
    status, headers, body = result
    for name, value in SECURITY_HEADERS.items():
        assert headers[name] == value
    return status, json.loads(body)


def _ready(db_path: Path) -> str:
    task_id = _sample(db_path)
    with serve(db_path, task_id) as server:
        assert api(server)["task"]["diagrams"]["current"]["version"] == 1
    return task_id


def _diff(before: list[str], after: list[str]) -> tuple[list[str], list[str]]:
    plain = [line for line in after if "sqlite_sequence" not in line]
    added = sorted(set(plain) - set(before))
    removed = sorted(set(before) - set(after))
    return added, removed


def test_a_comment_is_added_stored_with_its_event_and_shown(db_path):
    task_id = _ready(db_path)
    dump = _db_dump(db_path)
    with serve(db_path, task_id) as server:
        status, data = _json(post(server, _payload(task_id)))
        after = _db_dump(db_path)
        replay = _json(post(server, _payload(task_id)))
        conflict = _json(post(server, _payload(task_id, text="other")))
        unchanged = _db_dump(db_path)
        section = api(server)["task"]["comments"]
    comment = data["comment"]
    assert status == 201 and comment["id"] == 1
    assert (comment["version"], comment["element"], comment["kind"]) == (1, EDGE, "correction")
    assert (comment["status"], comment["text"]) == ("open", "<b>wrong</b> arrow")
    added, removed = _diff(dump, after)
    assert len(added) == 2 and added[0].startswith('INSERT INTO "comments" VALUES(1,')
    assert added[1].startswith('INSERT INTO "events"') and "comment.added" in added[1]
    assert f"'{RID}'" in added[0] and all("sqlite_sequence" in line for line in removed)
    assert replay == (200, data)
    assert conflict[0] == 409 and "already used for comment 1" in conflict[1]["error"]
    assert "reload" not in conflict[1] and unchanged == _db_dump(db_path) == after
    assert section["kinds"] == ["clarification", "correction", "redesign"]
    assert section["counts"] == {"open": 1, "addressed": 0, "resolved": 0}
    labels = {"element_kind": "connection", "element_label": "Browser -> Local Server"}
    assert section["items"] == [comment | labels]


def test_a_comment_on_an_outdated_version_is_refused_with_a_reload_hint(db_path):
    task_id = _ready(db_path)
    _state(db_path, task_id, "component Exporter")
    dump = _db_dump(db_path)
    with serve(db_path, task_id) as server:
        status, data = _json(post(server, _payload(task_id)))
        assert (status, data["reload"]) == (409, True) and "reload" in data["error"]
        assert _db_dump(db_path) == dump
        assert api(server)["task"]["diagrams"]["current"]["version"] == 2
        status, data = _json(post(server, _payload(task_id, request_id="req-00000002")))
        assert (status, data["reload"]) == (409, True)
        assert data["error"].startswith("diagram version 1 is no longer current (current: vers")
        new = _payload(task_id, version=2, request_id="req-00000003")
        assert _json(post(server, new))[0] == 201


def test_unknown_tasks_versions_and_elements_are_404(db_path, tmp_path):
    task_id = _ready(db_path)
    dump = _db_dump(db_path)
    with serve(db_path, task_id) as server:
        for changes, error in (
            ({"version": 2}, f"unknown diagram version 2 for task {task_id}"),
            ({"version": 2**64}, f"unknown diagram version {2**64} for task {task_id}"),
            ({"element": "component:nope"}, "element component:nope is not in diagram version 1"),
            ({"task": "t-00000000"}, "unknown task: t-00000000"),
        ):
            assert _json(post(server, _payload(task_id, **changes))) == (404, {"error": error})
    assert _db_dump(db_path) == dump
    missing = tmp_path / "absent" / "state.db"
    with serve(missing, task_id) as server:
        assert _json(post(server, _payload(task_id)))[0] == 404
    assert not missing.exists() and not missing.parent.exists()


BAD_BODIES = [
    b"\xff\xfe",
    b"not json",
    b"[]",
    b'{"task": "t", "task": "t"}',
    b"",
]


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"version": 1.0},
        {"version": 0},
        {"version": "1"},
        {"element": ""},
        {"element": 3},
        {"task": ""},
        {"kind": "praise"},
        {"kind": None},
        {"text": " \n\t "},
        {"text": 5},
        {"request_id": "short"},
        {"request_id": f"{RID}\n"},
        {"request_id": "req_00000001"},
        {"request_id": "r" * 65},
        {"extra": 1},
    ],
)
def test_an_invalid_body_is_a_400_and_writes_nothing(db_path, changes):
    task_id = _ready(db_path)
    dump = _db_dump(db_path)
    with serve(db_path, task_id) as server:
        status, data = _json(post(server, _payload(task_id, **changes)))
        missing = _payload(task_id)
        del missing["kind"]
        assert _json(post(server, missing))[0] == 400
        for raw in BAD_BODIES:
            assert _json(post(server, raw)) == (400, {"error": "body must be a UTF-8 JSON object"})
    assert status == 400 and set(data) == {"error"}
    assert _db_dump(db_path) == dump


def test_headers_are_checked_before_the_body_is_read(db_path):
    task_id = _ready(db_path)
    dump = _db_dump(db_path)
    with serve(db_path, task_id) as server:
        port = server.port
        unread = {"Content-Length": "10"}
        for headers, status in (
            ({"Origin": None}, 403),
            ({"Origin": "http://evil.example"}, 403),
            ({"Origin": "null"}, 403),
            ({"Origin": f"https://{LOCALHOST}:{port}"}, 403),
            ({"Sec-Fetch-Site": "cross-site"}, 403),
            ({"Sec-Fetch-Site": "same-site"}, 403),
            ({"Content-Type": "text/plain"}, 415),
            ({"Content-Type": "application/x-www-form-urlencoded"}, 415),
            ({"Content-Type": "multipart/form-data; boundary=x"}, 415),
            ({"Content-Type": None}, 415),
        ):
            result = post(server, b"", headers | unread)
            assert _json(result)[0] == status, headers
        status, headers, body = post(server, b"", {"Host": "evil.example:80"} | unread)
        assert status == 403 and body == b"forbidden: unexpected Host header\n"
        assert _json(post(server, b"", {"Transfer-Encoding": "chunked"}))[0] == 411
        assert _json(post(server, b"", {"Content-Length": None}))[0] == 411
        for length in ("abc", "-1", "1e3", ["5", "5"], "1" * 19):
            assert _json(post(server, b"", {"Content-Length": length}))[0] == 400, length
        short = post(server, b'{"task"', {"Content-Length": "100"}, shut=True)
        assert _json(short) == (400, {"error": "request body is incomplete"})
        status, headers, _ = post(server, _payload(task_id), path="/api/dashboard")
        assert status == 501 and headers["Content-Security-Policy"]
        for method in ("PUT", "OPTIONS"):
            status, headers, _ = post(server, _payload(task_id), method=method)
            assert status == 501 and headers["X-Content-Type-Options"] == "nosniff"
        alike = {"Origin": f"http://localhost:{port}", "Sec-Fetch-Site": "same-origin",
                 "Content-Type": "application/json; charset=utf-8"}  # fmt: skip
        assert _json(post(server, _payload(task_id), alike))[0] == 201
    after = _db_dump(db_path)
    assert len(_diff(dump, after)[0]) == 2


def test_a_long_comment_is_accepted_without_a_limit(db_path):
    task_id = _ready(db_path)
    text = "x" * (1024 * 1024) + "\n<script>end</script>"
    with serve(db_path, task_id) as server:
        status, data = _json(post(server, _payload(task_id, text=text)))
        shown = api(server)["task"]["comments"]["items"][0]
    assert status == 201 and data["comment"]["text"] == text == shown["text"]


def test_a_locked_database_is_a_503_and_the_retry_is_saved_once(db_path, monkeypatch):
    task_id = _ready(db_path)
    holder = sqlite3.connect(db_path, autocommit=True, check_same_thread=False)

    class LockedOut(CommentService):
        def add(self, *args, **kwargs):
            self.conn.execute("PRAGMA busy_timeout=100")
            holder.execute("BEGIN IMMEDIATE")
            try:
                return super().add(*args, **kwargs)
            finally:
                holder.execute("ROLLBACK")

    dump = _db_dump(db_path)
    with serve(db_path, task_id) as server:
        monkeypatch.setattr(comments_view, "CommentService", LockedOut)
        try:
            status, data = _json(post(server, _payload(task_id)))
        finally:
            holder.close()
        assert _db_dump(db_path) == dump
        monkeypatch.undo()
        saved = _json(post(server, _payload(task_id)))
        again = _json(post(server, _payload(task_id)))
    assert (status, data["retry"]) == (503, True) and "locked" in data["error"]
    assert saved[0] == 201 and again == (200, saved[1])
    assert len(api_items(db_path, task_id)) == 1


def api_items(db_path: Path, task_id: str) -> list[dict]:
    with serve(db_path, task_id) as server:
        return api(server)["task"]["comments"]["items"]


def test_reads_write_nothing_with_comments_and_the_stream_pushes_a_new_one(db_path):
    task_id = _ready(db_path)
    with serve(db_path, task_id) as server:
        with stream(server) as response:
            assert "retry" in frame(response)
            first = json.loads(frame(response)["data"])["task"]["comments"]
            assert _json(post(server, _payload(task_id)))[0] == 201
            pushed = json.loads(frame(response)["data"])["task"]["comments"]
        dump = _db_dump(db_path)
        server.stream_lifetime = 0.3
        for path in ("/", "/static/comments.js", "/api/dashboard", "/api/stream",
                     f"/api/dashboard?task={task_id}", "/api/diagrams?version=1"):  # fmt: skip
            fetch(server, path)
    assert first["items"] == [] and first["counts"]["open"] == 0
    assert [c["element"] for c in pushed["items"]] == [EDGE]
    assert _db_dump(db_path) == dump


def test_a_comment_on_a_removed_element_keeps_its_own_version_label(db_path):
    """Q7c: the element left the design; the comment is listed with its version 1 label."""
    task_id = start_task(db_path, "remove", "element")
    _state(db_path, task_id, "component A\ncomponent B")
    with serve(db_path, task_id) as server:
        api(server)
        body = _payload(task_id, element="component:a", text="drop A")
        assert _json(post(server, body))[0] == 201

        def withdraw(controller):
            ws = controller.workspace(task_id)
            with transaction(controller.conn):
                for entry in ws.design_entries():
                    ws.withdraw_design(entry.id)
                ws.state_design(DesignSection.COMPONENTS, "component B")

        _with(db_path, withdraw)
        task = api(server)["task"]
    assert task["diagrams"]["current"]["version"] == 2
    [item] = task["comments"]["items"]
    assert (item["version"], item["element_kind"], item["element_label"]) == (1, "component", "A")


def test_the_comment_script_is_served_in_order_and_declares_no_duplicate_globals(db_path):
    with serve(db_path, None) as server:
        status, headers, script = fetch(server, "/static/comments.js")
        page = fetch(server, "/")[2]
    assert status == 200 and headers["Content-Type"] == "text/javascript; charset=utf-8"
    assert b"/api/comments" in script and b"createElement" in script
    tags = [f'<script src="/static/{n}.js" defer></script>'.encode() for n in
            ("diagrams", "comments", "app")]  # fmt: skip
    assert page.index(tags[0]) < page.index(tags[1]) < page.index(tags[2])
    seen: dict[str, str] = {}
    for name in ("diagrams.js", "comments.js", "app.js"):
        for declared in DECLARED.findall((STATIC_DIR / name).read_text(encoding="utf-8")):
            assert declared not in seen, f"{declared} in {name} and {seen.get(declared)}"
            seen[declared] = name
