"""P2-07: a real headless Chrome drives the Approval Center's Approve & Implement button.

Like the P2-05/P2-06 browser tests, the driven runs serve a copy of the static files plus a
test-only driver script (the CSP allows no inline script). Skipped only when no Chrome/Chromium
is installed. Set HARNESS_DASHBOARD_SCREENSHOT_DIR to also save approval-center-1280w.png and
approval-center-390w.png.
"""

import json
import shutil
import sqlite3
from importlib.resources import as_file
from pathlib import Path

import pytest

from harness.comments import CommentService
from harness.dashboard import server as server_module
from harness.dashboard.server import DashboardServer
from harness.diagrams import DiagramService
from tests.test_dashboard import needs_git, serve
from tests.test_dashboard_approval import approval_task
from tests.test_dashboard_browser import CHROME
from tests.test_dashboard_comments import EDGE
from tests.test_dashboard_comments_browser import _result
from tests.test_dashboard_diagrams import _state, _with
from tests.test_dashboard_redesign_browser import _driven_dom, _shot

pytestmark = [pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not installed"), needs_git]

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
const codes = () => [...document.querySelectorAll("#approve-blockers li")]
  .map((li) => li.dataset.code);

function enabled() {
  const button = byId("approve-button");
  return button && !button.disabled && button;
}

async function approve() {
  const blocked = await waitFor("blocked", () => {
    const button = byId("approve-button");
    return button && button.disabled && codes().includes("comments") && button;
  });
  const before = { disabled: blocked.disabled, codes: codes(),
    described: blocked.getAttribute("aria-describedby"),
    groups: [...document.querySelectorAll("#approvals section h4")].map((h) => h.textContent) };
  byId(`comment-${DRIVE.comment}-resolve`).click();
  const button = await waitFor("enabled", enabled);
  const ready = byId("approve-blockers").textContent;
  button.focus();
  button.click();
  const busy = byId("approve-button");
  const status = byId("approve-status");
  const inflight = { disabled: busy.disabled, status: status ? status.textContent : null };
  await waitFor("approved", () => {
    const output = byId("approve-output");
    return output && output.textContent.includes("approval #1 recorded:")
      && codes().includes("approved");
  });
  const ids = [...document.querySelectorAll("[id]")].map((node) => node.id);
  finish({ before, ready, inflight, codes: codes(),
    output: [...document.querySelectorAll("#approve-output li")].map((li) => li.textContent),
    disabled: byId("approve-button").disabled,
    specDocuments: document.querySelectorAll("#spec-document").length,
    duplicates: ids.filter((id, index) => ids.indexOf(id) !== index),
    overflow: document.documentElement.scrollWidth > window.innerWidth });
}

async function stale() {
  const button = await waitFor("enabled", enabled);
  button.focus();
  button.click();
  const reload = await waitFor("reload", () => byId("approve-reload"));
  const alert = document.querySelector("#approve-result [role=alert]");
  const shown = { alert: alert ? alert.textContent : null, active: document.activeElement.id };
  reload.click();
  await waitFor("reloaded", () => !byId("approve-reload") && enabled());
  const center = byId("approvals").getBoundingClientRect();
  finish({ shown, alerts: document.querySelectorAll("#approve-result [role=alert]").length,
    overflow: document.documentElement.scrollWidth > window.innerWidth,
    inside: center.right <= window.innerWidth });
}

const MODES = { approve, stale };

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


def _addressed(db_path: Path, task_id: str) -> int:
    def step(controller):
        service = CommentService(controller, task_id)
        service.diagrams.sync()
        comment = service.add(1, EDGE, "correction", "fixture: arrow points the wrong way")[0]
        service.address(comment.id, 1)
        return comment.id

    return _with(db_path, step)


def _count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_headless_chrome_approves_once_every_comment_is_resolved(
    db_path, sample_repo, tmp_path, monkeypatch
):
    task_id = approval_task(db_path, sample_repo)
    comment = _addressed(db_path, task_id)
    specs = tmp_path / "specs"
    with serve(db_path, task_id, str(specs)) as server:
        _shot(server, tmp_path / "shot-wide", "approval-center-1280w.png", 1280, 2400)
        _shot(server, tmp_path / "shot-narrow", "approval-center-390w.png", 390, 4200)
        _driven(tmp_path, monkeypatch, {"mode": "approve", "comment": comment})
        result = _result(_driven_dom(server, tmp_path / "profile", 1280, 800))
    before = result["before"]
    assert before["disabled"] is True and before["codes"] == ["comments"]
    assert before["described"] == "approve-blockers"
    assert before["groups"] == ["Decisions", "Test plan", "Final spec"]
    assert result["ready"].startswith("ready to approve: every comment is resolved")
    assert result["inflight"] == {"disabled": True, "status": "approving…"}
    assert result["output"][0].startswith("approved v1 -> ")
    assert result["output"][-1].startswith("approval #1 recorded: spec v1 sha256:")
    assert result["codes"] == ["approved"] and result["disabled"] is True
    assert result["specDocuments"] == 1 and result["duplicates"] == []
    assert result["overflow"] is False
    assert _count(db_path, "approvals") == 1
    assert sorted(p.name for p in (specs / task_id).iterdir()) == ["v1.md"]


def test_headless_chrome_refuses_a_stale_page_and_reloads(
    db_path, sample_repo, tmp_path, monkeypatch
):
    task_id = approval_task(db_path, sample_repo)
    specs = tmp_path / "specs"
    real = server_module.approve_task

    def redesigned_first(db, body, specs_root):
        _state(db, task_id, "component Exporter")
        _with(db, lambda c: DiagramService(c, task_id).sync())
        monkeypatch.setattr(server_module, "approve_task", real)
        return real(db, body, specs_root)

    monkeypatch.setattr(server_module, "approve_task", redesigned_first)
    with serve(db_path, task_id, str(specs)) as server:
        _driven(tmp_path, monkeypatch, {"mode": "stale"})
        result = _result(_driven_dom(server, tmp_path / "profile", 390, 844))
    alert = result["shown"]["alert"]
    assert alert.startswith("diagram version 1 is no longer current (current: 2); reload")
    assert result["shown"]["active"] == "approve-reload" and result["alerts"] == 0
    assert result["overflow"] is False and result["inside"] is True
    assert _count(db_path, "approvals") == 0 and not specs.exists()
