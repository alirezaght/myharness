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
        with chrome(
            tmp_path / "chrome",
            server.url + f"?task={tid}&workspace=run&tab=changes",
            width,
            height,
        ) as call:
            wait(call, "Boolean(document.querySelector('.change-illustration svg'))")
            assert "Signed callback changes" in evaluate(
                call, "document.querySelector('.change-illustration').textContent"
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
                    (
                        "document.querySelector('[data-change-element=\\\"flow:noti"
                        "fication-service->warehouse\\\"]').dataset.inferred"
                    ),
                )
                == "true"
            )
            target_rect = evaluate(
                call,
                "(()=>{const target=[...document.querySelectorAll("
                "'.workspace-panel .change-illustration [data-change-element]')]"
                ".find(node=>node.dataset.changeElement==='flow:notification-service->warehouse');"
                "const copies=[...document.querySelectorAll('[data-change-element]')]"
                ".filter(node=>node.dataset.changeElement==='flow:notification-service->warehouse');"
                "target.scrollIntoView({block:'center',inline:'center'});"
                "let rect=target.getBoundingClientRect();"
                "if(rect.top<0||rect.bottom>innerHeight)window.scrollTo(window.scrollX,"
                "window.scrollY+rect.top-innerHeight/2);"
                "rect=target.getBoundingClientRect();return {x:rect.left+rect.width/2,"
                "y:rect.top+rect.height/2,copies:copies.length,"
                "insideClosedDetails:Boolean(target.closest('details:not([open])'))}"
                "})()",
            )
            assert target_rect["copies"] == 1 and not target_rect["insideClosedDetails"], (
                target_rect
            )
            focus_before_refresh = evaluate(
                call,
                "(()=>{const target=[...document.querySelectorAll("
                "'.workspace-panel .change-illustration [data-change-element]')]"
                ".find(node=>node.dataset.changeElement==='flow:notification-service->warehouse');"
                "target.focus({preventScroll:true});"
                "return {active:document.activeElement===target,"
                "key:document.activeElement.getAttribute('data-change-element'),"
                "tabIndex:target.tabIndex};})()",
            )
            assert focus_before_refresh["active"], focus_before_refresh
            evaluate(
                call,
                "window.diagramRefreshCompleted=false;"
                "refreshDashboard().then(()=>window.diagramRefreshCompleted=true);true",
            )
            wait(call, "window.diagramRefreshCompleted===true")
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
            directory = Path(
                os.environ.get("HARNESS_DASHBOARD_SCREENSHOT_DIR", tmp_path / "screenshots")
            )
            directory.mkdir(parents=True, exist_ok=True)
            image = call("Page.captureScreenshot", {"format": "png"})
            (directory / f"logical-change-{width}.png").write_bytes(base64.b64decode(image["data"]))
            assert evaluate(
                call, "Boolean(document.querySelector('#workspace-panel .change-illustration svg'))"
            )
            evaluate(
                call,
                ("document.querySelector('#workspace-tab-requirements').click()"),
            )
            wait(
                call,
                (
                    "document.querySelector('#workspace-tab-requirements')?.g"
                    "etAttribute('aria-selected')==='true'"
                ),
            )
            assert evaluate(
                call,
                "Boolean(document.querySelector('#workspace-panel .workspace-source-card'))",
            )
            evaluate(call, "document.querySelector('#workspace-tab-spec').click()")
            wait(
                call,
                "document.querySelector('#workspace-tab-spec')?.getAttribute('aria-selected')==='true'",
            )
            assert evaluate(call, "Boolean(document.querySelector('.spec-editor'))")
    assert get_execution(controller.conn, eid) == execution
