# ruff: noqa: F811
"""Real timeline Chrome qualification reuses established native keyboard/network schedules."""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from importlib.resources import as_file
from itertools import count
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

import pytest

from harness.dashboard import server as server_module
from harness.dashboard.reliability_view import read_timeline
from harness.dashboard.server import DashboardServer
from harness.db import connect
from harness.reliability import process_store, store, timeline_store
from tests.test_dashboard import serve
from tests.test_dashboard_browser import CHROME
from tests.test_dashboard_execution_browser import _cdp_call
from tests.test_reliability_api import record_private_dispatches
from tests.test_reliability_browser import DRIVER as INCIDENT_DRIVER
from tests.test_reliability_processes import processes, settled, start_process  # noqa: F401
from tests.test_reliability_store import observed  # noqa: F401

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium unavailable"),
]


def timeline_driver():
    driver = INCIDENT_DRIVER
    for old, new in (
        ("/api/execution/incidents/detail?", "/api/execution/timeline/span?"),
        ("/api/execution/incidents?", "/api/execution/timeline?"),
        ("#operation-incidents", "#execution-timeline"),
        (".incident-item", ".timeline-item"),
        ("incident-search", "timeline-principal"),
        ("incident-category", "timeline-kind"),
        ("incident-older", "timeline-older"),
        ("incident-latest", "timeline-latest"),
        ("incident-detail-", "timeline-detail-"),
        ("operation_denied", "operation.denied"),
        ("Operation denied", "operation.denied"),
        ("Loading recorded outcomes", "Loading timeline"),
        ("Loading incident details", "Loading span evidence"),
        ("Outcome not recorded", "unavailable"),
    ):
        driver = driver.replace(old, new)
    driver = driver.replace(
        'const visibleIds = () => [...live().querySelectorAll(".timeline-item button")]\n'
        '      .map((button) => button.id.replace("timeline-detail-", ""));',
        'const visibleIds = () => [...live().querySelectorAll(".timeline-item")]\n'
        "      .map((node) => node.dataset.itemId);",
    )
    live_flow = LIVE_COMPLETION
    driver = driver.replace(
        "    const viewport = window.innerWidth;",
        live_flow + "    const viewport = window.innerWidth;",
    )
    driver = driver.replace(
        "      burstFacts: {",
        "      liveDetailUpdated, "
        "liveDetailCollapseRetained, liveDetailFocusRetained,\n      burstFacts: {",
    )
    return driver


LIVE_COMPLETION = r"""
    const liveFacts = await fixtureAction("live_start");
    await refreshList();
    const newest = live().querySelector("#timeline-latest");
    if (newest) {
      const beforeLatest = listJSON;
      newest.click();
      await waitFor("latest running process", () => listJSON > beforeLatest);
      await painted();
    }
    await activateWithKeyboard(live().querySelector(".timeline-item button"));
    await waitFor("running process evidence", () =>
      live()?.querySelector("details")?.dataset.timelineSpan === liveFacts.span_id);
    const terminalField = () => [...(live()?.querySelectorAll("details dt") || [])]
      .find((node) => node.textContent === "terminal state")?.nextElementSibling?.textContent;
    const initiallyRunning = terminalField() === "unavailable";
    await activateWithKeyboard(live().querySelector("details summary"));
    const selectedSummary = document.activeElement.id;
    await fixtureAction("live_stop");
    await refreshList();
    await waitFor("actual completed process detail refresh", () => terminalField() === "exited");
    await painted();
    const liveDetailUpdated = initiallyRunning && terminalField() === "exited";
    const liveDetailCollapseRetained = !live().querySelector("details").open;
    const liveDetailFocusRetained = document.activeElement.id === selectedSummary;
"""


