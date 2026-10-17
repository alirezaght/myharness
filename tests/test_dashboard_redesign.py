"""P2-06: the comparison endpoint, the resolve/re-open writes and the whole redesign loop."""

import json
import re
import sqlite3
from pathlib import Path

import pytest

from harness import redesign
from harness.comments import CommentService
from harness.dashboard import comments_view
from harness.dashboard.server import LOCALHOST, STATIC_DIR, DashboardServer
from harness.db import transaction
from harness.design_models import DesignSection
from tests.test_dashboard import _db_dump, api, cli, fetch, serve, start_task
from tests.test_dashboard_comments import DECLARED, EDGE, _diff, _json, _payload, _ready, post
from tests.test_dashboard_diagrams import _state, _with
from tests.test_dashboard_stream import frame, stream

SCRIPTS = ("diagrams", "comments", "comment_actions", "compare", "app")
MOVE_PATHS = ("/api/comments/resolve", "/api/comments/reopen")


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(DashboardServer, "poll_seconds", 0.05)


def _compare(server: DashboardServer, query: str) -> tuple[int, dict]:
    status, _, body = fetch(server, f"/api/diagrams/compare?{query}")
    return status, json.loads(body)


def _two_versions(db_path: Path) -> str:
    task_id = _ready(db_path)
    _state(db_path, task_id, "component Exporter\nconnect Exporter -> Local Server: rows")
    with serve(db_path, task_id) as server:
        assert api(server)["task"]["diagrams"]["current"]["version"] == 2
    return task_id


def _comments(db_path: Path, task_id: str) -> dict[str, int]:
    """On version 1: an open, an addressed and a resolved comment."""

    def step(controller):
        service = CommentService(controller, task_id)
        ids = {status: service.add(1, EDGE, "correction", status)[0].id for status in
               ("open", "addressed", "resolved")}  # fmt: skip
        service.address(ids["addressed"], version=1)
        service.address(ids["resolved"], version=1)
        service.resolve(ids["resolved"])
        return ids

    return _with(db_path, step)


def _move(server, task_id, comment, action="resolve", **changes):
    body = {"task": task_id, "comment": comment} | changes
    return _json(post(server, body, path=f"/api/comments/{action}"))


# T7: GET /api/diagrams/compare


def test_compare_returns_both_versions_and_their_changes_and_writes_nothing(db_path, tmp_path):
    task_id = _two_versions(db_path)
    dump = _db_dump(db_path)
    with serve(db_path, task_id) as server:
        status, data = _compare(server, "from=1&to=2")
        again = _compare(server, f"task={task_id}&from=1&to=2")
    assert status == 200 and again == (200, data)
    assert set(data) == {"from", "to", "changes", "marks", "counts"}
    assert (data["from"]["version"], data["from"]["current"]) == (1, False)
    assert (data["to"]["version"], data["to"]["current"]) == (2, True)
    assert data["from"]["diagrams"] and data["to"]["diagrams"]
    assert {"content_hash", "created_at", "types"} <= set(data["to"])
    added = {"id": "component:exporter", "kind": "component", "label": "Exporter",
             "change": "added"}  # fmt: skip
    assert added in data["changes"]
    edge = "connection:exporter->local-server"
    assert data["marks"]["to"][edge] == "added" and edge not in data["marks"]["from"]
    assert data["counts"] == {"added": 2, "removed": 0, "changed": 0}
    assert _db_dump(db_path) == dump
    missing = tmp_path / "absent" / "state.db"
    with serve(missing, task_id) as server:
        assert _compare(server, "from=1&to=2") == (404, {"error": f"unknown task: {task_id}"})
    assert not missing.exists() and not missing.parent.exists()


def test_compare_validates_the_pair(db_path):
    task_id = _two_versions(db_path)
    dump = _db_dump(db_path)
    with serve(db_path, task_id) as server:
        for query in ("", "from=1", "to=2", "from=0&to=2", "from=a&to=2", "from=1&to=-2",
                      "from=01&to=2"):  # fmt: skip
            assert _compare(server, query) == (
                400, {"error": "from and to must be positive integers"}
            ), query  # fmt: skip
        for query in ("from=2&to=2", "from=2&to=1"):
            assert _compare(server, query) == (
                400, {"error": "from must be an earlier version than to"}
            )  # fmt: skip
        for query, number in (("from=1&to=3", 3), (f"from=1&to={2**64}", 2**64)):
            assert _compare(server, query) == (
                404, {"error": f"unknown diagram version {number} for task {task_id}"}
            )  # fmt: skip
        assert _compare(server, "task=t-00000000&from=1&to=2") == (
            404, {"error": "unknown task: t-00000000"}
        )  # fmt: skip
        status, _, body = fetch(server, "/api/diagrams/compare?from=1&to=2", host="evil.ex:80")
        assert status == 403 and body == b"forbidden: unexpected Host header\n"
    assert _db_dump(db_path) == dump
    with serve(db_path, None) as server:
        assert _compare(server, "from=1&to=2") == (404, {"error": "no active task"})


