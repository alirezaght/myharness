"""P4-06 browser acceptance at real desktop and mobile CDP viewports."""

import json
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
    controller.conn.execute(
        "INSERT INTO browser_plans(id,execution_id,document,created_at) VALUES (?,?,?,?)",
        (
            "phase5-plan-fixture",
            execution_id,
            json.dumps(
                {
                    "target": {
                        "url": "https://staging.example.test/very/long/path/" + "segment" * 40,
                        "deployment_id": "deploy-" + "a" * 80,
                    },
                    "criteria": [
                        {
                            "id": "Q24",
                            "text": "<img src=x onerror=alert(1)> " + "long-assertion-" * 30,
                            "classification": "selected",
                            "reason": "Fixture only; never indicates live acceptance",
                            "steps": [{"op": "assert_visible", "selector": "h1"}],
                        }
                    ],
                    "limitations": ["Synthetic display fixture; no browser run"],
                }
            ),
            "2026-10-26T09:30:00Z",
        ),
    )

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
            "Completion summary",
            "selected",
            "Q24",
            "separate explicit approval",
            "Synthetic display fixture",
            "<img src=x onerror=alert(1)>",
        ):
            assert content in panel["text"]


HOSTILE = '<img src=x onerror="window.__pwned=1">'


def test_hostile_delivery_text_and_urls_stay_inert_and_issue_only_reads(
    controller, sample_repo, monkeypatch, db_path, tmp_path
):
    from urllib.parse import quote

    from tests.chrome_cdp import chrome_page

    task_id, execution_id = approved_execution(controller, sample_repo, monkeypatch)
    _delivery_row(controller.conn, execution_id)
    mr, snapshot = (
        json.loads(value)
        for value in controller.conn.execute(
            "SELECT mr, snapshot FROM deliveries WHERE execution_id=?", (execution_id,)
        ).fetchone()
    )
    mr["web_url"] = "javascript:window.__pwned=1"
    snapshot["pipeline"]["web_url"] = "https://user:secret@ci.example.test/pipelines/5801"
    snapshot["blockers"] = [HOSTILE + " blocker"]
    controller.conn.execute(
        "UPDATE deliveries SET mr=?, snapshot=? WHERE execution_id=?",
        (json.dumps(mr), json.dumps(snapshot), execution_id),
    )
    controller.conn.commit()
    before_runs = controller.conn.execute("SELECT COUNT(*) FROM browser_runs").fetchone()[0]

    with serve(db_path, task_id) as server, chrome_page(tmp_path / "hostile") as page:
        page.open(
            f"{server.url}?task={quote(task_id)}&workspace=run&tab=delivery",
            "Boolean(document.querySelector('#delivery'))",
        )
        # Open every disclosure the operator can open; none may trigger a request.
        page.evaluate(
            "for (const s of document.querySelectorAll('#delivery summary')) s.click(); true"
        )
        page.settle()
        panel = page.evaluate(
            """(() => {
                const panel = document.querySelector('#delivery');
                return {
                    text: panel.textContent,
                    markup: panel.querySelectorAll('img,script,iframe,object,embed').length,
                    hrefs: [...panel.querySelectorAll('a')].map(a => [a.href, a.rel]),
                    plainLabels: [...panel.querySelectorAll('span')].map(e => e.textContent)
                        .filter(t => t.startsWith('Open ')),
                    closed: panel.querySelectorAll('details:not([open])').length,
                    pwned: window.__pwned === undefined ? null : window.__pwned,
                };
            })()"""
        )
        requests = list(page.requests)

    assert HOSTILE + " blocker" in panel["text"] and panel["markup"] == 0
    assert panel["pwned"] is None and panel["closed"] == 0
    assert "Browser validation" in panel["text"] and "Investigation records" in panel["text"]
    # A script URL and a credential-bearing URL are both refused: their labels stay
    # plain text and no anchor exists. Safe https links rendering as anchors with
    # noopener is proven by test_delivery_panel_fits_desktop_and_mobile_viewports.
    assert panel["hrefs"] == []
    assert {"Open review", "Open pipeline", "Open merge request"} <= set(panel["plainLabels"])
    origin = server.url.rstrip("/")
    assert requests and all(r["method"] == "GET" for r in requests), requests
    assert all(r["url"].startswith(origin) for r in requests), requests
    after_runs = controller.conn.execute("SELECT COUNT(*) FROM browser_runs").fetchone()[0]
    assert before_runs == after_runs == 0
