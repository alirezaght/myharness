"""P2-01/P2-02: a real headless Chrome renders the dashboard, its task list and live updates.

Skipped only when no Chrome/Chromium is installed. Set HARNESS_DASHBOARD_SCREENSHOT to a PNG
path to also save a 1280x800 screenshot of the active-task page, and
HARNESS_DASHBOARD_SCREENSHOT_DIR to a directory for the task overview at 1280x800 and 390x844.
The live stream is recycled after half a second so the page goes quiet for --dump-dom.
"""

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from harness.dashboard.server import DashboardHandler, DashboardServer
from harness.db import connect
from tests.test_dashboard import cli, needs_git, serve, start_task
from tests.test_dashboard_overview import _investigated, _propose, _resume

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
VIEWPORTS = ((1280, 800), (390, 844))


@pytest.fixture(autouse=True)
def short_streams(monkeypatch):
    monkeypatch.setattr(DashboardServer, "stream_lifetime", 0.5)


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


def _dom(server: DashboardServer, profile: Path, query: str = "") -> str:
    result = _headless(profile, "--dump-dom", f"{server.url}{query}")
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


def _created(db_path: Path, task_id: str, created_at: str) -> None:
    conn = connect(db_path)
    try:
        conn.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (created_at, task_id))
    finally:
        conn.close()


def _screenshots(server: DashboardServer, profile: Path) -> None:
    directory = os.environ.get("HARNESS_DASHBOARD_SCREENSHOT_DIR")
    if not directory:
        return
    Path(directory).mkdir(parents=True, exist_ok=True)
    for width, height in VIEWPORTS:
        shot = Path(directory) / f"overview-{width}x{height}.png"
        result = _headless(
            profile / f"{width}", f"--window-size={width},{height}", f"--screenshot={shot}",
            server.url,
        )  # fmt: skip
        assert result.returncode == 0 and shot.is_file(), result.stderr


@needs_git
def test_headless_chrome_shows_the_overview_and_switches_tasks(db_path, sample_repo, tmp_path):
    task_id = _investigated(db_path, sample_repo)
    other = start_task(db_path, "other", "task")
    _propose(db_path, task_id)
    since = (datetime.now(UTC) - timedelta(days=2, hours=3, minutes=30)).isoformat(
        timespec="seconds"
    )
    _created(db_path, task_id, since)
    resume = _resume(db_path, task_id)
    with serve(db_path, task_id) as server:
        dom = _dom(server, tmp_path / "profile")
        switched = _dom(server, tmp_path / "profile-b", f"?task={other}")
        unknown = _dom(server, tmp_path / "profile-c", "?task=t-00000000")
        _screenshots(server, tmp_path / "profile-shots")
    assert f"<h2>Task {task_id}</h2>" in dom and "<dd>csv export</dd>" in dom
    assert f'<time datetime="{since}" data-since="{since}">2d 03h</time>' in dom
    assert "<dd>awaiting approval</dd>" in dom and "<dd>proposal.created at " in dom
    assert "<p>P1 · interview recommendation: Export access</p>" in dom
    assert f"<code>harness interview decide {task_id} 1 accept|reject|modify</code>" in dom
    assert f"<p>next: <code>{resume['next']}</code></p>" in dom
    assert f'<a href="/?task={task_id}" aria-current="page">{task_id}</a>' in dom
    assert f'<a href="/?task={other}">{other}</a>' in dom
    assert f"<p>next: <code>harness investigate {other}</code></p>" in dom

    assert f"<h2>Task {other}</h2>" in switched and "<dd>investigation</dd>" in switched
    assert f'<a href="/?task={other}" aria-current="page">{other}</a>' in switched
    assert f'<a href="/?task={task_id}">{task_id}</a>' in switched

    assert "<p>unknown task: t-00000000</p>" in unknown and "<h2>Task " not in unknown
    assert f'<a href="/?task={task_id}">' in unknown and f'<a href="/?task={other}">' in unknown
    assert "aria-current" not in unknown


def test_headless_chrome_updates_live_from_the_stream(db_path, tmp_path, monkeypatch):
    task_id = start_task(db_path, "live", "update")
    original, seen = DashboardHandler._stream, []

    def change_then_stream(handler: DashboardHandler, selected: str | None) -> None:
        if not seen:
            seen.append(handler.server.task_id)
            cli(db_path, "phase", task_id, "next")
        original(handler, selected)

    monkeypatch.setattr(DashboardHandler, "_stream", change_then_stream)
    with serve(db_path, task_id) as server:
        dom = _dom(server, tmp_path / "profile")
    assert seen == [task_id]
    assert f"<h2>Task {task_id}</h2>" in dom
    assert "<dd>interview</dd>" in dom and "<dd>investigation</dd>" not in dom
    assert "<p>interview · in progress</p>" in dom
