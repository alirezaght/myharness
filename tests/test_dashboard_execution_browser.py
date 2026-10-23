"""Desktop/mobile browser acceptance for the execution dashboard projection."""

import base64
import json
import os
import socket
import struct
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlparse

import pytest

from harness.dashboard.server import DashboardServer
from harness.execution_logs import append_log
from harness.execution_projection import ingest_event
from tests.execution_helpers import approved_execution, event, started
from tests.test_dashboard import serve
from tests.test_dashboard_browser import CHROME
from tests.test_dashboard_redesign_browser import _driven_dom

pytestmark = [pytest.mark.browser, pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not installed")]

DRIVER = r"""
const RESULT = {};
function finish(value) {
  Object.assign(RESULT, value);
  const node = document.createElement("pre");
  node.id = "execution-browser-result";
  node.textContent = JSON.stringify(RESULT);
  node.hidden = true;
  document.body.append(node);
}
function waitFor(name, predicate) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const poll = () => {
      const value = predicate();
      if (value) resolve(value);
      else if (Date.now() - started > 8000) reject(new Error(`timed out: ${name}`));
      else setTimeout(poll, 40);
    };
    poll();
  });
}
document.addEventListener("DOMContentLoaded", async () => {
  try {
    await waitFor("execution panel", () => document.querySelector("#execution .execution-blocker"));
    const textarea = document.querySelector(".execution-decision-form textarea");
    textarea.focus();
    textarea.value = "<img src=x onerror=alert(1)> Keep this explicit developer draft.";
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.setSelectionRange(5, 13);
    const before = { value: textarea.value, active: document.activeElement.id };
    await refreshDashboard();
    const restored = document.querySelector(".execution-decision-form textarea");
    const after = { value: restored.value, active: document.activeElement.id,
      selection: [restored.selectionStart, restored.selectionEnd] };
    const agentStatuses = [...document.querySelectorAll(".execution-agent > p:first-of-type")]
      .map((node) => node.textContent.replace("Status: ", ""));
    const graph = document.querySelector(".execution-graph-scroll");
    const controls = [...document.querySelectorAll(
      ".execution-decision-form textarea, .execution-decision-form select"
    )]
      .map((node) => ({ tag: node.tagName, id: node.id, width: node.getBoundingClientRect().width,
        left: node.getBoundingClientRect().left, right: node.getBoundingClientRect().right }));
    const taskConnections = [...document.querySelectorAll(".execution-task-node")].map((node) => ({
      title: node.querySelector("h5")?.textContent,
      text: [...node.querySelectorAll("p")].find((paragraph) =>
        paragraph.textContent.startsWith("Connections:")
      )?.textContent,
    }));
    const feedTimes = [...document.querySelectorAll(".execution-feed time")]
      .map((node) => ({ text: node.textContent, dateTime: node.dateTime }));
    finish({ before, after, agentStatuses,
      maliciousNodes: document.querySelectorAll("#execution img").length,
      sourceLabel: document.querySelector(".execution-summary [data-source-status]")?.textContent,
      browserLive: document.getElementById("live")?.textContent,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth,
      viewportWidth: window.innerWidth,
      graphContained: graph.getBoundingClientRect().width <= window.innerWidth,
      controls, taskConnections, feedTimes });
  } catch (error) { finish({ error: error.message }); }
});
"""


def _install_driver(tmp_path: Path, monkeypatch) -> None:
    import shutil
    from importlib.resources import as_file

    from harness.dashboard import server as server_module

    copied = tmp_path / "static"
    with as_file(server_module.STATIC_DIR) as real:
        shutil.copytree(real, copied)
    (copied / "driver.js").write_text(DRIVER, encoding="utf-8")
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


def _read_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise RuntimeError("Chrome DevTools websocket closed")
        chunks.extend(chunk)
    return bytes(chunks)


def _cdp_frame(sock: socket.socket) -> bytes:
    first, second = _read_exact(sock, 2)
    opcode, length = first & 0x0F, second & 0x7F
    if length == 126:
        length = struct.unpack("!H", _read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _read_exact(sock, 8))[0]
    mask = _read_exact(sock, 4) if second & 0x80 else b""
    payload = _read_exact(sock, length)
    if mask:
        payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    if opcode == 8:
        raise RuntimeError("Chrome DevTools websocket closed")
    if opcode == 9:
        _send_cdp_frame(sock, payload, opcode=10)
        return _cdp_frame(sock)
    return payload


def _send_cdp_frame(sock: socket.socket, payload: bytes, opcode: int = 1) -> None:
    mask = os.urandom(4)
    length = len(payload)
    header = bytearray([0x80 | opcode])
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    sock.sendall(header + mask + bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload)))


