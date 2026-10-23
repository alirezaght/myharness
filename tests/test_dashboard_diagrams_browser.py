"""P2-04: a real headless Chrome renders the diagrams as SVG with addressable, selectable elements.

Skipped only when no Chrome/Chromium is installed. Set HARNESS_DASHBOARD_SCREENSHOT_DIR to a
directory to also save the diagrams view (diagrams-1280w.png, diagrams-390w.png).
"""

import os
from pathlib import Path
from urllib.parse import quote

import pytest

from harness.dashboard.server import DashboardServer
from tests.sample_design import TYPES
from tests.test_dashboard import api, serve
from tests.test_dashboard_browser import CHROME, _dom, _headless
from tests.test_dashboard_diagrams import _sample, _state

pytestmark = [pytest.mark.browser, pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not installed")]
SELECTED = "component:web-dashboard"


@pytest.fixture(autouse=True)
def short_streams(monkeypatch):
    monkeypatch.setattr(DashboardServer, "stream_lifetime", 0.5)


def _items(server: DashboardServer) -> list[dict]:
    current = api(server)["task"]["diagrams"]["current"]
    return [item for scene in current["diagrams"] for item in scene["items"]]


def _shots(server: DashboardServer, profile: Path, fragment: str) -> None:
    directory = os.environ.get("HARNESS_DASHBOARD_SCREENSHOT_DIR")
    if not directory:
        return
    Path(directory).mkdir(parents=True, exist_ok=True)
    for width, height in ((1280, 6000), (390, 12000)):
        shot = Path(directory) / f"diagrams-{width}w.png"
        result = _headless(
            profile / f"{width}", f"--window-size={width},{height}", f"--screenshot={shot}",
            f"{server.url}{fragment}",
        )  # fmt: skip
        assert result.returncode == 0 and shot.is_file(), result.stderr


def test_headless_chrome_renders_every_diagram_with_addressable_elements(db_path, tmp_path):
    task_id = _sample(db_path)
    _state(db_path, task_id, "component <b>x</b>: <img src=x onerror=alert(1)>")
    fragment = f"#element={quote(SELECTED, safe='')}"
    with serve(db_path, task_id) as server:
        items = _items(server)
        dom = _dom(server, tmp_path / "profile", fragment)
        plain = _dom(server, tmp_path / "profile-b")
        _shots(server, tmp_path / "profile-shots", fragment)
    for kind in TYPES:
        assert f'<figure class="diagram" data-diagram="{kind}">' in dom
    assert dom.count('<g class="diagram-element') == len(items)
    for item in items:
        assert f'data-element-id="{item["id"].replace(">", "&gt;")}"' in dom
    assert 'data-element-id="component:b-x-b"' in dom
    assert "&lt;b&gt;x&lt;/b&gt;" in dom and "<b>x</b>" not in dom and "<img" not in dom
    pressed = [item for item in items if item["id"] == SELECTED]
    assert len(pressed) == 2 and dom.count('aria-pressed="true"') == len(pressed)
    assert f'data-element-id="{SELECTED}" data-kind="component" tabindex="0" role="button"' in dom
    assert '<div id="diagram-selection" aria-live="polite"><h4>Selected element</h4>' in dom
    assert f"<dt>id</dt><dd>{SELECTED}</dd>" in dom and "<dd>version 1</dd>" in dom
    assert '<p class="diagram-status">showing: version 1 (current)</p>' in dom
    assert 'aria-pressed="true"' not in plain
    assert "<p>select an element to see its details</p>" in plain
