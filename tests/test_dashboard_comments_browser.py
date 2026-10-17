"""P2-05: a real headless Chrome selects an element, writes a comment and sees every state.

The write tests serve a copy of the static files plus a test-only driver script (the CSP allows
no inline script), so the production files stay untouched. Skipped only when no Chrome/Chromium
is installed. Set HARNESS_DASHBOARD_SCREENSHOT_DIR to a directory to also save the comments view
(comments-1280w.png, comments-390w.png).
"""

import html
import json
import os
import re
import shutil
import sqlite3
from importlib.resources import as_file
from pathlib import Path
from urllib.parse import quote

import pytest

from harness.comments import CommentService
from harness.dashboard import server as server_module
from harness.dashboard.server import DashboardServer
from tests.test_dashboard import api, serve
from tests.test_dashboard_browser import CHROME, _dom, _headless
from tests.test_dashboard_diagrams import _sample, _state, _with
from tests.test_dashboard_diagrams_browser import _items

pytestmark = pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not installed")
RESULT = re.compile(r'<pre id="driver-result">(.*?)</pre>', re.DOTALL)
TEXT = "<b>wrong</b> arrow"

DRIVER_JS = r"""
const RESULT = {};
const WAIT_MS = 6000;

function finish(extra) {
  Object.assign(RESULT, extra);
  const pre = document.createElement("pre");
  pre.id = "driver-result";
  pre.textContent = JSON.stringify(RESULT);
  document.body.append(pre);
}

function waitFor(step, pred) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const poll = () => {
      const value = pred();
      if (value) resolve(value);
      else if (Date.now() - started > WAIT_MS) reject(new Error(`timed out: ${step}`));
      else setTimeout(poll, 50);
    };
    poll();
  });
}

const byId = (id) => document.getElementById(id);

async function fill() {
  const selector = `#diagrams [data-element-id="${CSS.escape(DRIVE.element)}"]`;
  const group = await waitFor("load", () => document.querySelector(selector));
  group.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  const kind = await waitFor("form", () => byId("comment-kind"));
  kind.value = DRIVE.kind;
  kind.dispatchEvent(new Event("change", { bubbles: true }));
  const text = byId("comment-text");
  text.value = DRIVE.text;
  text.dispatchEvent(new Event("input", { bubbles: true }));
  text.focus();
  return text;
}

async function drive() {
  const text = await fill();
  if (DRIVE.expect === "draft") {
    text.setSelectionRange(3, 5);
    const before = byId("main").firstElementChild;
    await waitFor("re-render", () => byId("main").firstElementChild !== before);
    const now = byId("comment-text");
    finish({ active: document.activeElement.id, text: now.value, kind: byId("comment-kind").value,
      start: now.selectionStart, end: now.selectionEnd, replaced: now !== text });
    return;
  }
  byId("comment-form").requestSubmit();
  if (DRIVE.expect === "added") {
    const item = await waitFor("added", () => [...document.querySelectorAll(
      '#comments li[data-status="open"]')].find((li) => li.textContent.includes(DRIVE.text)));
    finish({ added: item.textContent, cleared: byId("comment-text").value,
      panel: byId("diagram-selection").textContent });
    return;
  }
  const alert = await waitFor("refused", () => byId("diagrams").dataset.version
    === String(DRIVE.settle) && document.querySelector("#diagram-selection [role=alert]"));
  finish({ alert: alert.textContent, reload: !!byId("comment-reload"),
    text: byId("comment-text").value, kind: byId("comment-kind").value });
}

document.addEventListener("DOMContentLoaded", () => {
  drive().catch((error) => finish({ error: error.message }));
});
"""


@pytest.fixture(autouse=True)
def short_streams(monkeypatch):
    monkeypatch.setattr(DashboardServer, "stream_lifetime", 0.5)