def _cdp_call(sock: socket.socket, call_id: int, method: str, params: dict | None = None) -> dict:
    message = {"id": call_id, "method": method, "params": params or {}}
    _send_cdp_frame(sock, json.dumps(message).encode())
    while True:
        reply = json.loads(_cdp_frame(sock))
        if reply.get("id") == call_id:
            if "error" in reply:
                raise RuntimeError(f"Chrome DevTools {method}: {reply['error']}")
            return reply.get("result", {})


def _cdp_capture(server, profile: Path, name: str, width: int, height: int, task_id: str) -> dict:
    directory = Path(os.environ["HARNESS_DASHBOARD_SCREENSHOT_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    shot = directory / name
    profile.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
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
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as response:
            targets = json.load(response)
        page = next(target for target in targets if target.get("type") == "page")
        parsed = urlparse(page["webSocketDebuggerUrl"])
        sock = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {parsed.path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode())
        handshake = bytearray()
        while not handshake.endswith(b"\r\n\r\n"):
            handshake.extend(sock.recv(1))
        if b" 101 " not in handshake:
            raise RuntimeError(f"Chrome DevTools websocket handshake failed: {handshake!r}")
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
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": 1,
                "mobile": width < 600,
            },
        )
        call("Page.navigate", {"url": f"{server.url}?task={quote(task_id)}"})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            observed = call(
                "Runtime.evaluate",
                {
                    "expression": (
                        "Boolean(document.querySelector('#execution .execution-blocker'))"
                    ),
                    "returnByValue": True,
                },
            )
            if observed.get("result", {}).get("value") is True:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("execution panel did not render before screenshot")
        stream_deadline = time.monotonic() + 10
        stream_state = {}
        while time.monotonic() < stream_deadline:
            stream_state = (
                call(
                    "Runtime.evaluate",
                    {
                        "expression": (
                            "({open: Boolean(source && source.readyState === EventSource.OPEN), "
                            "readyState: source?.readyState ?? null, "
                            "live: document.getElementById('live')?.dataset.state})"
                        ),
                        "returnByValue": True,
                    },
                )
                .get("result", {})
                .get("value", {})
            )
            if stream_state.get("open") is True:
                break
            time.sleep(0.1)
        assert stream_state.get("open") is True, stream_state
        assert stream_state.get("live") == "live", stream_state
        resources = (
            call(
                "Runtime.evaluate",
                {
                    "expression": (
                        "({names: performance.getEntriesByType('resource').map((entry) => "
                        "entry.name), "
                        "streamOpen: Boolean(source && source.readyState === EventSource.OPEN), "
                        "viewportWidth: window.innerWidth, "
                        "visualViewportWidth: window.visualViewport?.width, "
                        "scrollX: window.scrollX, "
                        "documentWidth: document.documentElement.scrollWidth, "
                        "bodyWidth: document.body.scrollWidth, controls: "
                        "[...document.querySelectorAll('.execution-decision-form textarea, "
                        ".execution-decision-form select')].map((node) => "
                        "({id: node.id, left: node.getBoundingClientRect().left, "
                        "right: node.getBoundingClientRect().right})), graph: (() => {"
                        "const node=document.querySelector('.execution-graph-scroll'); "
                        "return {clientWidth:node.clientWidth,scrollWidth:node.scrollWidth}})()})"
                    ),
                    "returnByValue": True,
                },
            )
            .get("result", {})
            .get("value", {})
        )
        resource_names = resources.get("names", [])
        assert resources.get("visualViewportWidth") == width, resources
        assert resources.get("documentWidth", 0) <= resources.get("viewportWidth", 0), resources
        assert all(control["right"] <= width + 1 for control in resources.get("controls", [])), (
            resources
        )
        # Capture may sample before or after the test driver's explicit refresh
        # completes. The initial load is required; more than one follow-up fetch
        # would indicate an unexpected request loop or duplicate load.
        dashboard_requests = sum("/api/dashboard" in resource for resource in resource_names)
        assert 1 <= dashboard_requests <= 2, resources
        assert resources.get("streamOpen") is True, resources
        assert len(resource_names) <= 20, resources
        capture = call(
            "Page.captureScreenshot",
            {
                "format": "png",
                "fromSurface": True,
                "captureBeyondViewport": False,
            },
        )
        shot.write_bytes(base64.b64decode(capture["data"]))

        overview_shot = directory / name.replace("execution-", "execution-overview-", 1)
        call(
            "Runtime.evaluate",
            {
                "expression": (
                    "window.scrollTo(0, "
                    "document.querySelector('#execution').getBoundingClientRect().top "
                    "+ window.scrollY)"
                ),
                "returnByValue": True,
            },
        )
        overview = call(
            "Page.captureScreenshot",
            {
                "format": "png",
                "fromSurface": True,
                "captureBeyondViewport": False,
            },
        )
        overview_shot.write_bytes(base64.b64decode(overview["data"]))

        graph_shot = directory / name.replace("execution-", "execution-graph-", 1)
        call(
            "Runtime.evaluate",
            {
                "expression": (
                    "document.querySelector('.execution-graph').scrollIntoView({block: 'start'})"
                ),
                "returnByValue": True,
            },
        )
        graph = call(
            "Page.captureScreenshot",
            {
                "format": "png",
                "fromSurface": True,
                "captureBeyondViewport": False,
            },
        )
        graph_shot.write_bytes(base64.b64decode(graph["data"]))
        sock.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    for artifact in (shot, overview_shot, graph_shot):
        assert artifact.is_file() and artifact.stat().st_size > 1024
    import hashlib

    return {
        "resource_count": len(resource_names),
        "dashboard_requests": dashboard_requests,
        "stream_open": True,
        "viewport_width": resources["visualViewportWidth"],
        "layout_viewport_width": resources["viewportWidth"],
        "document_width": resources["documentWidth"],
        "global_horizontal_overflow": resources["documentWidth"] > resources["viewportWidth"],
        "controls": resources["controls"],
        "graph": resources["graph"],
        "screenshots": {
            str(artifact): hashlib.sha256(artifact.read_bytes()).hexdigest()
            for artifact in (shot, overview_shot, graph_shot)
        },
    }