def install_driver(monkeypatch, tmp_path, terms):
    copied = tmp_path / "timeline-static"
    with as_file(server_module.STATIC_DIR) as source:
        shutil.copytree(source, copied)
    (copied / "timeline-driver.js").write_text(
        timeline_driver().replace("__PRIVATE_TERMS__", json.dumps(terms))
    )
    page = copied / "index.html"
    anchor = '<script src="/static/app.js" defer></script>'
    body = page.read_text()
    assert body.count(anchor) == 1
    page.write_text(
        body.replace(anchor, anchor + '\n<script src="/static/timeline-driver.js" defer></script>')
    )
    monkeypatch.setattr(server_module, "STATIC_DIR", copied)
    monkeypatch.setattr(
        server_module,
        "STATIC",
        server_module.STATIC
        | {
            "/static/timeline-driver.js": ("timeline-driver.js", "text/javascript; charset=utf-8"),
        },
    )


def _capture_timeline(
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
                    "document.querySelector('#execution-timeline').scrollIntoView({block:'start'})"
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
def test_real_process_timeline_in_chrome(processes, monkeypatch, tmp_path, name, width, height):
    operation, principal, _ = processes
    ticks, epoch = count(), datetime.now(UTC)

    def clock():
        return (epoch + timedelta(seconds=next(ticks))).isoformat()

    for module in (store, process_store, timeline_store):
        monkeypatch.setattr(module, "utcnow", clock)
    secret = "timeline-browser-private-process-5281"
    _, handle = start_process(processes, f"print({secret!r})")
    assert secret in settled(processes, handle)["output"]
    terms = record_private_dispatches(processes) + [
        secret,
        hashlib.sha256(secret.encode()).hexdigest(),
    ]
    for _ in range(8):
        oid = store.begin(operation, principal, "read_file")
        store.finish(operation, oid, "DISPATCH_REJECTED")
    output = tmp_path / "timeline-shots"
    output.mkdir()
    monkeypatch.setattr(DashboardServer, "stream_lifetime", 0.5)
    install_driver(monkeypatch, tmp_path, terms)
    params = {
        "task": operation.envelope["task_id"],
        "execution": operation.execution_id,
        "epoch": operation.epoch_id,
        "limit": 100,
    }

    def source_ids():
        ids, cursor = [], None
        while True:
            status, data = read_timeline(operation.database, urlencode(params | (cursor or {})))
            assert status == 200
            ids.extend(item["item_id"] for item in data["items"])
            cursor = data["next_cursor"]
            if not cursor:
                return ids

    assert len(source_ids()) == 27

    live_process = {}

    def fixture_action(action):
        if action == "live_start":
            oid, live_process["handle"] = start_process(
                processes, "import time; time.sleep(20)", timeout=30
            )
            with closing(connect(operation.database)) as conn:
                row = conn.execute(
                    "SELECT span_id FROM reliability_process_spans WHERE parent_observation_id=?",
                    (oid,),
                ).fetchone()
            return {"span_id": row["span_id"]}
        if action == "live_stop":
            operation.processes.cancel(principal["thread_id"], live_process["handle"]["handle"])
            result = settled(processes, live_process["handle"])
            assert result["status"] == "Failed"
            return {"completed": True}
        before = len(source_ids())
        entries = 30 if action == "burst" else 101 - before
        assert entries > 0 and entries % 2 == 0
        for _ in range(entries // 2):
            oid = store.begin(operation, principal, "read_file")
            store.finish(operation, oid, "DISPATCH_REJECTED")
        ids = source_ids()
        assert len(ids) == before + entries
        return {"ids": ids, "added": entries}

    with serve(operation.database, operation.envelope["task_id"]) as server:
        result = _capture_timeline(
            server,
            operation.envelope["task_id"],
            tmp_path / "chrome",
            output / f"timeline-{name}.png",
            width,
            height,
            fixture_action,
        )
    assert "error" not in result, result
    assert result["initialCount"] == 25 and result["olderCount"] == 27
    assert result["overflow"] is False and result["maliciousNodes"] == 0
    for key in (
        "hasPending",
        "literalSafe",
        "focusSurvivesRefresh",
        "controlFits",
        "fenced",
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
        "liveDetailUpdated",
        "liveDetailCollapseRetained",
        "liveDetailFocusRetained",
    ):
        assert result[key], (key, result)
    assert result["burstFacts"]["traversed"] == 57 and result["deepFacts"]["traversed"] == 101