def _driven(tmp_path: Path, monkeypatch, plan: dict) -> None:
    """Serve a copy of the static files whose page also loads driver.js (with the plan)."""
    copied = tmp_path / "static"
    with as_file(server_module.STATIC_DIR) as real:
        shutil.copytree(real, copied)
    (copied / "driver.js").write_text(
        f'"use strict";\nconst DRIVE = {json.dumps(plan)};\n{DRIVER_JS}', encoding="utf-8"
    )
    page = copied / "index.html"
    tag = '<script src="/static/app.js" defer></script>'
    source = page.read_text(encoding="utf-8")
    assert source.count(tag) == 1
    page.write_text(
        source.replace(tag, f'{tag}\n    <script src="/static/driver.js" defer></script>'),
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "STATIC_DIR", copied)
    driver = {"/static/driver.js": ("driver.js", "text/javascript; charset=utf-8")}
    monkeypatch.setattr(server_module, "STATIC", server_module.STATIC | driver)


def _result(dom: str) -> dict:
    found = RESULT.search(dom)
    assert found, dom[-2000:]
    data = json.loads(html.unescape(found.group(1)))
    assert "error" not in data, data
    return data


def _connection(server: DashboardServer) -> str:
    return next(item["id"] for item in _items(server) if item["kind"] == "connection")


def _rows(db_path: Path) -> tuple[list[tuple], list[tuple]]:
    conn = sqlite3.connect(db_path)
    try:
        comments = conn.execute(
            "SELECT diagram_version, element_id, kind, text, status FROM comments ORDER BY id"
        ).fetchall()
        events = conn.execute(
            "SELECT kind, payload FROM events WHERE kind LIKE 'comment.%' ORDER BY id"
        ).fetchall()
        return comments, events
    finally:
        conn.close()


def test_headless_chrome_adds_a_comment_on_a_connection(db_path, tmp_path, monkeypatch):
    task_id = _sample(db_path)
    with serve(db_path, task_id) as server:
        element = _connection(server)
        _driven(tmp_path, monkeypatch, {"element": element, "kind": "correction", "text": TEXT,
                                        "expect": "added"})  # fmt: skip
        dom = _dom(server, tmp_path / "profile")
    result = _result(dom)
    assert TEXT in result["added"] and "open · correction" in result["added"]
    assert f"({element}) · version 1" in result["added"] and result["cleared"] == ""
    assert "Comments on this element in version 1" in result["panel"]
    assert "&lt;b&gt;wrong&lt;/b&gt; arrow" in dom and "<b>wrong</b>" not in dom
    comments, events = _rows(db_path)
    assert comments == [(1, element, "correction", TEXT, "open")]
    assert [kind for kind, _ in events] == ["comment.added"]
    assert json.loads(events[0][1]) == {"id": 1, "version": 1, "element": element,
                                        "kind": "correction"}  # fmt: skip


def test_headless_chrome_keeps_the_draft_when_the_version_is_outdated(
    db_path, tmp_path, monkeypatch
):
    task_id = _sample(db_path)
    original, calls = server_module.add_comment, []

    def redesign_first(path, body):
        if not calls:
            calls.append(1)
            _state(db_path, task_id, "component Exporter")
        return original(path, body)

    with serve(db_path, task_id) as server:
        element = _connection(server)
        _driven(tmp_path, monkeypatch, {"element": element, "kind": "redesign", "text": TEXT,
                                        "expect": "refused", "settle": 2})  # fmt: skip
        monkeypatch.setattr(server_module, "add_comment", redesign_first)
        dom = _dom(server, tmp_path / "profile")
    result = _result(dom)
    assert calls == [1] and "reload" in result["alert"] and result["reload"] is True
    assert (result["text"], result["kind"]) == (TEXT, "redesign")
    assert _rows(db_path)[0] == []


