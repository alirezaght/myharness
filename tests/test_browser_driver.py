"""Actual isolated Chrome probe and real fixed-operation driver against loopback."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from harness.browser_capability import probe_capability
from harness.browser_driver import BrowserDriver

# Input and change listeners mirror the field into visible text, so a fill that does not
# set the value or dispatch the events leaves the echo paragraphs at "empty".
PAGE = b"""<h1>Hello</h1><input id="name">
<p id="typed">empty</p><p id="changed">empty</p>
<a href="/redirect" id="go">Go</a>
<script>
const field = document.getElementById("name");
field.addEventListener("input", () => { typed.textContent = "typed:" + field.value; });
field.addEventListener("change", () => { changed.textContent = "changed:" + field.value; });
</script>"""


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
            self.wfile.write(PAGE)

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
                    {"op": "assert_text", "selector": "#typed", "equals": "empty"},
                    {"op": "fill", "selector": "#name", "value": "filled value"},
                    {"op": "assert_text", "selector": "#typed", "equals": "typed:filled value"},
                    {"op": "assert_text", "selector": "#changed", "equals": "changed:filled value"},
                ]
            }
        )
        assert result["status"] == "passed", result
        assert [o["op"] for o in result["observations"]] == [
            "navigate",
            "assert_text",
            "assert_text",
            "fill",
            "assert_text",
            "assert_text",
        ]
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
    """Chrome's CDP pipe contract: the child reads commands on fd 3 and replies on fd 4."""
    import os
    import subprocess
    import sys

    child_read, parent_write = os.pipe()
    parent_read, child_write = os.pipe()
    from harness.browser_cdp import pipe_command

    echo = "import os\nrequest = os.read(3, 64)\nos.write(4, b'child-reply:' + request[::-1])\n"
    try:
        os.write(parent_write, b"parent-command")
        result = subprocess.run(
            pipe_command(child_read, child_write, [sys.executable, "-c", echo]),
            pass_fds=(child_read, child_write),
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        # Close the parent's copy of the write end so a missing reply reads b"" and fails
        # instead of blocking forever.
        os.close(child_write)
        child_write = None
        assert os.read(parent_read, 64) == b"child-reply:dnammoc-tnerap"
    finally:
        for fd in (child_read, parent_write, parent_read, child_write):
            if fd is not None:
                os.close(fd)


@pytest.mark.browser
def test_chrome_direct_startup_control(tmp_path):
    """Environment calibration, kept on purpose (issue #80, F17).

    BrowserDriver starts Chrome behind the fd 3/4 pipe bootstrap with
    --remote-debugging-pipe. This control starts the same executable with the same
    headless profile flags but no pipe or CDP. When the driver cannot start, the pair
    of this control and test_pipe_descriptor_diagnostic tells whether Chrome itself,
    the descriptor mapping, or the CDP exchange is at fault (a Chrome-level startup
    problem such as the macOS keychain prompt fixed in 34b697c fails here too).
    """
    import subprocess
    import tempfile

    from harness.browser_driver import chrome_executable

    with tempfile.TemporaryFile(mode="w+") as stdout, tempfile.TemporaryFile(mode="w+") as stderr:
        result = subprocess.run(
            [
                chrome_executable(),
                "--headless=new",
                "--use-mock-keychain",
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
