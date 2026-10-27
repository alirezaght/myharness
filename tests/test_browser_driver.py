"""Actual isolated Chrome probe and real fixed-operation driver against loopback."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from harness.browser_capability import probe_capability
from harness.browser_driver import BrowserDriver


@pytest.mark.browser
def test_real_driver_and_interception(tmp_path):
    capability = probe_capability()
    assert capability["available"], capability.get("error", capability)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", target + "/forbidden")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b'<h1>Hello</h1><input id="name"><a href="/redirect" id="go">Go</a>')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    target = f"http://127.0.0.1:{server.server_port}"
    plan = {
        "timeouts": {"action_ms": 2000, "run_ms": 15000},
        "viewport": {"width": 1280, "height": 800},
        "navigation_scope": [
            {"origin": target + "/", "path_prefix": "/allowed"},
            {"origin": target + "/", "path_prefix": "/redirect"},
        ],
        "evidence": {"screenshot": "each_criterion", "mask_selectors": []},
    }
    driver = None
    try:
        driver = BrowserDriver(plan, tmp_path)
        result = driver.run_criterion(
            {
                "steps": [
                    {"op": "navigate", "url": target + "/allowed"},
                    {"op": "assert_text", "selector": "h1", "equals": "Hello"},
                    {"op": "fill", "selector": "#name", "value": "test"},
                ]
            }
        )
        assert result["status"] == "passed", result
        assert result["evidence"][0]["size"] > 0
        result = driver.run_criterion(
            {
                "steps": [
                    {"op": "click", "selector": "#go"},
                    {"op": "assert_text", "selector": "h1", "equals": "Unreachable"},
                ]
            }
        )
        assert result["status"] != "passed"
        assert "/forbidden" not in requests
    finally:
        if driver:
            driver.close()
        server.shutdown()
        server.server_close()


def test_pipe_descriptor_diagnostic():
    import os
    import subprocess
    import sys

    child_read, parent_write = os.pipe()
    parent_read, child_write = os.pipe()
    from harness.browser_cdp import pipe_command

    try:
        result = subprocess.run(
            pipe_command(
                child_read, child_write, [sys.executable, "-c", "import os;os.fstat(3);os.fstat(4)"]
            ),
            pass_fds=(child_read, child_write),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    finally:
        for fd in (child_read, parent_write, parent_read, child_write):
            os.close(fd)


@pytest.mark.browser
def test_chrome_direct_startup_control(tmp_path):
    import subprocess
    import tempfile

    from harness.browser_driver import chrome_executable

    with tempfile.TemporaryFile(mode="w+") as stdout, tempfile.TemporaryFile(mode="w+") as stderr:
        result = subprocess.run(
            [
                chrome_executable(),
                "--headless=new",
                "--incognito",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-sync",
                "--disable-extensions",
                "--user-data-dir=" + str(tmp_path / "direct-profile"),
                "--dump-dom",
                "about:blank",
            ],
            stdout=stdout,
            stderr=stderr,
            text=True,
            timeout=20,
        )
        stdout.seek(0)
        stderr.seek(0)
        result = subprocess.CompletedProcess(
            result.args, result.returncode, stdout.read(), stderr.read()
        )
    assert result.returncode == 0, (result.returncode, result.stderr)


def test_lost_interaction_reply_is_never_repeated(tmp_path):
    driver = object.__new__(BrowserDriver)
    driver.plan = {
        "timeouts": {"action_ms": 100, "run_ms": 1000},
        "evidence": {"screenshot": "none"},
    }
    driver.deadline = __import__("time").monotonic() + 1
    calls = []

    def lost(step):
        calls.append(step)
        raise OSError("lost reply")

    driver._step = lost
    result = driver.run_criterion({"steps": [{"op": "click", "selector": "button"}]})
    assert result["status"] == "uncertain"
    assert len(calls) == 1