def test_headless_chrome_keeps_draft_and_focus_across_a_live_re_render(
    db_path, tmp_path, monkeypatch
):
    task_id = _sample(db_path)
    with serve(db_path, task_id) as server:
        element = _connection(server)
        _driven(tmp_path, monkeypatch, {"element": element, "kind": "clarification",
                                        "text": "half typed", "expect": "draft"})  # fmt: skip
        dom = _dom(server, tmp_path / "profile")
    result = _result(dom)
    assert result["replaced"] is True and result["active"] == "comment-text"
    assert (result["text"], result["kind"]) == ("half typed", "clarification")
    assert (result["start"], result["end"]) == (3, 5)
    assert _rows(db_path)[0] == []


def _comment_states(db_path: Path, task_id: str) -> None:
    """Version 1 gets an open, an addressed and a resolved comment."""

    def step(controller):
        service = CommentService(controller, task_id)
        edge = "connection:browser->local-server"
        service.add(1, edge, "correction", "<img src=x onerror=alert(1)> wrong arrow")
        addressed, _ = service.add(1, edge, "clarification", "which protocol?")
        service.address(addressed.id)
        resolved, _ = service.add(1, "component:web-dashboard", "redesign", "split\nthis")
        service.address(resolved.id)
        service.resolve(resolved.id)

    _with(db_path, step)


def _shots(server: DashboardServer, profile: Path, fragment: str) -> None:
    directory = os.environ.get("HARNESS_DASHBOARD_SCREENSHOT_DIR")
    if not directory:
        return
    Path(directory).mkdir(parents=True, exist_ok=True)
    for width, height in ((1280, 7000), (390, 14000)):
        shot = Path(directory) / f"comments-{width}w.png"
        result = _headless(
            profile / f"{width}", f"--window-size={width},{height}", f"--screenshot={shot}",
            f"{server.url}{fragment}",
        )  # fmt: skip
        assert result.returncode == 0 and shot.is_file(), result.stderr


def test_headless_chrome_shows_every_comment_state_and_old_version_comments(db_path, tmp_path):
    task_id = _sample(db_path)
    edge = quote("connection:browser->local-server", safe="")
    with serve(db_path, task_id) as server:
        api(server)
        _comment_states(db_path, task_id)
        current = _dom(server, tmp_path / "profile", f"#element={edge}")
        _shots(server, tmp_path / "profile-shots", f"#element={edge}")
        _state(db_path, task_id, "component Exporter")
        assert api(server)["task"]["diagrams"]["current"]["version"] == 2
        later = _dom(server, tmp_path / "profile-b", "#element=component%3Aexporter")
        old = _dom(server, tmp_path / "profile-c", f"#version=1&element={edge}")
    assert "<h3>Comments (open 1 · addressed 1 · resolved 1)</h3>" in current
    for status in ("open", "addressed", "resolved"):
        assert f'<li data-status="{status}"><p>#' in current
    assert "#1 · open · correction · connection Browser -&gt; Local Server" in current
    assert "&lt;img src=x onerror=alert(1)&gt; wrong arrow" in current and "<img" not in current
    assert '<p class="comment-text">split\nthis</p>' in current
    assert f'<a href="#element={edge}">show in diagram</a>' in current
    assert "<h5>Comments on this element in version 1</h5>" in current
    assert '<form id="comment-form">' in current
    for kind in ("clarification", "correction", "redesign"):
        assert f'<option value="{kind}">{kind}</option>' in current
    assert '<textarea id="comment-text" rows="4"></textarea>' in current
    assert "maxlength" not in current
    assert '<p class="diagram-status">showing: version 1 (current)</p>' in current

    assert "· version 1 (not current)" in later
    assert f'<a href="#version=1&amp;element={edge}">show in diagram</a>' in later
    assert "<p>no comments on this element yet</p>" in later and 'id="comment-form"' in later

    assert "showing: version 1 (not current)" in old and 'id="comment-form"' not in old
    assert "comments can be added only on the current version" in old
    assert '<li data-status="open"><p>#1 · open · correction</p>' in old
