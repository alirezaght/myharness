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
            native_id="01a17dbb-6ec4-7de3-9e5e-fc8a37f4779a",
            error=None,
            phase={"phase": "IMPLEMENTATION"},
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
            assert evaluate(
                call,
                "document.querySelector('.execution-diagram').getBoundingClientRect().height > 0",
            )
            assert (
                evaluate(
                    call,
                    "document.querySelector('[data-agent-id=engineer] .agent-map-badge')"
                    ".textContent",
                )
                == "Working"
            )
            assert "Implementing signed provider callbacks" in evaluate(
                call, "document.querySelector('.execution-diagram').textContent"
            )
            assert "Supervisor" in evaluate(
                call, "document.querySelector('.execution-diagram').textContent"
            )
            assert (
                evaluate(
                    call,
                    "document.querySelector('.agent-map-stages [aria-current=step]').textContent",
                )
                == "Build"
            )
            assert not evaluate(
                call, "document.querySelector('[data-view-disclosure=execution-full-details]').open"
            )
            evaluate(
                call,
                "document.querySelector('[data-view-disclosure=execution-full-details]').open=true",
            )
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
            # Stage navigation and check details are read-only, persistent and keyboard usable.
            state["activity"]["checks"] = [
                {"id": f"E{i}", "area": "email attribution", "status": "INFRA"} for i in range(1, 7)
            ]
            state["operational_issues"] = {
                "runtime": {
                    "id": "runtime",
                    "state": "Open",
                    "capability_or_check_ref": "E1 locked setup",
                    "next_action": "Install the locked dependencies for E1",
                },
                "other": {
                    "id": "other",
                    "state": "Open",
                    "capability_or_check_ref": "E10 unrelated cause",
                    "next_action": "Repair the E10 prerequisite",
                },
            }
            evaluate(call, "refreshDashboard()")
            wait(
                call,
                "document.querySelector('[aria-label=\"Show check details\"]')"
                ".textContent.includes('6 need attention')",
            )
            evaluate(call, "document.querySelector('[aria-label=\"Show check details\"]').click()")
            assert (
                evaluate(
                    call, "document.querySelector('[role=tab][aria-selected=true]').textContent"
                )
                == "Check"
            )
            assert (
                evaluate(call, "document.querySelectorAll('.agent-map-check-result').length") == 6
            )
            assert "E10 unrelated cause" not in evaluate(
                call, "document.querySelector('.agent-map-check-result').textContent"
            )
            evaluate(call, "document.querySelector('.agent-map-check-result summary').click()")
            evaluate(call, "refreshDashboard()")
            wait(
                call,
                "document.querySelector('[role=tab][aria-selected=true]').textContent === 'Check'",
            )
            assert evaluate(call, "document.querySelector('.agent-map-check-result details').open")
            evaluate(
                call,
                "document.querySelector('[role=tab][aria-selected=true]').dispatchEvent("
                "new KeyboardEvent('keydown', {key:'ArrowRight',bubbles:true}))",
            )
            assert evaluate(call, "document.activeElement.textContent") == "Review"
            assert "Formal review is not established" in evaluate(
                call, "document.querySelector('[role=tabpanel]').textContent"
            )
            evaluate(call, "document.querySelector('#execution-stage-1').click()")
            assert "Build" in evaluate(
                call, "document.querySelector('[role=tabpanel]').textContent"
            )
            controls = evaluate(
                call, "document.querySelector('.execution-run-controls').textContent"
            )
            assert all(
                label in controls
                for label in ("Continue task", "Retry checks", "Refresh recorded status")
            )
            assert evaluate(
                call, "!document.querySelector('.execution-run-controls').closest('details')"
            )
            directory = Path(
                os.environ.get("HARNESS_DASHBOARD_SCREENSHOT_DIR", tmp_path / "screenshots")
            )
            directory.mkdir(parents=True, exist_ok=True)
            image = call("Page.captureScreenshot", {"format": "png"})
            (directory / f"work-progress-{width}.png").write_bytes(base64.b64decode(image["data"]))
            state["status"] = "Interrupted"
            state["phase"] = {"phase": "DONE"}
            evaluate(call, "refreshDashboard()")
            wait(
                call,
                "document.querySelector('.execution-work-now h5').textContent"
                " === 'Last reported work'",
            )
            assert "Execution is not active" in evaluate(
                call, "document.querySelector('.execution-work-now').textContent"
            )
            assert evaluate(
                call, "document.querySelector('.agent-map-stages [aria-current=step]') === null"
            )
            assert (
                evaluate(
                    call,
                    "document.querySelector('[data-agent-id=engineer] .agent-map-badge')"
                    ".textContent",
                )
                == "Last reported work"
            )
            state["status"] = "Running"
            state["agents"][0]["status"] = "Completed"
            evaluate(call, "refreshDashboard()")
            wait(
                call,
                "document.querySelector('.agent-map-heading p').textContent"
                " === 'Supervisor coordinating'",
            )
            assert "None active" in evaluate(
                call, "document.querySelector('.agent-map-branches').textContent"
            )
            assert "1 finished agent runs" in evaluate(
                call, "document.querySelector('.agent-map-finished').textContent"
            )
            evaluate(call, "document.querySelector('.agent-map-finished summary').click()")
            assert evaluate(call, "document.querySelector('.agent-map-finished').open")
