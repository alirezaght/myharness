"""P2-04: diagrams in the dashboard read model, /api/diagrams and the writes they may cause."""

import json
import re
import sqlite3
from pathlib import Path

import pytest

from harness.controller import HarnessController
from harness.dashboard import view as view_module
from harness.dashboard.diagrams_view import NO_DIAGRAMS, NO_NOTATION_HINT
from harness.dashboard.server import SECURITY_HEADERS, STATIC_DIR, DashboardServer
from harness.dashboard.view import NO_ACTIVE_TASK, dashboard_view
from harness.db import connect, transaction
from harness.design_models import DesignSection
from harness.diagrams import DiagramService
from tests.sample_design import TYPES, add_sample_design
from tests.test_dashboard import _db_dump, api, fetch, serve, start_task
from tests.test_dashboard_stream import frame, stream

UNSAFE = re.compile(
    r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|setAttribute\(\"style\""
)


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(DashboardServer, "poll_seconds", 0.05)


def _with(db_path: Path, step):
    conn = connect(db_path)
    try:
        return step(HarnessController(conn))
    finally:
        conn.close()


def _sample(db_path: Path) -> str:
    task_id = start_task(db_path, "visual", "design")
    _with(db_path, lambda c: add_sample_design(c, task_id))
    return task_id


def _state(db_path: Path, task_id: str, text: str) -> None:
    def step(controller: HarnessController) -> None:
        with transaction(controller.conn):
            controller.workspace(task_id).state_design(DesignSection.COMPONENTS, text)

    _with(db_path, step)


def _diagrams(server: DashboardServer, query: str) -> tuple[int, dict]:
    status, _, body = fetch(server, f"/api/diagrams?{query}")
    return status, json.loads(body)


def _element_ids(diagrams: list[dict]) -> set[str]:
    return {item["id"] for scene in diagrams for item in scene["items"]}


def test_the_sample_design_renders_every_relevant_type_with_addressable_elements(db_path):
    task_id = _sample(db_path)
    with serve(db_path, task_id) as server:
        section = api(server)["task"]["diagrams"]
        again = api(server)["task"]["diagrams"]
    assert again == section
    current = section["current"]
    assert (current["version"], current["current"], current["types"]) == (1, True, TYPES)
    assert [scene["type"] for scene in current["diagrams"]] == TYPES
    assert (section["notice"], section["hint"], section["issues"]) == (None, None, [])
    assert (section["outdated"], section["unavailable"]) == (False, None)
    assert [h["version"] for h in section["history"]] == [1]
    assert section["history"][0]["content_hash"] == current["content_hash"]
    ids = _element_ids(current["diagrams"])
    for expected in (
        "component:web-dashboard",
        "connection:browser->local-server",
        "message:browser->local-server:1",
        "flow:design-entries->diagram-service",
        "machine:comment",
        "state:comment/resolved",
        "transition:comment/resolved->open",
        "node:localhost",
        "deployment:sqlite-state@localhost",
    ):
        assert expected in ids


def test_a_redesign_is_pushed_live_and_the_old_version_stays_viewable(db_path):
    task_id = _sample(db_path)
    with serve(db_path, task_id) as server:
        with stream(server) as response:
            assert "retry" in frame(response)
            first = json.loads(frame(response)["data"])["task"]["diagrams"]
            _state(db_path, task_id, "component Exporter: writes files")
            pushed = json.loads(frame(response)["data"])["task"]["diagrams"]
        status, old = _diagrams(server, "version=1")
        new_status, new = _diagrams(server, f"task={task_id}&version=2")
    assert first["current"]["version"] == 1
    assert pushed["current"]["version"] == 2 and pushed["outdated"] is False
    assert [h["version"] for h in pushed["history"]] == [1, 2]
    assert [h["current"] for h in pushed["history"]] == [False, True]
    assert "component:exporter" in _element_ids(pushed["current"]["diagrams"])
    assert status == 200 and (old["version"], old["current"]) == (1, False)
    assert old["diagrams"] == first["current"]["diagrams"]
    assert "component:exporter" not in _element_ids(old["diagrams"])
    assert new_status == 200 and new["current"] is True
    assert new["diagrams"] == pushed["current"]["diagrams"]


@pytest.mark.parametrize("raw", ["", "0", "01", "-1", "1.0", "%D9%A1", "1%0A", "+1", "1e3"])
def test_a_bad_version_is_a_400(db_path, raw):
    task_id = _sample(db_path)
    with serve(db_path, task_id) as server:
        assert _diagrams(server, f"version={raw}") == (
            400,
            {"error": "version must be a positive integer"},
        )
        assert _diagrams(server, "")[0] == 400


def test_unknown_versions_tasks_and_hosts_are_refused(db_path, tmp_path):
    task_id = _sample(db_path)
    with serve(db_path, task_id) as server:
        api(server)
        status, body = _diagrams(server, "version=2")
        assert status == 404 and body == {"error": f"unknown diagram version 2 for task {task_id}"}
        assert _diagrams(server, f"version={2**64}")[0] == 404
        status, body = _diagrams(server, "task=t-00000000&version=1")
        assert (status, body) == (404, {"error": "unknown task: t-00000000"})
        status, headers, raw = fetch(server, "/api/diagrams?version=1", host="evil.example:80")
        assert status == 403 and task_id.encode() not in raw
        status, headers, _ = fetch(server, "/api/diagrams?version=1")
        assert status == 200 and headers["Content-Type"].startswith("application/json")
        for name, value in SECURITY_HEADERS.items():
            assert headers[name] == value
        assert fetch(server, "/api/diagrams?version=1", method="POST")[0] == 501
    with serve(db_path, None) as server:
        assert _diagrams(server, "version=1") == (404, {"error": NO_ACTIVE_TASK})


def test_the_diagram_script_is_served_and_uses_no_unsafe_dom_api(db_path):
    with serve(db_path, None) as server:
        status, headers, script = fetch(server, "/static/diagrams.js")
        page = fetch(server, "/")[2]
    assert status == 200 and headers["Content-Type"] == "text/javascript; charset=utf-8"
    assert b"/api/diagrams" in script and b"createElementNS" in script
    assert page.index(b'<script src="/static/diagrams.js" defer></script>') < page.index(
        b'<script src="/static/app.js" defer></script>'
    )
    for name in sorted(p.name for p in STATIC_DIR.iterdir() if p.name.endswith(".js")):
        assert not UNSAFE.search((STATIC_DIR / name).read_text(encoding="utf-8")), name


def _routes(task_id: str) -> list[str]:
    paths = ["/", "/static/diagrams.js", "/api/dashboard", "/api/stream", "/api/diagrams"]
    return [*paths, f"/api/dashboard?task={task_id}", f"/api/diagrams?task={task_id}&version=1"]


def test_a_task_without_notation_writes_nothing(db_path):
    task_id = start_task(db_path, "csv", "export")
    _state(db_path, task_id, "One module writes the rows")
    dump = _db_dump(db_path)
    with serve(db_path, task_id) as server:
        server.stream_lifetime = 0.3
        for path in _routes(task_id):
            fetch(server, path)
        section = api(server)["task"]["diagrams"]
    assert _db_dump(db_path) == dump
    assert (section["notice"], section["hint"]) == (NO_DIAGRAMS, NO_NOTATION_HINT)
    assert (section["current"], section["history"], section["outdated"]) == (None, [], False)


def test_the_sample_task_adds_only_diagram_version_rows(db_path, tmp_path):
    task_id = _sample(db_path)
    dump = _db_dump(db_path)
    with serve(db_path, task_id) as server:
        server.stream_lifetime = 0.3
        for path in _routes(task_id):
            fetch(server, path)
    after = _db_dump(db_path)
    assert set(dump) <= set(after)
    added = sorted(set(after) - set(dump))
    assert added and all("diagram_versions" in line for line in added), added
    assert sum(line.startswith('INSERT INTO "diagram_versions"') for line in added) == 1

    missing = tmp_path / "absent" / "state.db"
    with serve(missing, task_id) as server:
        status, body = _diagrams(server, "version=1")
    assert (status, body) == (404, {"error": f"unknown task: {task_id}"})
    assert not missing.exists() and not missing.parent.exists()


def test_a_held_write_lock_marks_diagrams_unavailable_and_the_rest_renders(db_path, monkeypatch):
    """Another connection takes the write lock after the view connected, right before sync."""
    task_id = _sample(db_path)
    holder = sqlite3.connect(db_path, autocommit=True)

    class LockedOut(DiagramService):
        def sync(self):
            self.conn.execute("PRAGMA busy_timeout=100")
            holder.execute("BEGIN IMMEDIATE")
            try:
                return super().sync()
            finally:
                holder.execute("ROLLBACK")

    monkeypatch.setattr(view_module, "DiagramService", LockedOut)
    try:
        data = dashboard_view(db_path, task_id, None)
    finally:
        holder.close()
    monkeypatch.undo()
    section = data["task"]["diagrams"]
    assert section["unavailable"].startswith("diagram version could not be recorded: ")
    assert "locked" in section["unavailable"]
    assert (section["outdated"], section["current"], section["notice"]) == (True, None, NO_DIAGRAMS)
    assert section["hint"] is None
    assert data["task"]["id"] == task_id and data["task"]["spec"] and data["tasks"]
    recovered = dashboard_view(db_path, task_id, None)["task"]["diagrams"]
    assert recovered["current"]["version"] == 1 and recovered["unavailable"] is None


def test_no_design_and_malformed_notation(db_path):
    task_id = start_task(db_path, "csv", "export")
    empty = dashboard_view(db_path, task_id, None)["task"]["diagrams"]
    assert (empty["notice"], empty["hint"], empty["issues"]) == (NO_DIAGRAMS, None, [])
    _state(db_path, task_id, "prose\nconnect A -> B -> C")
    broken = dashboard_view(db_path, task_id, None)["task"]["diagrams"]
    assert broken["issues"] == [
        {
            "ref": "E1",
            "line": 2,
            "text": "connect A -> B -> C",
            "problem": "connect: expected connect <from> -> <to>[: <label>]",
        }
    ]
    assert (broken["notice"], broken["hint"], broken["current"]) == (
        NO_DIAGRAMS,
        NO_NOTATION_HINT,
        None,
    )
    assert dashboard_view(db_path, None, None)["task"] is None
    assert dashboard_view(db_path, "t-00000000", None)["notice"] == "unknown task: t-00000000"