# T8: POST /api/comments/resolve and /api/comments/reopen


def test_resolve_and_reopen_move_a_comment_once_with_its_event(db_path):
    task_id = _ready(db_path)
    ids = _comments(db_path, task_id)
    dump = _db_dump(db_path)
    with serve(db_path, task_id) as server:
        status, data = _move(server, task_id, ids["addressed"])
        after = _db_dump(db_path)
        repeat = _move(server, task_id, ids["addressed"])
        unchanged = _db_dump(db_path)
        reopened = _move(server, task_id, ids["resolved"], "reopen")
        same = _move(server, task_id, ids["open"], "reopen")
    assert status == 200 and data["changed"] is True
    assert (data["comment"]["id"], data["comment"]["status"]) == (ids["addressed"], "resolved")
    added, removed = _diff(dump, after)
    assert [line.split(" VALUES")[0] for line in added] == [
        'INSERT INTO "comments"', 'INSERT INTO "events"'
    ]  # fmt: skip
    assert "'resolved'" in added[0] and "comment.resolved" in added[1]
    assert [line for line in removed if "sqlite_sequence" not in line] == [
        line
        for line in dump
        if line.startswith(f'INSERT INTO "comments" VALUES({ids["addressed"]},')
    ]
    assert repeat[0] == 200 and repeat[1]["changed"] is False and unchanged == after
    assert reopened[0] == 200 and reopened[1]["comment"]["status"] == "open"
    assert same[0] == 200 and same[1]["changed"] is False


def test_illegal_unknown_and_other_tasks_comments_are_refused(db_path):
    task_id = _ready(db_path)
    ids = _comments(db_path, task_id)
    other = start_task(db_path, "other")
    dump = _db_dump(db_path)
    with serve(db_path, task_id) as server:
        status, data = _move(server, task_id, ids["open"])
        assert status == 409 and set(data) == {"error"}
        assert data["error"] == (
            f"comment {ids['open']} is open: only an addressed comment can be resolved"
        )
        assert _move(server, other, ids["addressed"]) == (
            404, {"error": f"unknown comment {ids['addressed']} for task {other}"}
        )  # fmt: skip
        for comment in (99, 2**64):
            assert _move(server, task_id, comment, "reopen") == (
                404, {"error": f"unknown comment {comment} for task {task_id}"}
            )  # fmt: skip
        assert _move(server, "t-00000000", 1) == (404, {"error": "unknown task: t-00000000"})
        result = post(server, {"task": task_id, "comment": ids["open"]},
                      path="/api/comments/address")  # fmt: skip
        assert result[0] == 501
    assert _db_dump(db_path) == dump


@pytest.mark.parametrize(
    "changes",
    [
        {"comment": True},
        {"comment": 0},
        {"comment": -1},
        {"comment": "1"},
        {"comment": 1.0},
        {"task": ""},
        {"task": 3},
        {"extra": 1},
    ],  # fmt: skip
)
def test_an_invalid_move_body_is_a_400(db_path, changes):
    task_id = _ready(db_path)
    ids = _comments(db_path, task_id)
    dump = _db_dump(db_path)
    with serve(db_path, task_id) as server:
        for path in MOVE_PATHS:
            body = {"task": task_id, "comment": ids["addressed"]} | changes
            status, data = _json(post(server, body, path=path))
            assert status == 400 and set(data) == {"error"}
            dup = f'{{"task": "{task_id}", "comment": 1, "comment": 1}}'.encode()
            assert _json(post(server, dup, path=path)) == (
                400, {"error": "body must be a UTF-8 JSON object"}
            )  # fmt: skip
            assert _json(post(server, {"task": task_id}, path=path))[0] == 400
            assert _json(post(server, b"[]", path=path))[0] == 400
    assert _db_dump(db_path) == dump


