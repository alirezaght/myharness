"""Operational debt stays separate from decisions; stale epoch UI cannot leak forward."""

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
def test_epoch_status_and_stale_actions(
    controller, sample_repo, monkeypatch, db_path, tmp_path, width, height
):
    task, execution = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, execution)
    with serve(db_path, task) as server:
        baseline = api(server, f"?task={task}")
        state = baseline["task"]["execution"]
        state.update(
            epoch_id="epoch-1",
            authority_generation=1,
            implementation={"status": "continuing", "issue_refs": ["discovery"]},
            verification={
                "status": "incomplete",
                "obligation_refs": ["checks/" + "discovery" * 30],
            },
            developer_decisions=[],
            operational_issues={
                "discovery": {
                    "id": "discovery",
                    "state": "Repairing",
                    "capability_or_check_ref": "Service/check discovery",
                    "next_action": "Inspect the relevant manifests and rerun discovery.",
                }
            },
            available_history_epochs=[{"epoch_id": "legacy-0", "status": "Archived"}],
            authored_work={
                "kind": "authored_work_recorded_verification_pending",
                "message": "Scoped source work is recorded; verification remains pending.",
                "source_file_count": 2,
            },
            status="Running",
            error=None,
            status_explanation=None,
            readiness={"actions": {"resume": True, "reconnect": True}},
            blockers=[],
            decisions=[],
        )
        monkeypatch.setattr(
            server_module, "dashboard_view", lambda *a, **kw: copy.deepcopy(baseline)
        )
        with chrome(tmp_path / "chrome", server.url + f"?task={task}", width, height) as call:
            wait(call, "Boolean(document.querySelector('.execution-verification h4'))")
            evaluate(call, "source.close()")
            assert (
                evaluate(call, "document.querySelector('#approvals h3').textContent")
                == "Specification approved"
            )
            assert evaluate(call, "document.querySelector('#approve-button').disabled")
            assert not evaluate(
                call, "document.querySelector('#approvals').textContent.includes('or run:')"
            )
            assert not evaluate(
                call, "document.querySelector('#approvals').textContent.includes('Final spec')"
            )
            assert evaluate(call, "document.querySelector('.execution-decision-form') === null")
            assert evaluate(
                call, "document.querySelector('.authored-work-notice').textContent"
            ) == ("Scoped source work is recorded; verification remains pending.")
            assert evaluate(
                call,
                "document.querySelector('#current-status').textContent"
                ".includes('Implementation continuing')",
            )
            assert evaluate(
                call,
                "document.querySelector('.execution-verification').textContent"
                ".includes('Verification incomplete')",
            )
            assert evaluate(
                call,
                "!document.querySelector('#current-status').textContent.includes('Why blocked')",
            )
            evaluate(
                call,
                "document.querySelector('[data-view-disclosure=verification-obligations]').open=true",
            )
            directory = Path(
                os.environ.get("HARNESS_DASHBOARD_SCREENSHOT_DIR", tmp_path / "screenshots")
            )
            directory.mkdir(parents=True, exist_ok=True)
            screenshot = call("Page.captureScreenshot", {"format": "png"})
            (directory / f"overview-{width}x{height}.png").write_bytes(
                base64.b64decode(screenshot["data"])
            )
            evaluate(call, "document.querySelector('.execution-verification').scrollIntoView()")
            screenshot = call("Page.captureScreenshot", {"format": "png"})
            (directory / f"pending-checks-{width}x{height}.png").write_bytes(
                base64.b64decode(screenshot["data"])
            )
            assert not evaluate(call, "document.documentElement.scrollWidth > innerWidth")
            assert evaluate(
                call,
                "[...document.querySelectorAll('#current-status article')].every(node => {"
                "const r=node.getBoundingClientRect();return r.left>=0 && r.right<=innerWidth})",
            )
            evaluate(
                call,
                """
                window.live=structuredClone(executionSnapshot);
                live.blockers=[{id:'choice',revision:1,state:'Open',kind:'specification',
                  tasks:[],question:'Choose the visible behavior',alternatives:[]}];
                live.developer_decisions=live.blockers;
                document.querySelector('#execution').replaceWith(renderExecution(executionTaskId,live));
                const input=document.querySelector('#execution-decision-text');
                input.value='My old epoch answer';input.dispatchEvent(new Event('input'));
                input.focus();input.setSelectionRange(2,5);
                window.realFetch=fetch;
                window.fetch=(url,options)=>String(url)==='/api/execution/reconnect'
                  ? new Promise(resolve=>{window.posted=JSON.parse(options.body);
                      window.finishOld=()=>resolve(new Response(
                        JSON.stringify({error:'old epoch failure'}),
                        {status:409,headers:{'Content-Type':'application/json'}}));})
                  :realFetch(url,options);
                document.querySelector('.execution-actions button').click();
                true;
                """,
            )
            wait(call, "Boolean(window.posted)")
            assert evaluate(call, "posted.epoch_id") == "epoch-1"
            assert evaluate(call, "posted.authority_generation") == 1
            evaluate(
                call,
                """
                const next=structuredClone(live);next.epoch_id='epoch-2';
                next.authority_generation=2;
                const old=document.querySelector('#execution');
                withExecutionFocus(()=>old.replaceWith(renderExecution(executionTaskId,next)));
                finishOld();
                true;
                """,
            )
            evaluate(
                call,
                "new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))",
            )
            assert evaluate(call, "document.querySelector('#execution-decision-text').value") == ""
            assert evaluate(call, "document.activeElement.id") != "execution-decision-text"
            assert not evaluate(
                call,
                "document.querySelector('#execution').textContent.includes('old epoch failure')",
            )
            assert (
                evaluate(call, "document.querySelector('.execution-action-results').textContent")
                == ""
            )