def _result(dom: str) -> dict:
    import html
    import re

    match = re.search(r'<pre id="execution-browser-result"[^>]*>(.*?)</pre>', dom, re.S)
    assert match, dom[-2000:]
    return json.loads(html.unescape(match.group(1)))


def test_execution_panel_keeps_decision_draft_and_fits_desktop_and_mobile(
    controller, sample_repo, monkeypatch, db_path, tmp_path
):
    # Screenshots go to the supervised directory when one is set, else to this test.
    monkeypatch.setenv(
        "HARNESS_DASHBOARD_SCREENSHOT_DIR",
        os.environ.get("HARNESS_DASHBOARD_SCREENSHOT_DIR") or str(tmp_path / "screenshots"),
    )
    task_id, execution_id = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, execution_id)
    tasks = [
        {
            "id": "taskA",
            "title": "Architecture choice <unsafe>",
            "status": "Idle",
            "dependencies": [],
            "files": ["demo"],
            "criteria": ["AC-01"],
            "revision": 1,
        },
        {
            "id": "taskB",
            "title": "Blocked dependent",
            "status": "Idle",
            "dependencies": ["taskA"],
            "files": ["tests"],
            "criteria": ["AC-01"],
            "revision": 1,
        },
        {
            "id": "taskC",
            "title": "Independent sibling",
            "status": "Idle",
            "dependencies": [],
            "files": ["docs"],
            "criteria": ["AC-01"],
            "revision": 1,
        },
    ]
    sequence = 2
    for task in tasks:
        ingest_event(
            controller.conn,
            execution_id,
            event(controller.conn, execution_id, sequence, "task.registered", task),
        )
        sequence += 1
    statuses = ["Idle", "Running", "Waiting", "Blocked", "Failed", "Completed"]
    for index, status in enumerate(statuses):
        agent = {
            "id": f"agent{index}",
            "name": f"Agent {index} <unsafe>",
            "role": "al-engineer",
            "task": "taskC" if index == 5 else "taskA",
            "status": status,
            "activity": "Inspecting approved task",
            "latest_output": "<script>raw text</script>",
        }
        if index == 1:
            agent["progress"] = {"completed": 2, "total": 4, "unit": "checks"}
        if index == 2:
            agent["progress"] = {
                "value": 40,
                "source": "agent estimate",
                "label": "implementation estimate",
            }
        ingest_event(
            controller.conn,
            execution_id,
            event(controller.conn, execution_id, sequence, "agent.registered", agent),
        )
        sequence += 1
    blocker = {
        "id": "design1",
        "revision": 1,
        "tasks": ["taskA"],
        "affected": ["taskA", "taskB"],
        "question": "Which storage design?",
        "context": "The approved plan leaves this choice open.",
        "alternatives": ["Keep the current format", "Add an indexed format"],
        "recommendation": "Keep the current format.",
    }
    ingest_event(
        controller.conn,
        execution_id,
        event(controller.conn, execution_id, sequence, "blocker.raised", blocker),
    )
    append_log(controller.conn, execution_id, "worker", "bounded diagnostic example")

    _install_driver(tmp_path, monkeypatch)
    monkeypatch.setattr(DashboardServer, "stream_lifetime", 0.5)
    with serve(db_path, task_id) as server:
        wide_dom = _driven_dom(server, tmp_path / "wide-profile", 1280, 800)
        wide = _result(wide_dom)
        mobile_dom = _driven_dom(server, tmp_path / "mobile-profile", 390, 844)
        mobile = _result(mobile_dom)
        directory = Path(os.environ["HARNESS_DASHBOARD_SCREENSHOT_DIR"])
        monkeypatch.setattr(DashboardServer, "stream_lifetime", 30.0)
        wide_resources = _cdp_capture(
            server, tmp_path / "wide-shot", "execution-1280x800.png", 1280, 800, task_id
        )
        mobile_resources = _cdp_capture(
            server, tmp_path / "mobile-shot", "execution-390x844.png", 390, 844, task_id
        )

    for observed in (wide, mobile):
        assert "error" not in observed
        assert observed["before"]["value"] == observed["after"]["value"]
        assert observed["after"]["active"] == "execution-decision-text"
        assert observed["after"]["selection"] == [5, 13]
        assert observed["agentStatuses"] == statuses
        assert observed["maliciousNodes"] == 0
        assert observed["sourceLabel"] == "AL source: Current"
        assert observed["browserLive"] == "live connection lost; reconnecting…"
        assert observed["horizontalOverflow"] is False
        assert observed["graphContained"] is True
        viewport_width = observed["viewportWidth"]
        assert all(control["right"] <= viewport_width + 1 for control in observed["controls"]), (
            f"decision control extends beyond {viewport_width}px viewport: {observed['controls']}"
        )
        blocked = next(
            item for item in observed["taskConnections"] if item["title"] == "Blocked dependent"
        )
        assert blocked["text"].count("dependency: taskB → taskA") == 1, blocked
        assert observed["feedTimes"]
        assert all(
            value["dateTime"] and not value["text"].strip().replace(".", "", 1).isdigit()
            for value in observed["feedTimes"]
        ), observed["feedTimes"]
    assert (directory / "execution-1280x800.png").is_file()
    assert (directory / "execution-390x844.png").is_file()
    assert 1 <= wide_resources["dashboard_requests"] <= 2
    assert 1 <= mobile_resources["dashboard_requests"] <= 2
    assert wide_resources["stream_open"] is mobile_resources["stream_open"] is True
    (directory / "execution-capture-metrics.json").write_text(
        json.dumps(
            {"desktop_capture": wide_resources, "mobile_capture": mobile_resources},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
