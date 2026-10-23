"""P2-06: a real headless Chrome compares two diagram versions and resolves/re-opens comments.

Like the P2-05 browser tests, the driven runs serve a copy of the static files plus a test-only
driver script (the CSP allows no inline script). Skipped only when no Chrome/Chromium is
installed. Set HARNESS_DASHBOARD_SCREENSHOT_DIR to a directory to also save the comparison
(compare-1280w.png, compare-390w.png) and the comment actions (comment-actions-1280w.png).
"""

import json
import os
import shutil
import sqlite3
from importlib.resources import as_file
from pathlib import Path

import pytest

from harness.comments import CommentService
from harness.dashboard import server as server_module
from harness.dashboard.server import DashboardServer
from harness.db import transaction
from harness.design_models import DesignSection
from tests.sample_design import INTERACTIONS
from tests.test_dashboard import api, serve
from tests.test_dashboard_browser import CHROME, _headless
from tests.test_dashboard_comments_browser import _result
from tests.test_dashboard_diagrams import _sample, _with

pytestmark = [pytest.mark.browser, pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not installed")]
GONE = "connection:local-server->sqlite-state"
RELABELLED = "connection:local-server->browser"
NEW = "component:exporter"

DRIVER_JS = r"""
const RESULT = {};
const WAIT_MS = 8000;

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

function box(node) {
  const r = node.getBoundingClientRect();
  return { top: r.top, left: r.left, bottom: r.bottom, right: r.right };
}

function mark(side, id) {
  const node = document.querySelector(
    `.compare-pane[data-side="${side}"] [data-compare-element="${CSS.escape(id)}"]`);
  return node ? node.getAttribute("data-change") : "missing";
}

function loaded(from, to) {
  const section = byId("diagram-compare");
  return section && section.dataset.from === String(from) && section.dataset.to === String(to)
    && document.querySelectorAll(".compare-pane").length === 2 && section;
}

function statusOf(id) {
  const item = [...document.querySelectorAll("#comments li")]
    .find((li) => li.firstElementChild.textContent.startsWith(`#${id} · `));
  return item ? item.dataset.status : null;
}

async function layout() {
  const section = await waitFor("compare", () => loaded(DRIVE.from, DRIVE.to));
  const panes = [...document.querySelectorAll(".compare-pane")];
  const ids = [...document.querySelectorAll("[id]")].map((node) => node.id);
  finish({
    panes: panes.map(box),
    titles: panes.map((pane) => pane.querySelector("h4").textContent),
    marks: DRIVE.check.map(([side, id]) => mark(side, id)),
    changes: [...section.querySelectorAll(".compare-changes li")].map((li) => li.textContent),
    legend: section.querySelector(".compare-legend").textContent,
    duplicates: ids.filter((id, index) => ids.indexOf(id) !== index),
    counts: panes.map((pane) => pane.querySelectorAll("[data-compare-element]").length),
    plain: panes.map((pane) => pane.querySelectorAll("[data-element-id]").length),
    overflow: document.documentElement.scrollWidth > window.innerWidth,
  });
}

async function actions() {
  const id = DRIVE.comment;
  const resolve = await waitFor("resolve", () => byId(`comment-${id}-resolve`));
  resolve.focus();
  resolve.click();
  const busy = byId(`comment-${id}-resolve`);
  const inflight = { disabled: !!busy && busy.disabled,
    saving: byId(`comment-${id}-actions`).textContent.includes("saving…") };
  await waitFor("resolved", () => statusOf(id) === "resolved" && byId(`comment-${id}-reopen`));
  const afterResolve = { reopen: !!byId(`comment-${id}-reopen`),
    resolve: !!byId(`comment-${id}-resolve`), active: document.activeElement.id };
  byId(`comment-${id}-reopen`).click();
  await waitFor("reopened", () => statusOf(id) === "open");
  finish({ inflight, afterResolve, open: byId(`comment-${id}-actions`).textContent,
    buttons: document.querySelectorAll(`#comment-${id}-actions button`).length });
}

async function compareButton() {
  await waitFor("default pair", () => loaded(DRIVE.from, DRIVE.to));
  const button = await waitFor("button", () => byId(`comment-${DRIVE.comment}-compare`));
  const label = button.textContent;
  button.click();
  await waitFor("picked pair", () => loaded(DRIVE.pick[0], DRIVE.pick[1])
    && document.querySelector(".compare-changes, #diagram-compare .compare-grid"));
  const section = byId("diagram-compare");
  finish({ label, from: byId("compare-from").value, to: byId("compare-to").value,
    titles: [...section.querySelectorAll(".compare-pane h4")].map((h) => h.textContent) });
}

const MODES = { layout, actions, compareButton };

document.addEventListener("DOMContentLoaded", () => {
  MODES[DRIVE.mode]().catch((error) => finish({ error: error.message }));
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


def _redesign(controller, task_id: str, interactions: str, extra: str) -> None:
    """Replace the interactions entry and add a component entry in one transaction."""
    ws = controller.workspace(task_id)
    with transaction(controller.conn):
        for entry in ws.design_entries():
            if entry.effective and entry.text == INTERACTIONS:
                ws.withdraw_design(entry.id)
        ws.state_design(DesignSection.INTERACTIONS, interactions)
        ws.state_design(DesignSection.COMPONENTS, extra)


REVISED = INTERACTIONS.replace("connect Local Server -> SQLite State: in-process\n", "").replace(
    "connect Local Server -> Browser: live updates", "connect Local Server -> Browser: SSE"
)


def _loop(db_path: Path, third: bool = False) -> tuple[str, dict[str, int]]:
    """Sample v1 with comments; v2 drops one edge, relabels one, adds Exporter (v3 optional)."""
    task_id = _sample(db_path)

    def step(controller):
        service = CommentService(controller, task_id)
        service.diagrams.sync()
        ids = {
            "gone": service.add(1, GONE, "redesign", "<b>no</b> direct DB access")[0].id,
            "open": service.add(1, "component:web-dashboard", "clarification", "why?")[0].id,
        }
        _redesign(controller, task_id, REVISED, "component Exporter: writes rows")
        service.diagrams.sync()
        service.address_all([ids["gone"]])
        if third:
            with transaction(controller.conn):
                ws = controller.workspace(task_id)
                ws.state_design(DesignSection.COMPONENTS, "connect Exporter -> SQLite State")
            service.diagrams.sync()
        return ids

    return task_id, _with(db_path, step)


def _driven_dom(server: DashboardServer, profile: Path, width: int, height: int) -> str:
    result = _headless(profile, f"--window-size={width},{height}", "--dump-dom", server.url)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _shot(server: DashboardServer, profile: Path, name: str, width: int, height: int) -> None:
    directory = os.environ.get("HARNESS_DASHBOARD_SCREENSHOT_DIR")
    if not directory:
        return
    Path(directory).mkdir(parents=True, exist_ok=True)
    shot = Path(directory) / name
    result = _headless(profile, f"--window-size={width},{height}", f"--screenshot={shot}",
                       server.url)  # fmt: skip
    assert result.returncode == 0 and shot.is_file(), result.stderr


def _events(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT kind FROM events WHERE kind LIKE 'comment.%' ORDER BY id")
        return [row[0] for row in rows]
    finally:
        conn.close()


def _status(db_path: Path, comment: int) -> str:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT status FROM comments WHERE id = ?", (comment,)).fetchone()[0]
    finally:
        conn.close()


CHECKS = [["from", GONE], ["to", GONE], ["from", RELABELLED], ["to", RELABELLED], ["to", NEW],
          ["from", NEW], ["to", "component:web-dashboard"]]  # fmt: skip
EXPECTED = ["removed", "missing", "changed", "changed", "added", "missing", None]


def test_headless_chrome_shows_the_versions_side_by_side_and_stacked(
    db_path, tmp_path, monkeypatch
):
    task_id, _ = _loop(db_path)
    with serve(db_path, task_id) as server:
        to = api(server)["task"]["diagrams"]["current"]
        items = sum(len(scene["items"]) for scene in to["diagrams"])
        _shot(server, tmp_path / "shot-wide", "compare-1280w.png", 1280, 9000)
        _shot(server, tmp_path / "shot-narrow", "compare-390w.png", 390, 18000)
        plan = {"mode": "layout", "from": 1, "to": 2, "check": CHECKS}
        _driven(tmp_path, monkeypatch, plan)
        wide = _result(_driven_dom(server, tmp_path / "wide", 1280, 800))
        narrow = _result(_driven_dom(server, tmp_path / "narrow", 390, 844))
    a, b = wide["panes"]
    assert a["top"] == b["top"] and b["left"] > a["right"] - 1
    a, b = narrow["panes"]
    assert b["top"] >= a["bottom"] and a["left"] == b["left"]
    for result in (wide, narrow):
        assert result["marks"] == EXPECTED
        assert result["titles"] == ["version 1 (previous)", "version 2 (current)"]
        assert f"removed: connection Local Server -> SQLite State ({GONE})" in result["changes"]
        assert f"added: component Exporter ({NEW})" in result["changes"]
        assert f"changed: connection Local Server -> Browser ({RELABELLED})" in result["changes"]
        assert "added: solid outline" in result["legend"]
        assert "removed: dashed outline" in result["legend"]
        assert result["duplicates"] == [] and result["plain"] == [0, 0]
        assert result["counts"][1] == items and result["overflow"] is False