def test_move_headers_are_checked_before_the_body_like_add(db_path):
    task_id = _ready(db_path)
    ids = _comments(db_path, task_id)
    dump = _db_dump(db_path)
    with serve(db_path, task_id) as server:
        port, unread = server.port, {"Content-Length": "10"}
        for path in MOVE_PATHS:
            for headers, status in (
                ({"Origin": None}, 403),
                ({"Origin": "http://evil.example"}, 403),
                ({"Origin": f"https://{LOCALHOST}:{port}"}, 403),
                ({"Sec-Fetch-Site": "cross-site"}, 403),
                ({"Sec-Fetch-Site": "same-site"}, 403),
                ({"Content-Type": "text/plain"}, 415),
                ({"Content-Type": None}, 415),
                ({"Transfer-Encoding": "chunked"}, 411),
                ({"Content-Length": None}, 411),
                ({"Content-Length": ["5", "5"]}, 400),
                ({"Content-Length": "-1"}, 400),
            ):
                result = post(server, b"", unread | headers, path=path)
                assert _json(result)[0] == status, (path, headers)
            status, _, body = post(server, b"", {"Host": "evil.example:80"} | unread, path=path)
            assert status == 403 and body == b"forbidden: unexpected Host header\n"
            short = post(server, b'{"task"', {"Content-Length": "100"}, shut=True, path=path)
            assert _json(short) == (400, {"error": "request body is incomplete"})
        assert post(server, {}, path="/api/comments/resolve", method="PUT")[0] == 501
        assert post(server, {}, path="/api/comments/resolve/x")[0] == 501
        alike = {"Origin": f"http://localhost:{port}", "Sec-Fetch-Site": "same-origin"}
        body = {"task": task_id, "comment": ids["addressed"]}
        assert _json(post(server, body, alike, path="/api/comments/resolve"))[0] == 200
    assert len(_diff(dump, _db_dump(db_path))[0]) == 2


def test_a_locked_database_is_a_503_and_the_retry_is_saved_once(db_path, monkeypatch):
    task_id = _ready(db_path)
    ids = _comments(db_path, task_id)
    holder = sqlite3.connect(db_path, autocommit=True, check_same_thread=False)

    class LockedOut(CommentService):
        def resolve(self, comment_id):
            self.conn.execute("PRAGMA busy_timeout=100")
            holder.execute("BEGIN IMMEDIATE")
            try:
                return super().resolve(comment_id)
            finally:
                holder.execute("ROLLBACK")

    dump = _db_dump(db_path)
    with serve(db_path, task_id) as server:
        monkeypatch.setattr(comments_view, "CommentService", LockedOut)
        try:
            status, data = _move(server, task_id, ids["addressed"])
        finally:
            holder.close()
        assert _db_dump(db_path) == dump
        monkeypatch.undo()
        saved = _move(server, task_id, ids["addressed"])
        again = _move(server, task_id, ids["addressed"])
    assert (status, data["retry"]) == (503, True) and "locked" in data["error"]
    assert saved[0] == 200 and saved[1]["changed"] is True
    assert again[0] == 200 and again[1]["changed"] is False
    assert len(_diff(dump, _db_dump(db_path))[0]) == 2


def test_a_removed_elements_comment_resolves_and_reopens(db_path):
    """Q7c: no stale-version check; the element left the design in version 2."""
    task_id = start_task(db_path, "remove", "element")
    _state(db_path, task_id, "component A\ncomponent B")

    def redesigned(controller):
        service = CommentService(controller, task_id)
        service.diagrams.sync()
        comment, _ = service.add(1, "component:a", "redesign", "drop A")
        ws = controller.workspace(task_id)
        with transaction(controller.conn):
            for entry in ws.design_entries():
                ws.withdraw_design(entry.id)
            ws.state_design(DesignSection.COMPONENTS, "component B")
        service.diagrams.sync()
        service.address_all([comment.id])
        return comment.id

    comment = _with(db_path, redesigned)
    with serve(db_path, task_id) as server:
        assert _move(server, task_id, comment)[1]["comment"]["status"] == "resolved"
        assert _move(server, task_id, comment, "reopen")[1]["comment"]["status"] == "open"
        section = api(server)["task"]["comments"]
    assert section["items"][0]["element_label"] == "A" and section["addressed_in"] == {}


