"""P4-06 browser acceptance at real desktop and mobile CDP viewports."""

import os

import pytest

from harness.execution_projection import ingest_event
from tests.execution_helpers import approved_execution, event, started
from tests.test_dashboard import serve
from tests.test_dashboard_delivery import _delivery_row
from tests.test_dashboard_execution_browser import CHROME, _cdp_capture

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not installed"),
]


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_delivery_panel_fits_desktop_and_mobile_viewports(
    controller, sample_repo, monkeypatch, db_path, tmp_path, provider
):
    monkeypatch.setenv(
        "HARNESS_DASHBOARD_SCREENSHOT_DIR",
        os.environ.get("HARNESS_DASHBOARD_SCREENSHOT_DIR") or str(tmp_path / "screenshots"),
    )
    task_id, execution_id = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, execution_id)
    ingest_event(
        controller.conn,
        execution_id,
        event(
            controller.conn,
            execution_id,
            2,
            "task.registered",
            {
                "id": "delivery-task",
                "title": "Delivery UI task",
                "status": "Idle",
                "dependencies": [],
                "files": ["harness/dashboard"],
                "criteria": ["P4-06"],
                "revision": 1,
            },
        ),
    )
    ingest_event(
        controller.conn,
        execution_id,
        event(
            controller.conn,
            execution_id,
            3,
            "blocker.raised",
            {
                "id": "delivery-blocker",
                "revision": 1,
                "tasks": ["delivery-task"],
                "affected": ["delivery-task"],
                "question": "Review the synthetic delivery fixture",
                "context": "Browser test setup",
                "alternatives": ["continue", "pause"],
                "recommendation": "continue",
            },
        ),
    )
    _delivery_row(controller.conn, execution_id, provider)

    with serve(db_path, task_id) as server:
        server.stream_lifetime = 30
        desktop = _cdp_capture(
            server,
            tmp_path / "delivery-desktop",
            provider + "-delivery-1280x800.png",
            1280,
            800,
            task_id,
            delivery=True,
        )
        mobile = _cdp_capture(
            server,
            tmp_path / "delivery-mobile",
            provider + "-delivery-390x844.png",
            390,
            844,
            task_id,
            delivery=True,
        )

    for metrics, width in ((desktop, 1280), (mobile, 390)):
        assert metrics["viewport_width"] == width
        assert metrics["global_horizontal_overflow"] is False
        assert metrics["stream_open"] is True
        assert metrics["dashboard_requests"] <= 2
        assert metrics["screenshots"]
        panel = metrics["delivery"]
        assert panel["focusedLink"] and panel["right"] <= width
        assert panel["unsafeNodes"] == panel["unsafeLinks"] == 0
        for content in (
            "#14" if provider == "github" else "!14",
            "success",
            "Stagingready",
            "12 tests passed" if provider == "github" else "11 passed",
            "one approval remains",
            "Phase 5",
        ):
            assert content in panel["text"]
