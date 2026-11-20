# ruff: noqa: F811
"""Real persisted incident dashboard acceptance in desktop and narrow Chrome."""

import html
import json
import os
import re
import shutil
import socket
import subprocess
import time
from importlib.resources import as_file
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import pytest

from harness.dashboard import server as server_module
from harness.dashboard.server import DashboardServer
from harness.reliability import store
from tests.test_dashboard import serve
from tests.test_dashboard_browser import CHROME
from tests.test_dashboard_execution_browser import _cdp_call
from tests.test_reliability_store import observed  # noqa: F401

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not installed"),
]

DRIVER = r"""
function waitFor(name, predicate) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const poll = () => {
      const value = predicate();
      if (value) resolve(value);
      else if (Date.now() - start > 8000) reject(new Error(`timed out: ${name}`));
      else setTimeout(poll, 40);
    };
    poll();
  });
}
async function painted() {
  await new Promise(requestAnimationFrame);
  await new Promise(requestAnimationFrame);
}
let keyboardToken = 0;
async function activateWithKeyboard(node) {
  node.focus();
  const token = ++keyboardToken;
  window.__reliabilityKeyboardRequest = {
    token, selector: node.id ? `#${node.id}` : "#operation-incidents details summary",
  };
  await waitFor("actual Chrome keyboard activation",
    () => window.__reliabilityKeyboardDone === token);
}
function finish(value) {
  const result = document.createElement("pre");
  result.id = "reliability-browser-result";
  result.hidden = true;
  result.textContent = JSON.stringify(value);
  document.body.append(result);
  window.__reliabilityDriverDone = true;
}
document.addEventListener("DOMContentLoaded", async () => {
  try {
    const privateTerms = __PRIVATE_TERMS__;
    const live = () => document.querySelector("#operation-incidents");
    const count = () => live()?.querySelectorAll(".incident-item").length || 0;
    await waitFor("persisted incidents", () => count() === 25);
    // Control network ordering while still driving the real parent refresh/render path.
    source?.close(); source = null;
    let listJSON = 0, detailJSON = 0;
    let holdLists = false, holdDetails = false;
    let lists = [], details = [];
    let privateAPI = true;
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, options) => {
      const url = String(input);
      const isDetail = url.includes("/api/execution/incidents/detail?");
      const isList = url.includes("/api/execution/incidents?");
      const response = await originalFetch(input, options);
      if (!isDetail && !isList) return response;
      const json = response.json.bind(response);
      response.json = async () => {
        const data = await json();
        privateAPI = privateAPI
          && privateTerms.every((term) => !JSON.stringify(data).includes(term));
        if (isDetail) detailJSON += 1; else listJSON += 1;
        return data;
      };
      if (isList && holdLists) return new Promise((resolve) => lists.push(() => resolve(response)));
      if (isDetail && holdDetails) {
        return new Promise((resolve) => details.push(() => resolve(response)));
      }
      return response;
    };
    async function refreshList() {
      const before = listJSON;
      await refreshDashboard();
      await waitFor("parent list response", () => listJSON > before);
      await painted();
    }
    function releaseDetails() {
      holdDetails = false; details.splice(0).forEach((release) => release());
    }
    function releaseLists() { holdLists = false; lists.splice(0).forEach((release) => release()); }
    const initialCount = count();
    const hasPending = live().textContent.includes("Outcome not recorded");
    const controlFits = ["#incident-search", "#incident-category"].every((selector) =>
      live().querySelector(selector).getBoundingClientRect().right <= window.innerWidth + 1);

    holdDetails = true;
    const firstId = live().querySelector(".incident-item button").id;
    await activateWithKeyboard(document.getElementById(firstId));
    const evidenceFocusOnStart = document.activeElement.id === firstId;
    await waitFor("held real detail response", () => details.length);
    await refreshList();
    const detailBefore = detailJSON;
    releaseDetails();
    await waitFor("released detail consumed", () => detailJSON > detailBefore);
    await painted();
    const delayedDetailSurvivesRefresh = Boolean(live().querySelector("details"))
      && !live().textContent.includes("Loading incident details");
    const evidenceFocusAfterResponse = document.activeElement.id === firstId;
    // Restore the real selection if this is the expected failing candidate.
    if (!live().querySelector("details")) {
      document.getElementById(firstId).click();
      document.getElementById(firstId).click();
      await waitFor("scoped detail", () => live()?.querySelector("details"));
    }
    const detailSummary = () => live()?.querySelector("details summary");
    await activateWithKeyboard(detailSummary());
    await painted();
    const summaryId = document.activeElement.id;
    const summaryFocusOnToggle = document.activeElement === detailSummary();
    const collapsedLocally = !live().querySelector("details").open;
    await refreshList();
    const collapsedAfterParentAndList = !live().querySelector("details").open;
    const summaryFocusAfterParentAndList = Boolean(summaryId)
      && document.activeElement === detailSummary() && document.activeElement.id === summaryId;

    // A detail selection must not invalidate an overlapping list's loading state.
    document.getElementById(firstId).click();
    holdLists = true;
    await refreshDashboard();
    await waitFor("held real list response", () => lists.length);
    const overlapDetailBefore = detailJSON;
    document.getElementById(firstId).click();
    await waitFor("overlapping detail consumed", () => detailJSON > overlapDetailBefore);
    const overlapListBefore = listJSON;
    releaseLists();
    await waitFor("overlapping list consumed", () => listJSON > overlapListBefore);
    await painted();
    const independentListAndDetail = !live().textContent.includes("Loading recorded outcomes")
      && !live().querySelector("#incident-older")?.disabled
      && Boolean(live().querySelector("details"));
    await refreshList();

    let search = live().querySelector("#incident-search");
    search.value = "<img src=x onerror=alert(1)>";
    let before = listJSON;
    search.dispatchEvent(new Event("input", { bubbles: true }));
    await waitFor("literal empty response", () => listJSON > before);
    await painted();
    const literalSafe = count() === 0 && live().querySelectorAll("img,script,iframe").length === 0;
    search = live().querySelector("#incident-search");
    search.value = "";
    search.dispatchEvent(new Event("input", { bubbles: true }));
    const category = live().querySelector("#incident-category");
    category.value = "operation_denied";
    before = listJSON;
    category.dispatchEvent(new Event("change", { bubbles: true }));
    await waitFor("denied response", () => listJSON > before && count() > 0
      && [...live().querySelectorAll(".incident-item h5")]
        .every((n) => n.textContent === "Operation denied"));
    live().querySelector("#incident-category").value = "";
    live().querySelector("#incident-category")
      .dispatchEvent(new Event("change", { bubbles: true }));
    await waitFor("all category results", () => count() === 25);

    search = live().querySelector("#incident-search");
    search.focus(); search.value = "<img"; search.setSelectionRange(1, 3);
    before = listJSON;
    search.dispatchEvent(new Event("input", { bubbles: true }));
    await waitFor("literal search response", () => listJSON > before && count() === 0);
    await painted();
    const focusAfterIncidentResponse = document.activeElement
      === live().querySelector("#incident-search");
    await refreshList();
    search = live().querySelector("#incident-search");
    const focusSurvivesRefresh = focusAfterIncidentResponse && document.activeElement === search
      && search.value === "<img" && search.selectionStart === 1 && search.selectionEnd === 3;
    search.value = "";
    search.dispatchEvent(new Event("input", { bubbles: true }));
    await waitFor("restore paginated results", () => count() === 25);
    document.getElementById("incident-older").click();
    await waitFor("older page", () => count() === 27);
    const olderCount = count();
    const oldButton = [...live().querySelectorAll(".incident-item button")].at(-1);
    const oldId = oldButton.id;
    await activateWithKeyboard(oldButton);
    await waitFor("older row details", () => live()?.querySelector("details")
      && document.getElementById(oldId)?.getAttribute("aria-expanded") === "true");
    const priorTop = document.getElementById(oldId).getBoundingClientRect().top;
    await refreshList();
    const loadedHistorySurvivesRefresh = count() === olderCount
      && Boolean(document.getElementById(oldId));
    const oldInvestigationSurvivesRefresh = document.activeElement.id === oldId
      && document.getElementById(oldId)?.getAttribute("aria-expanded") === "true"
      && Math.abs(document.getElementById(oldId).getBoundingClientRect().top - priorTop) < 2;
    const privateUI = privateTerms.every((term) => !live().textContent.includes(term));

    const activeEpoch = executionSnapshot.epoch_id;
    holdLists = true;
    search = live().querySelector("#incident-search");
    search.value = "";
    search.dispatchEvent(new Event("input", { bubbles: true }));
    await waitFor("delayed old scope", () => lists.length);
    // Hold only the old response; the new explicit scope must receive its real 404.
    holdLists = false;
    executionSnapshot.epoch_id = "foreign-epoch-for-ui-fence";
    repaintExecution();
    await waitFor("foreign scope response", () => live()?.querySelector("[role=alert]"));
    const staleBefore = listJSON;
    releaseLists();
    await waitFor("old response consumed", () => listJSON > staleBefore);
    await painted();
    const fenced = live().querySelectorAll(".incident-item").length === 0;
    executionSnapshot.epoch_id = activeEpoch;
    repaintExecution();
    search = live().querySelector("#incident-search");
    search.value = "";
    search.dispatchEvent(new Event("input", { bubbles: true }));
    await waitFor("restored real persisted rows", () => count() >= 25);
    await painted();
    async function fixtureAction(action) {
      const token = action;
      window.__reliabilityFixtureRequest = { token, action };
      return await waitFor(`persisted fixture ${action}`, () => {
        const result = window.__reliabilityFixtureResult;
        return result?.token === token && result;
      });
    }
    const visibleIds = () => [...live().querySelectorAll(".incident-item button")]
      .map((button) => button.id.replace("incident-detail-", ""));
    async function traverseOlder() {
      const seen = new Set(visibleIds());
      let maximumRendered = count(), pages = 1;
      for (let page = 0; page < 12; page += 1) {
        const older = live().querySelector("#incident-older");
        if (!older || older.disabled) break;
        const beforePage = listJSON;
        older.click();
        await waitFor("next persisted older page", () => listJSON > beforePage);
        await painted();
        visibleIds().forEach((id) => seen.add(id));
        maximumRendered = Math.max(maximumRendered, count());
        pages += 1;
      }
      return { seen: [...seen], maximumRendered, pages };
    }
    await waitFor("baseline history ready", () => count() === 25
      && !live().querySelector("#incident-older")?.disabled);
    const beforeBurst = visibleIds();
    const burst = await fixtureAction("burst");
    await refreshList();
    const passiveWindowPreserved = JSON.stringify(visibleIds()) === JSON.stringify(beforeBurst);
    const newArrivalsSignaled = Boolean(live().querySelector("#incident-latest"));
    const latest = live().querySelector("#incident-latest");
    if (latest) {
      const latestBefore = listJSON;
      latest.click();
      await waitFor("explicit latest page", () => listJSON > latestBefore);
      await painted();
    }
    const burstTraversal = await traverseOlder();
    const burstMissing = burst.ids.filter((id) => !burstTraversal.seen.includes(id));
    const burstComplete = burstMissing.length === 0;
    const deep = await fixtureAction("deep");
    search = live().querySelector("#incident-search");
    search.value = "";
    const deepBefore = listJSON;
    search.dispatchEvent(new Event("input", { bubbles: true }));
    await waitFor("fresh deep-history page", () => listJSON > deepBefore && count() === 25);
    await painted();
    const deepTraversal = await traverseOlder();
    const deepMissing = deep.ids.filter((id) => !deepTraversal.seen.includes(id));
    const deepComplete = deepMissing.length === 0;
    const renderedHistoryBounded = deepTraversal.maximumRendered <= 100;
    const viewport = window.innerWidth;
    const overflow = document.documentElement.scrollWidth > viewport;
    const maliciousNodes = live().querySelectorAll("img,script,iframe").length;
    live().scrollIntoView({ block: "start" });
    await painted();
    finish({ viewport, initialCount, olderCount, hasPending, literalSafe, focusSurvivesRefresh,
      controlFits, maliciousNodes, overflow, fenced, activeEpoch, privateAPI, privateUI,
      evidenceFocusOnStart, evidenceFocusAfterResponse, delayedDetailSurvivesRefresh,
      summaryFocusOnToggle, collapsedLocally, collapsedAfterParentAndList,
      summaryFocusAfterParentAndList, independentListAndDetail, loadedHistorySurvivesRefresh,
      oldInvestigationSurvivesRefresh, passiveWindowPreserved, newArrivalsSignaled,
      burstComplete, deepComplete, renderedHistoryBounded,
      burstFacts: { total: burst.ids.length, added: burst.added, missing: burstMissing,
        traversed: burstTraversal.seen.length, pages: burstTraversal.pages },
      deepFacts: { total: deep.ids.length, missing: deepMissing,
        traversed: deepTraversal.seen.length, pages: deepTraversal.pages,
        maximumRendered: deepTraversal.maximumRendered } });
  } catch (error) { finish({ error: error.message, stack: error.stack }); }
});
"""