def test_the_read_model_has_addressed_in_and_the_stream_pushes_a_move(db_path):
    task_id = _ready(db_path)
    ids = _comments(db_path, task_id)
    with serve(db_path, task_id) as server:
        with stream(server) as response:
            assert "retry" in frame(response)
            first = json.loads(frame(response)["data"])["task"]["comments"]
            assert _move(server, task_id, ids["addressed"])[0] == 200
            pushed = json.loads(frame(response)["data"])["task"]["comments"]
    assert first["addressed_in"] == {str(ids["addressed"]): 1, str(ids["resolved"]): 1}
    assert set(first["items"][0]) == {"id", "version", "element", "kind", "text", "status",
                                      "created_at", "updated_at", "element_kind",
                                      "element_label"}  # fmt: skip
    statuses = {c["id"]: c["status"] for c in pushed["items"]}
    assert statuses[ids["addressed"]] == "resolved"
    assert pushed["counts"] == {"open": 1, "addressed": 0, "resolved": 2}


# T9: the whole loop — comment, list, fix the design with the CLI, address, compare, resolve


def _redesign(capsys, *argv: str) -> str:
    assert redesign.main(list(argv)) == 0
    return capsys.readouterr().out


def test_the_redesign_loop_end_to_end(db_path, capsys):
    task_id = start_task(db_path, "redesign", "loop")
    cli(db_path, "phase", task_id, "next")
    cli(db_path, "phase", task_id, "next")
    cli(db_path, "design", "add", task_id, "components",
        "component A\ncomponent B\nconnect A -> B: wrong way")  # fmt: skip
    wrong, right = "connection:a->b", "connection:b->a"
    db = ["--db", str(db_path)]
    with serve(db_path, task_id) as server:
        api(server)
        body = _payload(task_id, element=wrong, kind="redesign", text="should be B -> A")
        status, added = _json(post(server, body))
        assert status == 201
        comment = added["comment"]["id"]
        listed = _redesign(capsys, task_id, *db)
        assert f"#{comment} · redesign · connection A -> B ({wrong}) · version 1" in listed
        cli(db_path, "phase", task_id, "next")
        cli(db_path, "phase", task_id, "back", "design", "--reason", f"comment #{comment}")
        out = cli(db_path, "design", "propose", task_id, "components",
                  "component A\ncomponent B\nconnect B -> A: right way", "--rationale",
                  f"comment #{comment}")  # fmt: skip
        entry = re.match(r"E(\d+) pending", out).group(1)
        cli(db_path, "design", "decide", task_id, entry, "accept")
        cli(db_path, "design", "withdraw", task_id, "1")
        done = _redesign(capsys, "address", task_id, str(comment), *db)
        assert done == f"recorded diagram version 2\n#{comment} addressed in diagram version 2\n"
        task = api(server)["task"]
        assert task["diagrams"]["current"]["version"] == 2
        assert task["comments"]["addressed_in"] == {str(comment): 2}
        status, diff = _compare(server, "from=1&to=2")
        assert status == 200 and diff["marks"]["from"] == {wrong: "removed"}
        assert diff["marks"]["to"] == {right: "added"}
        assert [(c["change"], c["id"]) for c in diff["changes"]] == [
            ("removed", wrong), ("added", right)
        ]  # fmt: skip
        assert _move(server, task_id, comment)[1]["comment"]["status"] == "resolved"
        assert "no open comments" in _redesign(capsys, task_id, *db)
        assert _move(server, task_id, comment, "reopen")[1]["comment"]["status"] == "open"
    again = _redesign(capsys, task_id, *db)
    assert f"#{comment} · redesign · connection A -> B ({wrong}) · version 1 (not current)" in again


def test_every_script_is_served_in_order_without_duplicate_globals(db_path):
    with serve(db_path, None) as server:
        page = fetch(server, "/")[2]
        for name in ("comment_actions", "compare"):
            status, headers, script = fetch(server, f"/static/{name}.js")
            assert status == 200 and headers["Content-Type"] == "text/javascript; charset=utf-8"
    tags = [page.index(f'<script src="/static/{n}.js" defer></script>'.encode()) for n in SCRIPTS]
    assert tags == sorted(tags)
    seen: dict[str, str] = {}
    for name in SCRIPTS:
        source = (STATIC_DIR / f"{name}.js").read_text(encoding="utf-8")
        for declared in DECLARED.findall(source):
            assert declared not in seen, f"{declared} in {name}.js and {seen.get(declared)}"
            seen[declared] = f"{name}.js"
    actions = (STATIC_DIR / "comment_actions.js").read_text(encoding="utf-8")
    assert "/address" not in actions and "Address" not in actions
