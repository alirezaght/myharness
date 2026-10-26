"""P2-08: a real headless Chrome runs the browser part of the wrong-connection scenario.

Run 1: the developer comments on the wrong connection and sees Approve & Implement disabled.
Between the runs the harness fixes the design through the redesign loop (scripted verdicts).
Run 2: the developer sees the revision highlighted, resolves the comment and only then approves.
Like the P2-05..P2-07 browser tests, the page loads a test-only driver script from a copy of the
static files. Skipped only when no Chrome/Chromium is installed. Set
HARNESS_DASHBOARD_SCREENSHOT_DIR to also save acceptance-comment-1280w.png and
acceptance-resolve-1280w.png.
"""

import json
import shutil
import sqlite3
from importlib.resources import as_file
from pathlib import Path

import pytest

from harness.dashboard import server as server_module
from harness.dashboard.server import DashboardServer
from tests.test_dashboard import api, needs_git, serve
from tests.test_dashboard_browser import CHROME
from tests.test_dashboard_comments_browser import _result
from tests.test_dashboard_redesign_browser import _driven_dom, _events, _shot, _status
from tests.test_phase2_acceptance import RIGHT, WRONG, harness_fixes, wrong_connection_task

pytestmark = [pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not installed"), needs_git]
TEXT = "fixture: the arrow points the wrong way"

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
const codes = () => [...document.querySelectorAll("#approve-blockers li[data-code]")]
  .map((li) => li.dataset.code);

function button(disabled) {
  const node = byId("approve-button");
  return node && node.disabled === disabled && node;
}

function mark(side, id) {
  const node = document.querySelector(
    `.compare-pane[data-side="${side}"] [data-compare-element="${CSS.escape(id)}"]`);
  return node ? node.getAttribute("data-change") : "missing";
}

function item(status) {
  return [...document.querySelectorAll(`#comments li[data-status="${status}"]`)]
    .find((li) => li.textContent.includes(DRIVE.text));
}

async function comment() {
  await waitFor("ready before", () => button(false) && codes().length === 0);
  const readyBefore = { disabled: byId("approve-button").disabled, codes: codes() };
  const selector = `#diagrams [data-element-id="${CSS.escape(DRIVE.wrong)}"]`;
  const group = await waitFor("connection", () => document.querySelector(selector));
  group.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  const kind = await waitFor("form", () => byId("comment-kind"));
  kind.value = "correction";
  kind.dispatchEvent(new Event("change", { bubbles: true }));
  const text = byId("comment-text");
  text.value = DRIVE.text;
  text.dispatchEvent(new Event("input", { bubbles: true }));
  byId("comment-form").requestSubmit();
  const added = await waitFor("added", () => item("open"));
  await waitFor("blocked", () => button(true) && codes().join() === "comments");
  finish({ readyBefore, added: added.textContent, codes: codes(),
    disabled: byId("approve-button").disabled,
    overflow: document.documentElement.scrollWidth > window.innerWidth });
}

async function resolve() {
  await waitFor("compare", () => {
    const section = byId("diagram-compare");
    return section && section.dataset.from === "1" && section.dataset.to === "2"
      && document.querySelectorAll(".compare-pane").length === 2 && section;
  });
  const marks = [mark("from", DRIVE.wrong), mark("to", DRIVE.right), mark("to", DRIVE.wrong)];
  await waitFor("blocked", () => button(true) && codes().join() === "comments"
    && item("addressed"));
  const blocked = { disabled: byId("approve-button").disabled, codes: codes(),
    status: item("addressed").dataset.status };
  byId(`comment-${DRIVE.comment}-resolve`).click();
  const ready = await waitFor("enabled", () => button(false));
  const readyCodes = codes();
  ready.click();
  await waitFor("approved", () => {
    const output = byId("approve-output");
    return output && output.textContent.includes("approval #1 recorded:")
      && codes().includes("approved");
  });
  finish({ marks, blocked, readyCodes, codes: codes(),
    output: [...document.querySelectorAll("#approve-output li")].map((li) => li.textContent),
    disabled: byId("approve-button").disabled,
    overflow: document.documentElement.scrollWidth > window.innerWidth });
}

const MODES = { comment, resolve };

document.addEventListener("DOMContentLoaded", () => {
  MODES[DRIVE.mode]().catch((error) => finish({ error: error.message }));
});
"""


@pytest.fixture(autouse=True)
def short_streams(monkeypatch):
    monkeypatch.setattr(DashboardServer, "stream_lifetime", 0.5)


def _driven(tmp_path: Path, monkeypatch, plan: dict) -> None:
    """Serve a copy of the static files whose page also loads driver.js (with the plan)."""
    copied = tmp_path / f"static-{plan['mode']}"
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


def _count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_headless_chrome_runs_the_wrong_connection_scenario(
    db_path, sample_repo, tmp_path, monkeypatch
):
    task_id = wrong_connection_task(db_path, sample_repo)
    specs = tmp_path / "specs"
    plan = {"wrong": WRONG, "right": RIGHT, "text": TEXT}
    with serve(db_path, task_id, str(specs)) as server:
        with monkeypatch.context() as patch:
            _driven(tmp_path, patch, plan | {"mode": "comment"})
            first = _result(_driven_dom(server, tmp_path / "profile-comment", 1280, 800))
        _shot(server, tmp_path / "shot-comment", "acceptance-comment-1280w.png", 1280, 2400)
        [item] = api(server)["task"]["comments"]["items"]
        assert (item["element"], item["kind"], item["text"]) == (WRONG, "correction", TEXT)
        harness_fixes(db_path, task_id, fixed=[item["id"]], answered=[])
        _shot(server, tmp_path / "shot-resolve", "acceptance-resolve-1280w.png", 1280, 2400)
        _driven(tmp_path, monkeypatch, plan | {"mode": "resolve", "comment": item["id"]})
        second = _result(_driven_dom(server, tmp_path / "profile-resolve", 1280, 800))
    assert first["readyBefore"] == {"disabled": False, "codes": []}
    assert TEXT in first["added"] and first["codes"] == ["comments"]
    assert first["disabled"] is True and first["overflow"] is False
    assert second["marks"] == ["removed", "added", "missing"]
    assert second["blocked"] == {"disabled": True, "codes": ["comments"], "status": "addressed"}
    assert second["readyCodes"] == []
    assert second["output"][-1].startswith("approval #1 recorded: spec v1 sha256:")
    assert second["codes"] == ["approved"] and second["disabled"] is True
    assert second["overflow"] is False
    assert _status(db_path, item["id"]) == "resolved"
    assert _events(db_path) == ["comment.added", "comment.addressed", "comment.resolved"]
    assert _count(db_path, "approvals") == 1
    assert sorted(p.name for p in (specs / task_id).iterdir()) == ["v1.md"]