def _install_driver(monkeypatch, tmp_path, private_terms):
    copied = tmp_path / "reliability-static"
    with as_file(server_module.STATIC_DIR) as source:
        shutil.copytree(source, copied)
    (copied / "reliability-driver.js").write_text(
        DRIVER.replace("__PRIVATE_TERMS__", json.dumps(private_terms)), encoding="utf-8"
    )
    page = copied / "index.html"
    body = page.read_text(encoding="utf-8")
    anchor = '<script src="/static/app.js" defer></script>'
    assert body.count(anchor) == 1
    page.write_text(
        body.replace(
            anchor, anchor + '\n    <script src="/static/reliability-driver.js" defer></script>'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "STATIC_DIR", copied)
    monkeypatch.setattr(
        server_module,
        "STATIC",
        server_module.STATIC
        | {
            "/static/reliability-driver.js": (
                "reliability-driver.js",
                "text/javascript; charset=utf-8",
            )
        },
    )


def _result(dom: str) -> dict:
    match = re.search(r'<pre id="reliability-browser-result"[^>]*>(.*?)</pre>', dom, re.S)
    assert match, dom[-2000:]
    return json.loads(html.unescape(match.group(1)))


def _capture_incident_view(
    server, task_id, profile: Path, output: Path, width: int, height: int, fixture_action
):
    profile.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            "--incognito",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            f"--user-data-dir={profile}",
            "--remote-debugging-port=0",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        port_file = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 10
        while not port_file.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Chrome exited before opening the DevTools port")
            time.sleep(0.05)
        if not port_file.exists():
            raise RuntimeError("Chrome DevTools port did not become ready")
        port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
        with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as response:
            target = next(row for row in json.load(response) if row.get("type") == "page")
        parsed = urlparse(target["webSocketDebuggerUrl"])
        sock = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        key = __import__("base64").b64encode(os.urandom(16)).decode()
        sock.sendall(
            (
                f"GET {parsed.path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        handshake = bytearray()
        while not handshake.endswith(b"\r\n\r\n"):
            handshake.extend(sock.recv(1))
        if b" 101 " not in handshake:
            raise RuntimeError("Chrome DevTools websocket handshake failed")
        sock.settimeout(10)
        call_id = 0

        def call(method, params=None):
            nonlocal call_id
            call_id += 1
            return _cdp_call(sock, call_id, method, params)

        call("Page.enable")
        call("Runtime.enable")
        call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": width < 600},
        )
        call("Page.navigate", {"url": f"{server.url}?task={task_id}"})
        deadline = time.monotonic() + 60
        handled_keyboard = set()
        handled_fixture = set()
        while time.monotonic() < deadline:
            state = (
                call(
                    "Runtime.evaluate",
                    {
                        "expression": "({done:Boolean(window.__reliabilityDriverDone), "
                        "keyboard:window.__reliabilityKeyboardRequest, "
                        "fixture:window.__reliabilityFixtureRequest})",
                        "returnByValue": True,
                    },
                )
                .get("result", {})
                .get("value", {})
            )
            keyboard = state.get("keyboard")
            if keyboard and keyboard["token"] not in handled_keyboard:
                call(
                    "Runtime.evaluate",
                    {
                        "expression": (
                            f"document.querySelector({json.dumps(keyboard['selector'])})?.focus()"
                        )
                    },
                )
                call(
                    "Input.dispatchKeyEvent",
                    {
                        "type": "rawKeyDown",
                        "key": "Enter",
                        "code": "Enter",
                        "windowsVirtualKeyCode": 13,
                        "nativeVirtualKeyCode": 36,
                    },
                )
                call(
                    "Input.dispatchKeyEvent",
                    {"type": "char", "key": "Enter", "code": "Enter", "text": "\r"},
                )
                call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter"})
                handled_keyboard.add(keyboard["token"])
                call(
                    "Runtime.evaluate",
                    {"expression": f"window.__reliabilityKeyboardDone={keyboard['token']}"},
                )
            fixture = state.get("fixture")
            if fixture and fixture["token"] not in handled_fixture:
                facts = fixture_action(fixture["action"])
                facts["token"] = fixture["token"]
                call(
                    "Runtime.evaluate",
                    {"expression": "window.__reliabilityFixtureResult=" + json.dumps(facts)},
                )
                handled_fixture.add(fixture["token"])
            if state.get("done"):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("incident view did not finish before screenshot")
        driver_result = (
            call(
                "Runtime.evaluate",
                {
                    "expression": (
                        "JSON.parse(document.getElementById('reliability-browser-result').textContent)"
                    ),
                    "returnByValue": True,
                },
            )
            .get("result", {})
            .get("value", {})
        )
        output.with_suffix(".json").write_text(json.dumps(driver_result, indent=2) + "\n")
        if driver_result.get("error"):
            raise RuntimeError(f"incident screenshot browser flow failed: {driver_result['error']}")
        call(
            "Runtime.evaluate",
            {
                "expression": (
                    "document.querySelector('#operation-incidents').scrollIntoView({block:'start'})"
                )
            },
        )
        capture = call(
            "Page.captureScreenshot",
            {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
        )
        output.write_bytes(__import__("base64").b64decode(capture["data"]))
        sock.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    assert output.is_file() and output.stat().st_size > 1024
    return driver_result


@pytest.mark.parametrize("name,width,height", [("1280x800", 1280, 800), ("390x844", 390, 844)])
def test_persisted_operation_incidents_in_chrome(
    observed, monkeypatch, tmp_path, name, width, height
):
    from contextlib import closing
    from datetime import UTC, datetime, timedelta
    from itertools import count

    from tests.test_reliability_api import record_private_dispatches

    operation, principal, _ = observed

    ticks = count()
    epoch = datetime.now(UTC)
    monkeypatch.setattr(
        store, "utcnow", lambda: (epoch + timedelta(seconds=next(ticks))).isoformat()
    )
    private_terms = record_private_dispatches(observed)
    for _index in range(25):
        observation = store.begin(operation, principal, "read_file")
        store.finish(operation, observation, "DISPATCH_REJECTED")
    store.begin(operation, principal, "run_command")

    output = Path(os.environ.get("HARNESS_DASHBOARD_SCREENSHOT_DIR", tmp_path / "incident-shots"))
    output.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HARNESS_DASHBOARD_SCREENSHOT_DIR", str(output))
    monkeypatch.setattr(DashboardServer, "stream_lifetime", 0.5)
    _install_driver(monkeypatch, tmp_path, private_terms)
    task_id = operation.envelope["task_id"]

    def fixture_action(action):
        from harness.db import connect

        with closing(connect(operation.database)) as conn:
            before_count = conn.execute("SELECT COUNT(*) FROM reliability_incidents").fetchone()[0]
        added = 30 if action == "burst" else 101 - before_count
        assert action in {"burst", "deep"} and added > 0
        for _ in range(added):
            oid = store.begin(operation, principal, "read_file")
            store.finish(operation, oid, "DISPATCH_REJECTED")
        with closing(connect(operation.database)) as conn:
            ids = [row[0] for row in conn.execute("SELECT incident_id FROM reliability_incidents")]
        assert len(ids) == before_count + added
        return {"ids": ids, "added": added}

    with serve(operation.database, task_id) as server:
        profile = tmp_path / f"chrome-{name}"
        screenshot = output / f"operation-incidents-{name}.png"
        results = [
            _capture_incident_view(
                server, task_id, profile / "shot", screenshot, width, height, fixture_action
            )
        ]

    for result in results:
        assert "error" not in result, result
        assert result["initialCount"] == 25 and result["olderCount"] == 27, result
        assert result["hasPending"] and result["literalSafe"] and result["focusSurvivesRefresh"], (
            result
        )
        assert result["controlFits"] and result["maliciousNodes"] == 0 and not result["overflow"], (
            result
        )
        assert result["fenced"], result
        for scenario in (
            "privateAPI",
            "privateUI",
            "evidenceFocusOnStart",
            "evidenceFocusAfterResponse",
            "delayedDetailSurvivesRefresh",
            "summaryFocusOnToggle",
            "collapsedLocally",
            "collapsedAfterParentAndList",
            "summaryFocusAfterParentAndList",
            "independentListAndDetail",
            "loadedHistorySurvivesRefresh",
            "oldInvestigationSurvivesRefresh",
            "passiveWindowPreserved",
            "newArrivalsSignaled",
            "burstComplete",
            "deepComplete",
            "renderedHistoryBounded",
        ):
            assert result[scenario], (scenario, result)
    assert {path.name for path in output.glob("operation-incidents-*.png")} == {
        f"operation-incidents-{name}.png",
    }
