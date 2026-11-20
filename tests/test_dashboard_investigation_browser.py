"""Real Chrome investigation-first, candidate adoption and exact review flow."""

import json
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest

from harness.dashboard.server import DashboardServer
from tests.investigation_fixture import InvestigationModelFixture
from tests.test_dashboard_authoring_browser import CHROME, _capture, _http_json
from tests.test_dashboard_interview_recovery_browser import click_text, enter, set_theme
from tests.test_dashboard_live_view_state_browser import chrome, evaluate, wait

pytest_plugins = ("tests.investigation_fixture",)

pytestmark = [pytest.mark.browser, pytest.mark.skipif(CHROME is None, reason="Chrome unavailable")]
ARTIFACTS = Path(tempfile.gettempdir()) / "harness-interview-recovery-artifacts" / "outputs"


@pytest.mark.parametrize(
    "width,theme", [(1280, "light"), (1280, "dark"), (390, "light"), (390, "dark")]
)
def test_chrome_investigation_first_to_explicit_adoption_and_source_review(
    db_path, tmp_path, monkeypatch, width, theme, native_fixture
):
    """Use the real dashboard HTTP flow and native fixture, never a DOM-only task injection."""
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "README.md").write_text("CSV exports preserve existing behavior.\n")
    (repo / "export.py").write_text("def export_csv(accounts):\n    return accounts\n")

    from harness.authoring_worker import AuthoringWorker

    monkeypatch.setattr(
        "harness.authoring_worker.AuthoringWorker",
        lambda path, root, **kwargs: AuthoringWorker(
            path, root, provider_factory=InvestigationModelFixture, **kwargs
        ),
    )

    def profile(host):
        return {
            "host": host,
            "available": True,
            "status": "synthetic fixture",
            "version": "fixture",
        }

    monkeypatch.setattr("harness.authoring_policy.availability", profile)
    monkeypatch.setattr("harness.dashboard.authoring_view.availability", profile)
    server = DashboardServer(db_path, None, project_root=repo)
    server.poll_seconds = 0.1
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    task_id = None
    capture_id = uuid.uuid4().hex[:10]
    try:
        with chrome(tmp_path / "chrome-investigation", server.url, width, 900) as call:
            wait(call, 'Boolean(document.querySelector("#project-start textarea"))')
            set_theme(call, theme)
            enter(
                call,
                '#project-start textarea[aria-label="What would you like to build?"]',
                "AR-2906: Build a CSV export for accounts and preserve existing exports.",
            )
            click_text(call, "Start task and interview")
            wait(call, 'new URLSearchParams(location.search).has("task")')
            task_id = evaluate(call, 'new URLSearchParams(location.search).get("task")')
            assert task_id

            deadline = time.monotonic() + 20
            snapshot = None
            while time.monotonic() < deadline:
                code, snapshot = _http_json(server, f"/api/authoring?task={task_id}")
                assert code == 200
                source_jobs = [
                    job for job in snapshot["jobs"] if job["kind"] == "source_investigation"
                ]
                if source_jobs and source_jobs[-1]["state"] not in {"queued", "running"}:
                    break
                time.sleep(0.05)
            assert source_jobs and source_jobs[-1]["state"] == "succeeded", (
                source_jobs[-1] if source_jobs else snapshot
            )
            assert snapshot["investigation"]["stage"] == "ready"
            assert {source["kind"] for source in snapshot["investigation"]["sources"]} == {
                "ticket",
                "repository",
            }
            assert snapshot["questions"] and snapshot["interview"]["current_question_id"]

            wait(call, 'document.body.innerText.includes("Source review is ready")')
            wait(call, 'document.body.innerText.includes("What Harness found")')
            wait(call, 'document.body.innerText.includes("Developer decision status")')
            wait(
                call,
                (
                    f'document.body.innerText.includes("Current decision '
                    f'Q{snapshot["interview"]["current_question_id"]}:")'
                ),
            )
            evaluate(
                call,
                "(()=>{const progress=[...document.querySelectorAll('.investigati"
                "on-panel details')]"
                ".find(item=>item.querySelector('summary')?.textContent.includes("
                "'Repository inspection progress'));"
                "if(progress)progress.open=true;return Boolean(progress);})()",
            )
            assert evaluate(call, 'document.body.innerText.includes("bounded file reads")')
            assert evaluate(call, "document.documentElement.scrollWidth<=innerWidth+1")
            _capture(call, ARTIFACTS / f"investigation-interview-{width}-{theme}-{capture_id}.png")

            click_text(call, "Open current decision")
            wait(call, 'Boolean(document.querySelector("dialog.authoring-drawer[open]"))')
            decision_id = snapshot["interview"]["current_question_id"]
            answer_selector = f'dialog[open] textarea[aria-label="Answer question {decision_id}"]'
            enter(call, answer_selector, "Include archived accounts in the export.")
            click_text(call, "Save answer", 'document.querySelector("dialog[open]")')
            wait(call, f'document.body.innerText.includes("Answer for Q{decision_id} saved")')
            answer_snapshot = _http_json(server, f"/api/authoring?task={task_id}")[1]
            assert (
                next(row for row in answer_snapshot["questions"] if row["id"] == decision_id)[
                    "answer"
                ]
                == "Include archived accounts in the export."
            )
            wait(call, 'document.body.innerText.includes("Answered decision history")')
            assert not evaluate(
                call, f'document.body.innerText.includes("Current decision Q{decision_id}:")'
            )
            assert not evaluate(
                call,
                'Boolean([...document.querySelectorAll("button")].find(button=>bu'
                'tton.textContent==="Open current decision"))',
            )
            click_text(call, "Close interview")

            candidates = [
                row
                for row in answer_snapshot["revisions"]
                if row["attribution"] == "model_proposal"
            ]
            assert candidates, "source pipeline did not persist its specification candidate"
            candidate = candidates[-1]
            wait(call, 'Boolean(document.querySelector("#workspace-tab-diagram"))')
            click_text(call, "Diagram")
            wait(call, 'document.body.innerText.includes("Proposed logical system design")')
            wait(call, 'Boolean(document.querySelector(".workspace-canvas svg .workspace-node"))')
            click_text(call, "Fit diagram")
            evaluate(
                call,
                'document.querySelector(".workspace-canvas").scrollIntoView({block:"start"});true',
            )
            assert evaluate(call, "document.documentElement.scrollWidth<=innerWidth+1")
            _capture(call, ARTIFACTS / f"investigation-diagram-{width}-{theme}-{capture_id}.png")

            click_text(call, "Review")
            wait(
                call, "Boolean(document.querySelector('select[aria-label=\"Candidate revision\"]'))"
            )
            evaluate(
                call,
                "(()=>{const select=document.querySelector('select[aria-label=\"Ca"
                "ndidate revision\"]');"
                + "select.value="
                + json.dumps(candidate["id"])
                + ";select.dispatchEvent(new Event('change',{bubbles:true}));return true;})()",
            )
            click_text(call, "Load candidate diff")
            wait(
                call, 'document.querySelector(".candidate-diff")?.innerText.includes("CSV export")'
            )
            candidate_detail = _http_json(
                server, f"/api/authoring/revision?task={task_id}&revision={candidate['id']}"
            )[1]
            assert "CSV export" in candidate_detail["body"]
            checkboxes = evaluate(
                call,
                "[...document.querySelectorAll('.authoring-reconcile > label inpu"
                "t[type=checkbox]')].length",
            )
            assert checkboxes == 2
            evaluate(
                call,
                "[...document.querySelectorAll('.authoring-reconcile > label inpu"
                "t[type=checkbox]')].forEach(box=>box.click());true",
            )
            click_text(call, "Preview explicit adoption")
            wait(call, 'Boolean(document.querySelector(".reconcile-preview button"))')
            click_text(
                call,
                "Adopt this revision and request review",
                'document.querySelector(".reconcile-preview")',
            )
            wait(call, 'document.body.innerText.includes("Revision adopted")')
            adopted = _http_json(server, f"/api/authoring?task={task_id}")[1]
            adopted_revision = adopted["adopted"]["id"]
            adopted_detail = _http_json(
                server, f"/api/authoring/revision?task={task_id}&revision={adopted_revision}"
            )[1]
            assert adopted_revision != candidate["id"]
            assert adopted_detail["parent_id"] == candidate["id"]
            assert adopted["adopted"]["body"] == candidate_detail["body"]
            assert adopted["adopted"]["graph"]["value"] == candidate_detail["graph"]["value"]

            click_text(call, "Spec")
            wait(call, 'document.querySelector(".spec-editor")?.value.includes("CSV export")')
            assert evaluate(
                call, 'document.querySelector(".spec-preview").innerText.includes("CSV export")'
            )
            evaluate(
                call, 'document.querySelector(".spec-editor").scrollIntoView({block:"start"});true'
            )
            _capture(call, ARTIFACTS / f"investigation-spec-{width}-{theme}-{capture_id}.png")

            click_text(call, "Interview")
            evaluate(
                call,
                "(()=>{window.__investigationReviewHttp=null;window.__investigationFetches=[];"
                "const original=window.fetch;window.fetch=async(...args)=>{"
                "const response=await original(...args);const url=String(args[0]);"
                "if(url.includes('/api/')){const body=await response.clone().json"
                "().catch(()=>({}));"
                "window.__investigationFetches.push({url,status:response.status,k"
                "eys:Object.keys(body),"
                "hasReview:Boolean(body.review),requestId:args[1]?.body?JSON.pars"
                "e(args[1].body).request_id:null});"
                "if(url.includes('/investigation-review'))window.__investigationReviewHttp={url,"
                "status:response.status,request:JSON.parse(args[1]?.body||'{}'),body};}"
                "return response;};return true;})()",
            )
            click_text(call, "Load exact investigation review")
            wait(call, "Boolean(window.__investigationReviewHttp)")
            review_http = evaluate(call, "window.__investigationReviewHttp")
            assert review_http["status"] == 200, review_http
            assert review_http["body"].get("review", {}).get("content", {}).get("complete") is True
            try:
                wait(
                    call,
                    'document.querySelector(".authoring-action-message")?.innerText.i'
                    'ncludes("Loaded review package")',
                )
            except BaseException as error:
                review_debug = evaluate(
                    call,
                    "(()=>{const state=workspaceStates.get(new URLSearchParams(locati"
                    "on.search).get('task'));"
                    "return {fetches:window.__investigationFetches,tab:state?.tab,"
                    "message:state?.authoringMessage,review:Boolean(state?.investigationReview),"
                    "requests:state?.authoringRequests,dom:document.querySelector('.a"
                    "uthoring-action-message')?.innerText};})()",
                )
                raise AssertionError({"wait_error": str(error), **review_debug}) from error
            wait(call, 'document.body.innerText.includes("Exact investigation review package")')
            wait(
                call,
                'document.body.innerText.includes("Actual requirement") || docume'
                'nt.body.innerText.includes("CSV export")',
            )
            package_state = evaluate(
                call,
                "workspaceStates.get(new URLSearchParams(location.search).get('ta"
                "sk')).investigationReview",
            )
            assert package_state and package_state["content"]["complete"]
            assert package_state["content"]["adopted_revision"] == adopted_revision
            assert package_state["content"]["graph"]["value"] == candidate_detail["graph"]["value"]
            assert (
                package_state["content"]["sources"]
                and package_state["content"]["evidence"]["coverage"]
            )
            assert isinstance(package_state["content"]["effective_lineage"], dict)
            assert evaluate(
                call,
                'document.body.innerText.includes("Review basis and decision line'
                'age · 0 recorded links")',
            )
            assert evaluate(call, "document.documentElement.scrollWidth<=innerWidth+1")
            evaluate(
                call,
                "(()=>{const node=[...document.querySelectorAll('.investigation-r"
                "eview-package details')]"
                ".find(item=>item.querySelector('summary')?.textContent.includes("
                "'Review basis and decision lineage'));"
                "if(node)node.open=true;return Boolean(node);})()",
            )
            evaluate(
                call,
                'document.querySelector(".investigation-review-package").scrollIn'
                'toView({block:"start"});true',
            )
            _capture(call, ARTIFACTS / f"investigation-review-{width}-{theme}-{capture_id}.png")

            evaluate(
                call,
                "document.querySelector('.investigation-controls label input[type"
                "=checkbox]')?.click();true",
            )
            evaluate(
                call,
                'document.querySelector(".investigation-controls button")?.scroll'
                'IntoView({block:"center"});true',
            )
            assert evaluate(
                call, 'document.querySelector(".investigation-controls button")?.disabled === false'
            )
            assert evaluate(
                call,
                'document.querySelector("[aria-label=\\"Optional source review acc'
                'eptance note\\"]")?.value === ""',
            )
            _capture(call, ARTIFACTS / f"investigation-acceptance-{width}-{theme}-{capture_id}.png")
            click_text(call, "Accept this exact source review")
            wait(call, 'document.body.innerText.includes("Exact source coverage was accepted")')
            accepted = _http_json(server, f"/api/authoring?task={task_id}")[1]
            assert accepted["investigation"]["coverage"]["accepted"]
            assert accepted["approved"] is None and accepted["execution"] is None

            original_bundle = accepted["investigation"]["bundle"]
            original_adopted = accepted["adopted"]["id"]
            (repo / "README.md").write_text(
                "CSV exports preserve existing behavior. Source refresh version two.\n"
            )
            click_text(call, "Refresh ticket and repository sources")
            deadline = time.monotonic() + 20
            refreshed = None
            refreshed_job = None
            while time.monotonic() < deadline:
                code, refreshed = _http_json(server, f"/api/authoring?task={task_id}")
                assert code == 200
                source_jobs = [
                    job for job in refreshed["jobs"] if job["kind"] == "source_investigation"
                ]
                if source_jobs and source_jobs[-1]["id"] != snapshot["investigation"]["job"]:
                    refreshed_job = source_jobs[-1]
                    if refreshed_job["state"] not in {"queued", "running"}:
                        break
                time.sleep(0.05)
            assert refreshed_job and refreshed_job["state"] == "succeeded", refreshed_job
            assert refreshed["investigation"]["bundle"] != original_bundle
            assert refreshed["adopted"]["id"] == original_adopted
            old_repository_source = next(
                source
                for source in snapshot["investigation"]["sources"]
                if source["kind"] == "repository"
            )
            new_repository_source = next(
                source
                for source in refreshed["investigation"]["sources"]
                if source["kind"] == "repository"
            )
            assert new_repository_source["content"] != old_repository_source["content"]
            wait(call, 'document.body.innerText.includes("This review package is stale;")')
            old_graph = package_state["content"]["graph"]["value"]
            assert evaluate(
                call,
                "(()=>{const diagram=[...document.querySelectorAll('.investigatio"
                "n-review-package details')]"
                ".find(item=>item.querySelector('summary')?.textContent.includes("
                "'Proposed logical diagram'));"
                "if(diagram)diagram.open=true;return Boolean(diagram);})()",
            )
            for node in old_graph["nodes"]:
                assert evaluate(
                    call,
                    "document.querySelector('.investigation-review-package').innerText.includes("
                    + json.dumps(node["label"])
                    + ")",
                )
            _capture(
                call, ARTIFACTS / f"investigation-stale-review-{width}-{theme}-{capture_id}.png"
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        assert not thread.is_alive()


@pytest.mark.parametrize("width,theme", [(1280, "light"), (390, "dark")])
def test_chrome_jira_reconnect_is_explicit_and_handoff_stays_ephemeral(
    controller, db_path, tmp_path, monkeypatch, width, theme, native_fixture
):
    """Reconnect and authorization navigation require an explicit user action."""
    from harness.authoring_jobs import claim_next
    from harness.authoring_worker import AuthoringWorker
    from harness.investigation_policy import HANDOFFS

    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "README.md").write_text("CSV export behavior.\n")

    def profile(host):
        return {
            "host": host,
            "available": True,
            "status": "synthetic fixture",
            "version": "fixture",
        }

    monkeypatch.setattr(AuthoringWorker, "start", lambda self: False)
    monkeypatch.setattr("harness.authoring_policy.availability", profile)
    monkeypatch.setattr("harness.dashboard.authoring_view.availability", profile)
    server = DashboardServer(db_path, None, project_root=repo)
    server.poll_seconds = 0.1
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    task_id = None
    claim = None
    authorization_url = "https://auth.atlassian.com/authorize?synthetic=ephemeral-reconnect"
    try:
        with chrome(tmp_path / "chrome-reconnect", server.url, width, 900) as call:
            wait(call, 'Boolean(document.querySelector("#project-start textarea"))')
            set_theme(call, theme)
            enter(
                call,
                '#project-start textarea[aria-label="What would you like to build?"]',
                "AR-2906: Build a CSV export and preserve existing behavior.",
            )
            click_text(call, "Start task and interview")
            wait(call, 'new URLSearchParams(location.search).has("task")')
            task_id = evaluate(call, 'new URLSearchParams(location.search).get("task")')
            assert task_id
            queued = _http_json(server, f"/api/authoring?task={task_id}")[1]
            source_job = next(
                job for job in queued["jobs"] if job["kind"] == "source_investigation"
            )
            assert source_job["state"] == "queued"
            assert queued["investigation"]["reconnect"]["available"]
            assert native_fixture[2]() == []

            before_reload = evaluate(call, "performance.timeOrigin")
            call("Page.reload")
            wait(call, f"performance.timeOrigin !== {before_reload}")
            wait(call, 'Boolean(document.querySelector("#workspace-tab-interview"))')
            assert native_fixture[2]() == []

            click_text(call, "Cancel source investigation")
            wait(
                call,
                "document.body.innerText.includes('Cancelled') || document.body.i"
                "nnerText.includes('cancelled')",
            )
            cancelled_source = _http_json(server, f"/api/authoring?task={task_id}")[1]
            source_job = next(
                job for job in cancelled_source["jobs"] if job["id"] == source_job["id"]
            )
            assert source_job["state"] == "cancelled"
            assert native_fixture[2]() == []

            draft = "Keep this unsent follow-up through reconnect and reload."
            enter(call, '[aria-label="Follow-up message"]', draft)
            assert evaluate(
                call,
                "(()=>{const summary=[...document.querySelectorAll('.investigation-panel summary')]"
                ".find(item=>item.textContent.includes('Reconnect Jira using expl"
                "icit native consent'));"
                "if(summary&&!summary.parentElement.open)summary.click();return B"
                "oolean(summary?.parentElement.open);})()",
            )
            evaluate(
                call,
                "(()=>{const original=window.fetch.bind(window);window.__authorin"
                "gCalls=[];window.__fetchLog=[];window.__reconnectClicks=[];"
                "document.addEventListener('click',event=>{const button=event.tar"
                "get.closest('button');"
                "if(button?.textContent==='Start Jira reconnect')window.__reconne"
                "ctClicks.push({connected:button.isConnected,disabled:button.disa"
                "bled});},true);"
                "window.fetch=async(...args)=>{const response=await original(...args);"
                "window.__fetchLog.push(String(args[0]));"
                "if(String(args[0]).includes('/api/authoring/reconnect-jira')){"
                "let body=null;try{body=await response.clone().json()}catch{}"
                "window.__authoringCalls.push({url:String(args[0]),status:response.status,body});}"
                "return response};return true})()",
            )
            click_text(call, "Start Jira reconnect")
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if evaluate(
                    call,
                    "window.__authoringCalls?.length===1 || document.querySelector('."
                    "authoring-error')",
                ):
                    break
                time.sleep(0.05)
            reconnect_diagnostic = evaluate(
                call,
                "(()=>({calls:window.__authoringCalls,fetches:window.__fetchLog,"
                "clicks:window.__reconnectClicks,details:[...document.querySelect"
                "orAll('.investigation-panel details')].map(d=>({open:d.open,text"
                ":d.querySelector('summary')?.textContent})),"
                "message:[...workspaceStates.values()].map(s=>s.authoringMessage),"
                "button:[...document.querySelectorAll('button')].find(b=>b.textCo"
                "ntent==='Start Jira reconnect')"
                "?.disabled,body:document.body.innerText.slice(-1500)}))()",
            )
            assert reconnect_diagnostic["calls"] or reconnect_diagnostic["message"], (
                reconnect_diagnostic
            )
            reconnect_call = evaluate(call, "window.__authoringCalls?.[0]||null")
            assert reconnect_call and reconnect_call["status"] == 200, reconnect_diagnostic
            wait(call, "document.body.innerText.includes('Request queued')")
            reconnect_state = _http_json(server, f"/api/authoring?task={task_id}")[1]
            reconnect_job = next(
                job for job in reconnect_state["jobs"] if job["kind"] == "jira_reconnect"
            )
            assert reconnect_job["state"] == "queued"
            assert (
                evaluate(
                    call, 'document.querySelector("[aria-label=\\"Follow-up message\\"]")?.value'
                )
                == draft
            )
            assert native_fixture[2]() == []
            _capture(
                call, ARTIFACTS / f"reconnect-controls-{width}-{theme}-{uuid.uuid4().hex[:10]}.png"
            )

            before_reload = evaluate(call, "performance.timeOrigin")
            call("Page.reload")
            wait(call, f"performance.timeOrigin !== {before_reload}")
            wait(call, 'Boolean(document.querySelector("#workspace-tab-interview"))')
            assert (
                evaluate(
                    call, 'document.querySelector("[aria-label=\\"Follow-up message\\"]")?.value'
                )
                == draft
            )
            assert native_fixture[2]() == []

            claim = claim_next(
                controller.conn, "browser-reconnect-fixture", reconnect_state["project"]["id"]
            )
            assert (
                claim and claim["id"] == reconnect_job["id"] and claim["kind"] == "jira_reconnect"
            )
            HANDOFFS.publish(
                claim, {"authorization_url": authorization_url, "login_id": "fixture-login"}
            )
            evaluate(call, "refreshDashboard().then(()=>true)")
            wait(
                call,
                'Boolean([...document.querySelectorAll("button")].find(button=>bu'
                'tton.textContent==="Open Jira authorization page"))',
            )
            assert evaluate(
                call,
                "(()=>{const summary=[...document.querySelectorAll('.investigation-panel summary')]"
                ".find(item=>item.textContent.includes('Reconnect Jira using expl"
                "icit native consent'));"
                "if(summary&&!summary.parentElement.open)summary.click();return B"
                "oolean(summary?.parentElement.open);})()",
            )
            wait(
                call,
                'Boolean([...document.querySelectorAll("button")].find(button=>bu'
                'tton.textContent==="Open Jira authorization page"))',
            )
            evaluate(
                call,
                "(()=>{window.__fixtureOpenedUrls=[];window.__fixturePopupCloses=0;"
                "window.open=()=>({location:{replace:value=>window.__fixtureOpene"
                "dUrls.push(value)},"
                "close:()=>window.__fixturePopupCloses++});return true;})()",
            )
            click_text(call, "Open Jira authorization page")
            wait(call, f"window.__fixtureOpenedUrls?.[0]==={json.dumps(authorization_url)}")
            persistence = evaluate(
                call,
                "(()=>{const storage=Object.keys(localStorage).map(key=>localStor"
                "age.getItem(key)||'').join(' ');"
                "return {url:location.href,storage,history:JSON.stringify(history.state||{}),"
                "opened:window.__fixtureOpenedUrls};})()",
            )
            assert persistence["opened"] == [authorization_url]
            assert authorization_url not in persistence["url"]
            assert authorization_url not in persistence["storage"]
            assert authorization_url not in persistence["history"]
            assert native_fixture[2]() == []

            HANDOFFS.discard(claim)
            evaluate(call, "window.__fixtureOpenedUrls=[];true")
            click_text(call, "Open Jira authorization page")
            wait(
                call,
                'document.querySelector(".authoring-error")?.innerText.includes("'
                'no longer available")',
            )
            assert evaluate(
                call, "window.__fixtureOpenedUrls.length===0 && window.__fixturePopupCloses===1"
            )
            assert native_fixture[2]() == []

            evaluate(
                call,
                "(()=>{window.__cancelJobPosts=[];const original=window.fetch.bind(window);"
                "window.fetch=async(...args)=>{const response=await original(...args);"
                "if(String(args[0]).endsWith('/cancel-job'))"
                "window.__cancelJobPosts.push({request:JSON.parse(args[1].body),"
                "status:response.status});return response;};return true;})()",
            )
            click_text(call, "Cancel source investigation")
            wait(call, "window.__cancelJobPosts.length===1")
            cancel_post = evaluate(call, "window.__cancelJobPosts[0]")
            assert cancel_post["status"] == 200, cancel_post
            assert cancel_post["request"]["job"] == reconnect_job["id"], cancel_post
            deadline = time.monotonic() + 10
            cancelled_reconnect = None
            while time.monotonic() < deadline:
                cancelled_reconnect = _http_json(server, f"/api/authoring?task={task_id}")[1]
                reconnect_job = next(
                    job for job in cancelled_reconnect["jobs"] if job["id"] == reconnect_job["id"]
                )
                if reconnect_job["state"] == "cancelled":
                    break
                time.sleep(0.05)
            assert reconnect_job["state"] == "cancelled"
            assert native_fixture[2]() == []
            before_reload = evaluate(call, "performance.timeOrigin")
            call("Page.reload")
            wait(call, f"performance.timeOrigin !== {before_reload}")
            wait(call, 'Boolean(document.querySelector("#workspace-tab-interview"))')
            assert authorization_url not in evaluate(call, "location.href")
            assert authorization_url not in evaluate(
                call, "Object.keys(localStorage).map(key=>localStorage.getItem(key)||'').join(' ')"
            )
            assert native_fixture[2]() == []
            assert (
                evaluate(
                    call, 'document.querySelector("[aria-label=\\"Follow-up message\\"]")?.value'
                )
                == draft
            )
            assert evaluate(call, "document.documentElement.scrollWidth<=innerWidth+1")
    finally:
        if claim:
            HANDOFFS.discard(claim)
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        assert not thread.is_alive()
