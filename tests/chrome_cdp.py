"""A headless Chrome page driven over CDP that records every network request.

Browser tests use it to observe runtime outcomes (rendered DOM, executed handlers,
issued requests) instead of searching static source for symbols.
"""

import base64
import json
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from tests.test_dashboard_browser import CHROME
from tests.test_dashboard_execution_browser import _cdp_frame, _send_cdp_frame


class ChromePage:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._id = 0
        self.requests: list[dict] = []

    def _record(self, message: dict) -> None:
        if message.get("method") == "Network.requestWillBeSent":
            request = message["params"]["request"]
            self.requests.append({"method": request["method"], "url": request["url"]})

    def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        call_id = self._id
        _send_cdp_frame(
            self._sock,
            json.dumps({"id": call_id, "method": method, "params": params or {}}).encode(),
        )
        while True:
            message = json.loads(_cdp_frame(self._sock))
            if message.get("id") == call_id:
                if "error" in message:
                    raise RuntimeError(f"Chrome DevTools {method}: {message['error']}")
                return message.get("result", {})
            self._record(message)

    def evaluate(self, expression: str):
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
        )
        if "exceptionDetails" in result:
            raise RuntimeError(f"page script failed: {result['exceptionDetails']}")
        return result["result"].get("value")

    def wait_for(self, expression: str, timeout: float = 15):
        deadline = time.monotonic() + timeout
        while True:
            try:
                value = self.evaluate(expression)
            except RuntimeError:
                # A navigation can destroy the execution context mid-evaluation.
                value = None
            if value:
                return value
            if time.monotonic() >= deadline:
                raise AssertionError(f"timed out waiting for: {expression}")
            time.sleep(0.05)

    def settle(self, seconds: float = 0.3) -> None:
        """Pump CDP events for a while so late requests are recorded."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.evaluate("1")
            time.sleep(0.03)

    def open(self, url: str, ready: str, timeout: float = 15):
        self.call("Page.navigate", {"url": url})
        return self.wait_for(ready, timeout)


@contextmanager
def chrome_page(profile: Path, width: int = 1280, height: int = 800) -> Iterator[ChromePage]:
    if CHROME is None:
        raise RuntimeError("Chrome/Chromium not installed")
    profile.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            "--use-mock-keychain",
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
    sock = None
    try:
        port_file = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 10
        while not port_file.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Chrome exited before opening the DevTools port")
            time.sleep(0.05)
        port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
        with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as response:
            target = next(row for row in json.load(response) if row.get("type") == "page")
        parsed = urlparse(target["webSocketDebuggerUrl"])
        sock = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
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
        page = ChromePage(sock)
        page.call("Page.enable")
        page.call("Runtime.enable")
        page.call("Network.enable")
        page.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": width < 600},
        )
        yield page
    finally:
        if sock is not None:
            sock.close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
