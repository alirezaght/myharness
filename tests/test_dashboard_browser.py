"""P2-01..P2-03: a real headless Chrome renders the dashboard, task list, live updates and spec.

Skipped only when no Chrome/Chromium is installed. Set HARNESS_DASHBOARD_SCREENSHOT to a PNG
path to also save a 1280x800 screenshot of the active-task page, and
HARNESS_DASHBOARD_SCREENSHOT_DIR to a directory for the task overview at 1280x800 and 390x844
and the spec & decisions view (spec-1280w.png, spec-390w.png).
The live stream is recycled after half a second so the page goes quiet for --dump-dom.
"""

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from harness.dashboard.server import DashboardServer
from harness.db import connect
from tests.test_dashboard import needs_git, serve, start_task
from tests.test_dashboard_overview import _investigated, _propose, _resume
from tests.test_dashboard_spec import _spec_text, _versions, _write

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
pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not installed"),
]
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


SPEC_BODY = "## Requirements\n\n- **Rows:** keep `csv` <img src=x onerror=alert(1)>\n  wrapped\n"


def _spec_shots(server: DashboardServer, profile: Path) -> None:
    directory = os.environ.get("HARNESS_DASHBOARD_SCREENSHOT_DIR")
    if not directory:
        return
    Path(directory).mkdir(parents=True, exist_ok=True)
    for width, height in ((1280, 2400), (390, 4000)):
        shot = Path(directory) / f"spec-{width}w.png"
        result = _headless(
            profile / f"{width}", f"--window-size={width},{height}", f"--screenshot={shot}",
            server.url,
        )  # fmt: skip
        assert result.returncode == 0 and shot.is_file(), result.stderr


@needs_git
def test_headless_chrome_renders_the_spec_history_and_decisions(db_path, sample_repo, tmp_path):
    task_id = _investigated(db_path, sample_repo)
    _propose(db_path, task_id)
    specs = tmp_path / "specs"
    _versions(specs, task_id, SPEC_BODY, SPEC_BODY + "- added line\n")
    _write(specs, task_id, "draft.md", _spec_text(task_id, "draft", None, SPEC_BODY, 3))
    with serve(db_path, task_id, str(specs)) as server:
        dom = _dom(server, tmp_path / "profile")
        _spec_shots(server, tmp_path / "profile-shots")
    assert '<section id="spec"><h3>Specification</h3><p>showing: v2 (approved)</p>' in dom
    assert '<article id="spec-document"><h4>Specification — csv export</h4>' in dom
    assert "<h5>Requirements</h5>" in dom and "<strong>Task:</strong>" in dom
    assert "<strong>Rows:</strong> keep <code>csv</code> &lt;img src=x" in dom
    assert "onerror=alert(1)&gt;<br>wrapped</li>" in dom and "<img" not in dom
    assert "<li><p>v1 superseded (superseded by v2) · 2026-01-01T00:00:00+00:00" in dom
    assert "<p>· first version</p>" in dom
    assert f'<details data-key="{task_id} v2"><summary>· +1 -0 against v1</summary>' in dom
    assert '<span data-kind="add">+- added line</span>' in dom
    assert '<span data-kind="hunk">@@ ' in dom
    assert f'<details data-key="{task_id} draft"><summary>· +0 -1 against v2</summary>' in dom
    assert '<span data-kind="remove">-- added line</span>' in dom
    assert '<section id="approved-decisions"><h4>Approved decisions (1)</h4>' in dom
    assert "<p>DEC-0001 v1 — Storage engine</p>" in dom and "DEC-0002" not in dom
    assert "<p>scope: Persistence · 2026-01-01</p>" in dom
    assert '<section id="open-decisions"><h4>Open decisions (' in dom
    assert "awaiting_decision: Who may export?</p>" in dom
    assert "<p>recommendation: P1 Export access: Admins only</p>" in dom
    assert f"<code>harness interview decide {task_id} 1 accept|reject|modify</code>" in dom
