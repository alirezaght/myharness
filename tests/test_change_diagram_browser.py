"""Logical flow diagrams remain useful while the dashboard updates live."""

import base64
import os
from pathlib import Path

import pytest

from harness.change_diagrams import publish
from harness.execution_store import get_execution
from tests.test_change_diagrams import approved_with_prose, content
from tests.test_dashboard import serve
from tests.test_dashboard_browser import CHROME
from tests.test_dashboard_live_view_state_browser import chrome, evaluate, wait

pytestmark = [pytest.mark.browser, pytest.mark.skipif(CHROME is None, reason="Chrome unavailable")]


@pytest.mark.parametrize("width,height", [(1280, 800), (390, 844)])
def test_logical_change_flow_has_sources_and_survives_live_refresh(
    controller, sample_repo, monkeypatch, db_path, tmp_path, width, height
):
    tid, eid, refs = approved_with_prose(controller, sample_repo, monkeypatch)
    execution = get_execution(controller.conn, eid)
    publish(controller, execution, content(controller, execution, refs))
    with serve(db_path, tid) as server:
        with chrome(tmp_path / "chrome", server.url + f"?task={tid}", width, height) as call:
            wait(call, "Boolean(document.querySelector('.change-illustration svg'))")
            assert "Signed callback changes" in evaluate(
                call, "document.querySelector('#diagrams').textContent"
            )
            assert evaluate(
                call,
                "[...document.querySelectorAll('.change-illustration .edge-label')]"
                ".every(node => /^\\d+$/.test(node.textContent))",
            )
            assert "daily import" in evaluate(
                call, "document.querySelector('.change-flow-key').textContent"
            )
            evaluate(
                call,
                "document.querySelector('[data-change-element=\\\"flow:notification-service->warehouse\\\"]')"
                ".dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))",
            )
            assert "inferred relationship" in evaluate(
                call, "document.querySelector('.change-illustration-detail').textContent"
            )
            assert refs[1] in evaluate(
                call, "document.querySelector('.change-illustration-detail').textContent"
            )
            assert (
                evaluate(
                    call,
                    "document.querySelector('[data-change-element=\\\"flow:notification-service->warehouse\\\"]').dataset.inferred",
                )
                == "true"
            )
            evaluate(
                call,
                "document.querySelector('[data-change-element=\\\"flow:notification-service->warehouse\\\"]').focus({preventScroll:true});refreshDashboard()",
            )
            wait(
                call,
                "document.querySelector('.change-illustration-detail')"
                ".textContent.includes('inferred relationship')",
            )
            assert (
                evaluate(call, "document.activeElement.getAttribute('data-change-element')")
                == "flow:notification-service->warehouse"
            )
            assert not evaluate(call, "document.documentElement.scrollWidth > innerWidth")
            assert evaluate(
                call,
                "Boolean(document.querySelector('#diagrams')"
                ".compareDocumentPosition(document.querySelector('#spec'))"
                " & Node.DOCUMENT_POSITION_FOLLOWING)",
            )
            directory = Path(
                os.environ.get("HARNESS_DASHBOARD_SCREENSHOT_DIR", tmp_path / "screenshots")
            )
            directory.mkdir(parents=True, exist_ok=True)
            image = call("Page.captureScreenshot", {"format": "png"})
            (directory / f"logical-change-{width}.png").write_bytes(base64.b64decode(image["data"]))
    assert get_execution(controller.conn, eid) == execution
