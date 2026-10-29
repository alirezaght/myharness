"""Actual work is visible before approval history, distinct from verified completion."""

import base64
import copy
import os
from pathlib import Path

import pytest

from harness.dashboard import server as server_module
from tests.execution_helpers import approved_execution, started
from tests.test_dashboard import api, serve
from tests.test_dashboard_browser import CHROME
from tests.test_dashboard_live_view_state_browser import chrome, evaluate, wait

pytestmark = [pytest.mark.browser, pytest.mark.skipif(CHROME is None, reason="Chrome unavailable")]


@pytest.mark.parametrize("width,height", [(1280, 800), (390, 844)])
def test_work_is_visible_without_fake_percentages(
    controller, sample_repo, monkeypatch, db_path, tmp_path, width, height
):
    task, execution = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, execution)
    with serve(db_path, task) as server:
        baseline = api(server, f"?task={task}")
        state = baseline["task"]["execution"]
        state.update(
            status="Running",
            error=None,
            agents=[
                {
                    "id": "engineer",
                    "name": "Feature Engineer",
                    "role": "al-engineer",
                    "status": "Running",
                    "activity": "Implementing signed provider callbacks",
                }
            ],
            progress={"completed": 0, "total": 1, "unit": "tasks"},
            activity={
                "recent_actions": [
                    {
                        "action": "Updating source",
                        "agent_name": "Feature Engineer",
                        "at": "2026-01-01T00:00:00+00:00",
                    }
                ],
                "last_activity_at": "2026-01-01T00:00:00+00:00",
                "finished_agent_runs": 2,
                "report_count": 3,
                "checks": [{"id": "E2", "area": "provider callbacks", "status": "RUNNING"}],
            },
        )
        monkeypatch.setattr(
            server_module, "dashboard_view", lambda *a, **kw: copy.deepcopy(baseline)
        )
        with chrome(tmp_path / "chrome", server.url + f"?task={task}", width, height) as call:
            wait(call, "Boolean(document.querySelector('.execution-current-work'))")
            text = evaluate(call, "document.querySelector('.execution-current-work').textContent")
            assert "Implementing signed provider callbacks" in text
            assert "Updating source" in text and "Feature Engineer" in text
            assert "2 agent runs finished · 3 reports recorded" in text
            assert "Reported running" in text and "No newer work update" in text
            assert "Progress: unknown" not in evaluate(
                call, "document.querySelector('#execution').textContent"
            )
            assert evaluate(
                call, "document.querySelector('.execution-progress').textContent"
            ).startswith("Verified completion")
            assert "not the amount of work performed" in evaluate(
                call, "document.querySelector('.execution-progress').textContent"
            )
            assert evaluate(
                call,
                "Boolean(document.querySelector('#execution').compareDocumentPosition("
                "document.querySelector('#approvals')) & Node.DOCUMENT_POSITION_FOLLOWING)",
            )
            assert not evaluate(call, "document.documentElement.scrollWidth > innerWidth")
            directory = Path(
                os.environ.get("HARNESS_DASHBOARD_SCREENSHOT_DIR", tmp_path / "screenshots")
            )
            directory.mkdir(parents=True, exist_ok=True)
            image = call("Page.captureScreenshot", {"format": "png"})
            (directory / f"work-progress-{width}.png").write_bytes(base64.b64decode(image["data"]))
            state["status"] = "Interrupted"
            evaluate(call, "refreshDashboard()")
            wait(
                call,
                "document.querySelector('.execution-work-now h5').textContent"
                " === 'Last reported work'",
            )
            assert "Execution is not active" in evaluate(
                call, "document.querySelector('.execution-work-now').textContent"
            )
