"""P2-01: a real headless Chrome renders the dashboard for the active task.

Skipped only when no Chrome/Chromium is installed. Set HARNESS_DASHBOARD_SCREENSHOT to a PNG
path to also save a 1280x800 screenshot of the active-task page.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from harness.dashboard.server import DashboardServer
from tests.test_dashboard import cli, serve, start_task

MAC_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
TIMEOUT = 60


def _chrome() -> str | None:
    if MAC_CHROME.is_file():
        return str(MAC_CHROME)
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if found := shutil.which(name):
            return found
    return None


CHROME = _chrome()
pytestmark = pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not installed")


def _headless(profile: Path, *args: str) -> subprocess.CompletedProcess:
    command = [
        CHROME,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--disable-extensions",
        f"--user-data-dir={profile}",
        "--virtual-time-budget=10000",
        *args,
    ]
    return subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT, check=False)


def _dom(server: DashboardServer, profile: Path) -> str:
    result = _headless(profile, "--dump-dom", server.url)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_headless_chrome_renders_the_active_task(db_path, tmp_path):
    task_id = start_task(db_path, "export", "<b>x</b>", "rows")
    cli(db_path, "phase", task_id, "next")
    with serve(db_path, task_id) as server:
        dom = _dom(server, tmp_path / "profile")
        if shot := os.environ.get("HARNESS_DASHBOARD_SCREENSHOT"):
            result = _headless(
                tmp_path / "profile-shot",
                "--window-size=1280,800",
                f"--screenshot={shot}",
                server.url,
            )
            assert result.returncode == 0 and Path(shot).is_file(), result.stderr
    assert f"<h2>Task {task_id}</h2>" in dom
    assert "<dd>export &lt;b&gt;x&lt;/b&gt; rows</dd>" in dom and "<b>x</b>" not in dom
    assert "<dd>interview</dd>" in dom and "<dd>trivial (developer)</dd>" in dom
    assert "<dd>not drafted yet</dd>" in dom and "Loading…" not in dom


def test_headless_chrome_shows_no_tasks_without_a_db(tmp_path):
    with serve(tmp_path / "absent" / "state.db", None) as server:
        dom = _dom(server, tmp_path / "profile")
    assert "<p>no active task</p>" in dom and "<p>no tasks</p>" in dom
    assert 'role="alert"' not in dom


def test_headless_chrome_reports_a_failed_load_with_retry(db_path, tmp_path):
    task_id = start_task(db_path, "csv", "export")
    specs = tmp_path / "specs"
    (specs / task_id).mkdir(parents=True)
    (specs / task_id / "v1.md").write_text("not a specification\n", encoding="utf-8")
    with serve(db_path, task_id, str(specs)) as server:
        dom = _dom(server, tmp_path / "profile")
    assert '<p role="alert">Dashboard data unavailable: ' in dom
    assert '<button type="button">Retry</button>' in dom and f"Task {task_id}" not in dom
