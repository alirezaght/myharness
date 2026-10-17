"""P2-01: the dashboard launcher prints the URL, opens the browser on it and stops cleanly."""

import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import pytest

from harness.dashboard import launch
from harness.dashboard.launch import build_parser, main, start_dashboard
from tests.test_dashboard import start_task

REPO_ROOT = Path(__file__).resolve().parents[1]
URL_LINE = re.compile(r"^dashboard: (http://127\.0\.0\.1:(\d+)/) · (.+) · Ctrl-C stops it$")
TIMEOUT = 30


def test_parser_takes_an_optional_task_and_the_cli_paths():
    args = build_parser().parse_args(["t-1", "--db", "x.db", "--specs", "s", "--no-browser"])
    assert (args.task_id, args.db, args.specs, args.no_browser) == ("t-1", "x.db", "s", True)
    none = build_parser().parse_args([])
    assert (none.task_id, none.db, none.specs, none.no_browser) == (None, None, None, False)


def test_launcher_opens_the_browser_on_the_served_url(db_path, monkeypatch, capsys):
    # Justified fake: the real webbrowser.open would open a desktop window; the real path
    # through the stdlib browser lookup is covered end to end by the subprocess test below.
    task_id = start_task(db_path, "csv", "export")
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    server = start_dashboard(db_path, task_id, None, open_browser=True)
    try:
        out = capsys.readouterr().out.splitlines()
        assert out == [f"dashboard: {server.url} · task {task_id} · Ctrl-C stops it"]
        assert opened == [server.url]
    finally:
        server.server_close()

    server = start_dashboard(db_path, None, None, open_browser=False)
    server.server_close()
    assert len(opened) == 1
    assert capsys.readouterr().out == (
        f"dashboard: {server.url} · no active task · Ctrl-C stops it\n"
    )


@pytest.mark.parametrize("failure", ["false", "error"])
def test_launcher_says_so_when_no_browser_opens(db_path, monkeypatch, capsys, failure):
    def refuse(url):
        if failure == "error":
            raise webbrowser.Error("no runnable browser")
        return False

    monkeypatch.setattr(webbrowser, "open", refuse)
    server = start_dashboard(db_path, None, None, open_browser=True)
    server.server_close()
    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == f"could not open a browser; open {server.url} yourself"


def test_bind_failure_is_reported(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise OSError("address unavailable")

    monkeypatch.setattr(launch, "DashboardServer", fail)
    previous = signal.getsignal(signal.SIGTERM)
    assert main(["--no-browser"]) == 1
    assert capsys.readouterr().err == "harness dashboard: address unavailable\n"
    assert signal.getsignal(signal.SIGTERM) is previous


def _fake_browser(tmp_path: Path) -> tuple[str, Path]:
    record = tmp_path / "opened.txt"
    script = tmp_path / "fake_browser.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys
            with open({str(record)!r}, "a", encoding="utf-8") as handle:
                handle.write(sys.argv[1] + "\\n")
            """
        ),
        encoding="utf-8",
    )
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} %s", record


def _launch(db_path: Path, browser: str, *args: str) -> tuple[subprocess.Popen, str, int]:
    env = {**os.environ, "BROWSER": browser}
    child = subprocess.Popen(
        [sys.executable, "-m", "harness.dashboard", *args, "--db", str(db_path)],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    lines: list[str] = []
    reader = threading.Thread(target=lambda: lines.append(child.stdout.readline()), daemon=True)
    reader.start()
    reader.join(TIMEOUT)
    if not lines:
        child.kill()
        pytest.fail("the dashboard printed no URL line")
    match = URL_LINE.match(lines[0].rstrip("\n"))
    assert match, lines[0]
    return child, match.group(1), int(match.group(2))


def _wait_for(path: Path) -> str:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if path.exists() and path.read_text(encoding="utf-8").endswith("\n"):
            return path.read_text(encoding="utf-8")
        time.sleep(0.05)
    pytest.fail(f"{path} was never written")


def _stop(child: subprocess.Popen, signum: int) -> str:
    child.send_signal(signum)
    out, err = child.communicate(timeout=TIMEOUT)
    assert (child.returncode, err) == (0, ""), err
    return out


def _refused(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
    except OSError:
        return True
    return False


def test_end_to_end_launch_opens_the_active_task(db_path, tmp_path):
    task_id = start_task(db_path, "csv", "export")
    browser, record = _fake_browser(tmp_path)
    child, url, port = _launch(db_path, browser, task_id)
    try:
        assert _wait_for(record) == f"{url}\n"
        with urllib.request.urlopen(f"{url}api/dashboard", timeout=10) as response:
            data = json.loads(response.read())
        assert data["task"]["id"] == task_id and data["task"]["phase"] == "investigation"
    finally:
        out = _stop(child, signal.SIGINT)
    assert out.splitlines()[-1] == "dashboard stopped"
    assert _refused(port)


def test_end_to_end_no_browser_and_sigterm(db_path, tmp_path):
    browser, record = _fake_browser(tmp_path)
    child, url, port = _launch(db_path, browser, "--no-browser")
    try:
        with urllib.request.urlopen(f"{url}api/dashboard", timeout=10) as response:
            assert json.loads(response.read())["notice"] == "no active task"
    finally:
        out = _stop(child, signal.SIGTERM)
    assert out == "dashboard stopped\n"
    assert not record.exists() and _refused(port)
