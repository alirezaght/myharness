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
from tests.test_dashboard import needs_git, serve
from tests.test_dashboard_approval import approval_task
from tests.test_dashboard_browser import CHROME, _headless
from tests.test_dashboard_comments import EDGE
from tests.test_dashboard_comments_browser import _result
from tests.test_dashboard_diagrams import _with
from tests.test_dashboard_redesign_browser import _driven_dom, _shot

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not installed"),
    needs_git,
]

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


DRIVER = r"""
const RESULT = {};
function finish(value) {
  Object.assign(RESULT, value);
  const out = document.createElement("pre");
  out.id = "approval-scroll-result";
  out.textContent = JSON.stringify(RESULT);
  document.body.append(out);
}
function waitFor(predicate) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const poll = () => predicate() ? resolve() : Date.now() - start > 8000
      ? reject(new Error("approval preview did not render")) : setTimeout(poll, 40);
    poll();
  });
}
document.addEventListener("DOMContentLoaded", async () => {
  try {
    await waitFor(() => document.querySelector("#approve-preview-document"));
    const renderCenter = renderApprovalCenter;
    let longPreview = true;
    renderApprovalCenter = (task) => {
      const center = renderCenter(task);
      const article = center.querySelector("#approve-preview-document");
      if (longPreview && article) {
        for (let index = 0; index < 40; index += 1) {
          const text = `scroll fixture paragraph ${index}: ${"preview text ".repeat(12)}`;
          article.append(el("p", text));
        }
      }
      return center;
    };
    repaintApprovals();
    const details = document.getElementById("approve-preview");
    details.open = true;
    const preview = () => document.getElementById("approve-preview-document");
    preview().scrollTop = 240;
    const initialScroll = { top: preview().scrollTop,
      max: preview().scrollHeight - preview().clientHeight };
    await refreshDashboard();
    const repeatedRefresh = preview().scrollTop;
    preview().scrollTop = 175;
    repaintApprovals();
    const approvalRepaint = preview().scrollTop;
    preview().scrollTop = 95;
    await refreshDashboard();
    const userPosition = preview().scrollTop;

    preview().scrollTop = 210;
    const short = await fetch(`/api/dashboard?task=${encodeURIComponent(DRIVE.task)}`)
      .then((response) => response.json());
    short.task.approval_center.final.preview = [
      { type: "paragraph", lines: [[{ text: "short" }]] }
    ];
    longPreview = false;
    render(short);
    const clamped = preview().scrollTop;

    longPreview = true;
    preview().scrollTop = 150;
    const other = await fetch(`/api/dashboard?task=${encodeURIComponent(DRIVE.other)}`)
      .then((response) => response.json());
    render(other);
    const taskChanged = { id: document.getElementById("approve-preview").dataset.taskId,
      top: preview().scrollTop };

    const current = await fetch(`/api/dashboard?task=${encodeURIComponent(DRIVE.task)}`)
      .then((response) => response.json());
    render(current);
    document.getElementById("approve-preview").open = true;
    preview().scrollTop = 105;
    const originalFetch = window.fetch;
    window.fetch = (input, options) => String(input).startsWith("/api/dashboard")
      ? Promise.reject(new Error("controlled fetch failure")) : originalFetch(input, options);
    await refreshDashboard();
    window.fetch = originalFetch;
    const fetchFailure = preview().scrollTop;

    const center = document.getElementById("approvals");
    document.getElementById("approve-preview").remove();
    withApprovalFocus(() => center.replaceWith(renderApprovalCenter(approvalTask)));
    finish({ initialScroll, repeatedRefresh, approvalRepaint, userPosition, clamped,
      taskChanged, fetchFailure,
      missingPreviewRepainted: !!document.getElementById("approve-preview"),
      taskId: document.getElementById("approve-preview").dataset.taskId,
      viewportWidth: innerWidth, documentWidth: document.documentElement.scrollWidth });
  } catch (error) { finish({ error: error.message }); }
});
"""


def _install_scroll_driver(tmp_path: Path, monkeypatch, task: str, other: str) -> None:
    copied = tmp_path / "static"
    with as_file(server_module.STATIC_DIR) as real:
        shutil.copytree(real, copied)
    (copied / "driver.js").write_text(
        f'"use strict";\nconst DRIVE = {json.dumps({"task": task, "other": other})};\n{DRIVER}',
        encoding="utf-8",
    )
    page = copied / "index.html"
    source = page.read_text(encoding="utf-8")
    tag = '<script src="/static/app.js" defer></script>'
    assert source.count(tag) == 1
    page.write_text(
        source.replace(tag, f'{tag}\n    <script src="/static/driver.js" defer></script>'),
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "STATIC_DIR", copied)
    monkeypatch.setattr(
        server_module,
        "STATIC",
        server_module.STATIC
        | {"/static/driver.js": ("driver.js", "text/javascript; charset=utf-8")},
    )


def test_preview_scroll_survives_refresh_repaint_and_stays_task_scoped(
    db_path, sample_repo, tmp_path, monkeypatch
):
    task = approval_task(db_path, sample_repo)
    other = approval_task(db_path, sample_repo)
    _install_scroll_driver(tmp_path, monkeypatch, task, other)
    monkeypatch.setattr(DashboardServer, "stream_lifetime", 0.5)
    import re

    captures = {}
    with serve(db_path, task) as server:
        for name, width, height in (("desktop", 1280, 800), ("mobile", 390, 844)):
            url = f"{server.url}?task={task}"
            viewport = f"--window-size={width},{height}"
            result = _headless(
                tmp_path / f"{name}-driver-profile",
                viewport,
                "--virtual-time-budget=12000",
                "--dump-dom",
                url,
            )
            assert result.returncode == 0, result.stderr
            match = re.search(
                r'<pre id="approval-scroll-result"[^>]*>(.*?)</pre>', result.stdout, re.S
            )
            assert match, result.stdout[-1500:]
            captures[name] = json.loads(match.group(1))
    for name, observed in captures.items():
        assert "error" not in observed, {"viewport": name, **observed}
        assert observed["initialScroll"]["top"] > 0
        assert observed["initialScroll"]["max"] > 0
        assert observed["repeatedRefresh"] == 240
        assert observed["approvalRepaint"] == 175
        assert observed["userPosition"] == 95
        assert observed["clamped"] == 0
        assert observed["taskChanged"] == {"id": other, "top": 0}
        assert observed["fetchFailure"] == 105
        assert observed["missingPreviewRepainted"] is True
        assert observed["taskId"] == task
        assert observed["documentWidth"] <= observed["viewportWidth"]
