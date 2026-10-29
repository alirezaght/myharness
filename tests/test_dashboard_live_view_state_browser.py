"""Mounted dashboard view state survives genuine SSE and local execution paints."""

import base64
import copy
import json
import os
import socket
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import pytest

from harness.dashboard import server as server_module
from tests.execution_helpers import approved_execution, started
from tests.test_dashboard import api, serve
from tests.test_dashboard_browser import CHROME
from tests.test_dashboard_execution_browser import _cdp_call

pytestmark = [pytest.mark.browser, pytest.mark.skipif(CHROME is None, reason="Chrome unavailable")]


@contextmanager
def chrome(profile, url, width, height):
    profile.mkdir()
    process = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            "--no-first-run",
            "--disable-gpu",
            "--disable-background-networking",
            f"--user-data-dir={profile}",
            "--remote-debugging-port=0",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sock = None
    try:
        port_file = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 10
        while not port_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        port = int(port_file.read_text().splitlines()[0])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
            target = next(t for t in json.load(response) if t["type"] == "page")
        parsed = urlparse(target["webSocketDebuggerUrl"])
        sock = socket.create_connection((parsed.hostname, parsed.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(
            (
                f"GET {parsed.path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
                f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        handshake = bytearray()
        while not handshake.endswith(b"\r\n\r\n"):
            handshake.extend(sock.recv(1))
        assert b" 101 " in handshake
        serial = 0

        def call(method, params=None):
            nonlocal serial
            serial += 1
            return _cdp_call(sock, serial, method, params)

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
        call("Page.navigate", {"url": url})
        yield call
    finally:
        if sock:
            sock.close()
        process.terminate()
        process.wait(timeout=10)


def evaluate(call, expression):
    result = call(
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        },
    )
    assert "exceptionDetails" not in result, result
    return result.get("result", {}).get("value")


def wait(call, expression):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if evaluate(call, expression):
            return
        time.sleep(0.05)
    pytest.fail(f"browser condition timed out: {expression}")


SETUP = r"""
window.latestView = null;
window.frames = 0;
const originalRender = render;
render = (data) => { latestView = structuredClone(data); frames++; originalRender(data); };
window.realFetch = window.fetch;
window.logCalls = 0;
window.fetch = (url, options) => {
  if (String(url).startsWith('/api/execution/logs?')) {
    logCalls++;
    return Promise.resolve({ok:true, json:async()=>({text:'Loaded fixture diagnostic'})});
  }
  return realFetch(url, options);
};
window.openViews = () => {
  document.querySelector('.task-metadata').open = true;
  document.querySelector('.execution-identities-disclosure').open = true;
  document.querySelector('.saved-decision-details').open = true;
  document.querySelector('.execution-logs details').open = true;
  document.querySelector('[data-delivery-disclosure="plan:P1"]').open = false;
  document.querySelector('[data-delivery-disclosure="approvals:history"]').open = true;
  document.querySelector('.execution-logs button').click();
};
"""

OBSERVE = r"""
({task:document.querySelector('.task-metadata').open,
 identities:document.querySelector('.execution-identities-disclosure').open,
 decision:document.querySelector('.saved-decision-details').open,
 closed:!document.querySelectorAll('.saved-decision-details')[1].open,
 log:document.querySelector('.execution-logs details').open,
 text:document.querySelector('.execution-logs pre').textContent,
 deliveryClosed:!document.querySelector('[data-delivery-disclosure="plan:P1"]').open,
 deliveryOpen:document.querySelector('[data-delivery-disclosure="approvals:history"]').open,
 logCalls, y:scrollY, frames, cursor:executionSnapshot.cursor,
 overflow:document.documentElement.scrollWidth > innerWidth})
"""


@pytest.mark.parametrize("width,height", [(1280, 800), (390, 844)])
def test_live_view_state(controller, sample_repo, monkeypatch, db_path, tmp_path, width, height):
    task, execution = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, execution)
    with serve(db_path, task) as server:
        baseline = api(server, f"?task={task}")
        state = baseline["task"]["execution"]
        state["blockers"] = [
            {"id": "B", "state": "Resolved", "question": "Fixture", "tasks": []},
            {"id": "draft", "revision": 1, "state": "Open", "question": "Draft", "tasks": []},
        ]
        state["developer_decisions"] = [state["blockers"][1]]
        baseline["task"]["approval_center"]["final"]["preview"] = [
            {"type": "paragraph", "lines": [[{"text": "Preview fixture " * 20}]]} for _ in range(40)
        ]
        state["decisions"] = [
            {"id": name, "blocker_id": "B", "state": "Resolved", "delivery_status": "acknowledged"}
            for name in ("D1", "D2")
        ]
        state["logs"] = [{"id": "L1", "channel": "stdout", "created_at": "fixture"}]
        state["delivery"] = {
            "browser_validation": {"plan": {"plan_id": "P1", "plan": {"criteria": []}}}
        }
        count = 0

        def live_view(*args, **kwargs):
            nonlocal count
            count += 1
            result = copy.deepcopy(baseline)
            result["task"]["execution"]["cursor"] = count
            return result

        monkeypatch.setattr(server_module, "dashboard_view", live_view)
        server.poll_seconds = 0.2
        with chrome(tmp_path / "chrome", server.url + f"?task={task}", width, height) as call:
            wait(call, "Boolean(document.querySelector('.saved-decision-details'))")
            evaluate(call, SETUP)
            evaluate(call, "openViews()")
            wait(call, "document.querySelector('.execution-logs pre').textContent !== ''")
            evaluate(
                call,
                """
                document.querySelector('#approve-preview').open=true;
                document.querySelector('#approve-preview-document').scrollTop=170;
                const draft=document.querySelector('#execution-decision-text');
                draft.value='Keep my selection'; draft.dispatchEvent(new Event('input'));
                draft.focus({preventScroll:true}); draft.setSelectionRange(2,7);
            """,
            )
            evaluate(
                call,
                "document.querySelector('.execution-logs').scrollIntoView(); "
                "window.startY=scrollY; window.startFrame=frames;",
            )
            before = evaluate(call, OBSERVE)
            assert before["y"] > 100
            evaluate(
                call,
                """window.positions=[]; window.watch=true;
                function watchScroll(){if(watch){positions.push(scrollY);
                  requestAnimationFrame(watchScroll)}};
                requestAnimationFrame(watchScroll)""",
            )
            wait(call, "frames >= startFrame + 3")
            after = evaluate(call, OBSERVE)
            evaluate(call, "watch=false")
            assert all(after[key] for key in ("task", "identities", "decision", "closed", "log")), (
                after
            )
            assert after["text"] == "Loaded fixture diagnostic", after
            assert after["logCalls"] == 1
            assert after["deliveryClosed"] and after["deliveryOpen"]
            assert evaluate(
                call,
                """({focus:document.activeElement.id,
                selection:[document.activeElement.selectionStart,document.activeElement.selectionEnd],
                preview:document.querySelector('#approve-preview-document').scrollTop})""",
            ) == {
                "focus": "execution-decision-text",
                "selection": [2, 7],
                "preview": 170,
            }
            assert after["cursor"] > before["cursor"]
            assert abs(after["y"] - before["y"]) <= 1, (before, after)
            assert evaluate(call, "positions.every(y => Math.abs(y-startY)<=1)"), after
            evaluate(call, "repaintExecution()")
            repainted = evaluate(call, OBSERVE)
            for key in ("task", "identities", "decision", "closed", "log", "text", "logCalls", "y"):
                assert repainted[key] == after[key], repainted
            assert not after["overflow"]
            directory = Path("/private/tmp/harness-scroll-fix-20261028/live-view-screenshots")
            directory.mkdir(parents=True, exist_ok=True)
            shot = call("Page.captureScreenshot", {"format": "png"})
            (directory / f"live-state-{width}x{height}.png").write_bytes(
                base64.b64decode(shot["data"])
            )
            evaluate(
                call,
                "document.querySelector('.task-metadata').open=false; window.startFrame=frames",
            )
            wait(call, "frames >= startFrame+2")
            assert evaluate(call, "!document.querySelector('.task-metadata').open")
            evaluate(
                call,
                """
                source.close();
                window.fetch=()=>new Promise(resolve=>window.finishLog=()=>resolve({ok:true,
                  json:async()=>({text:'Old delayed log'})}));
                document.querySelector('.execution-logs button').click();
                const changed=structuredClone(latestView);
                changed.task.execution.al_run_id='other-run'; render(changed);
                finishLog();
            """,
            )
            reset = evaluate(call, OBSERVE)
            assert not reset["task"] and not reset["identities"] and not reset["decision"]
            assert not reset["log"] and reset["text"] == ""
            assert evaluate(call, "document.querySelector('#execution-decision-text').value") == ""
            assert not evaluate(call, "document.querySelector('#approve-preview').open")
            evaluate(
                call,
                "openViews(); const other=structuredClone(latestView); "
                "other.task.id='other-task'; render(other)",
            )
            assert not evaluate(call, "document.querySelector('.task-metadata').open")
            assert evaluate(call, "document.querySelector('.execution-logs pre').textContent") == ""
