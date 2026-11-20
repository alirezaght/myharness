"""Real Chrome coverage of the task-scoped Interview & Spec HTTP flow."""

import base64
import http.client
import json
import os
import time
import uuid
from pathlib import Path

import pytest

from harness.authoring_worker import AuthoringWorker
from harness.browser_driver import chrome_executable
from harness.dashboard.server import DashboardServer
from tests.test_dashboard_live_view_state_browser import chrome, evaluate, wait

try:
    CHROME = chrome_executable()
except OSError:
    CHROME = None

pytestmark = [pytest.mark.browser, pytest.mark.skipif(CHROME is None, reason="Chrome unavailable")]


@pytest.fixture(autouse=True)
def available_fixture_host(monkeypatch):
    def profile(host):
        return {"host": host, "available": True, "status": "fixture", "version": "fixture"}

    monkeypatch.setattr("harness.authoring_policy.availability", profile)
    monkeypatch.setattr("harness.dashboard.authoring_view.availability", profile)


def _model_output(*, include_question):
    graph = {
        "nodes": [
            {
                "id": "actor",
                "label": "Developer",
                "kind": "actor",
                "status": "proposed",
                "sources": ["request"],
                "assumption": "Proposed from task request",
            },
            {
                "id": "service",
                "label": "Task service",
                "kind": "component",
                "status": "proposed",
                "sources": ["request"],
                "assumption": "Proposed from task request",
            },
        ],
        "edges": [
            {
                "id": "asks",
                "label": "preserves submitted task data after restart and recovery",
                "kind": "relationship",
                "status": "proposed",
                "sources": ["request"],
                "assumption": "Proposed from task request",
                "from": "actor",
                "to": "service",
            }
        ],
    }
    return {
        "assistant": "I recorded the task and prepared a specification candidate.",
        "questions": (
            [
                {
                    "topic": "Expected behavior",
                    "text": "What should happen when the input is empty?",
                    "decision": True,
                    "suggestions": ["Show a validation error"],
                }
            ]
            if include_question
            else []
        ),
        "recommendations": (
            [
                {
                    "question": 0,
                    "title": "Reject empty values",
                    "recommendation": "Show a validation error before saving.",
                    "rationale": "Empty task data cannot be acted on safely.",
                    "impact": "The user can correct the entry without losing other fields.",
                }
            ]
            if include_question
            else []
        ),
        "body": "## Task specification\n\nValidate empty input and preserve submitted task data.\n",
        "planning": [],
        "graph": {"concrete": graph},
    }


class ModelFixture:
    calls = 0

    def __init__(self, host):
        assert host == "codex"

    def run(self, inputs, *, cancelled=lambda: False):
        assert inputs["request"] and not cancelled()
        type(self).calls += 1
        return _model_output(include_question=type(self).calls == 1)


def _capture(call, filename):
    result = call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
    Path(filename).write_bytes(base64.b64decode(result["data"]))


def _set_theme(call, theme):
    evaluate(
        call,
        "(() => {const choice=document.querySelector('#tasks select[aria-label=\"Theme\"]');"
        + "choice.value="
        + json.dumps(theme)
        + ";choice.dispatchEvent(new Event('change',{bubbles:true}));return true;})()",
    )
    wait(call, "document.documentElement.dataset.theme===" + json.dumps(theme))
    expected = "rgb(247, 248, 247)" if theme == "light" else "rgb(21, 26, 23)"
    assert evaluate(call, "getComputedStyle(document.body).backgroundColor") == expected


def _http_json(server, path, *, method="GET", body=None):
    host, port = server.server_address
    connection = http.client.HTTPConnection(host, port, timeout=10)
    headers = {"Host": f"{host}:{port}"}
    if body is not None:
        headers.update({"Origin": f"http://{host}:{port}", "Content-Type": "application/json"})
    connection.request(
        method, path, body=json.dumps(body) if body is not None else None, headers=headers
    )
    response = connection.getresponse()
    payload = json.loads(response.read())
    status = response.status
    connection.close()
    return status, payload


def _capture_authoring_posts(call):
    evaluate(
        call,
        "(() => {if(window.__authoringPostCapture)return true;"
        "window.__authoringPostCapture=true;window.__authoringPosts=[];"
        "const prior=window.fetch;window.fetch=async(...args)=>{"
        "const response=await prior(...args);"
        "if(String(args[0]).includes('/api/authoring/')&&args[1]?.method==='POST')"
        "window.__authoringPosts.push({url:String(args[0]),request:JSON.parse(args[1].body),"
        "status:response.status,body:await response.clone().text()});"
        "return response;};return true;})()",
    )


def _wait_for_succeeded_generation(call, count=1):
    expression = (
        "(async()=>{const id=new URLSearchParams(location.search).get('task');"
        "const value=await(await fetch('/api/authoring?task='+encodeURIComponent(id),"
        "{cache:'no-store'})).json();return value.jobs.filter("
        "row=>row.kind==='generate'&&row.state==='succeeded').length>=" + str(count) + "})()"
    )
    wait(
        call,
        expression,
    )


def _wait_for_authoring_form_baseline(call, task_id, target_id, buffer_key):
    expression = (
        "(async()=>{const task="
        + json.dumps(task_id)
        + ";const targetId="
        + json.dumps(target_id)
        + ";const key="
        + json.dumps(buffer_key)
        + ";const response=await fetch('/api/authoring?task='+encodeURIComponent(task),"
        "{cache:'no-store'});const snapshot=await response.json();"
        "const target=snapshot.revision_targets.find(row=>row.target===targetId);"
        "const storageKey='myharness-authoring:'+snapshot.project.id+':'+task+':'+key;"
        "const stored=JSON.parse(localStorage.getItem(storageKey));"
        "return {ready:Boolean(stored&&target&&!stored.dirty"
        "&&JSON.stringify(stored.base)===JSON.stringify(snapshot.base)"
        "&&stored.token===target.revision),serverBase:snapshot.base,serverToken:target?.revision,"
        "bufferBase:stored?.base,bufferToken:stored?.token,dirty:stored?.dirty,generation:stored?.generation};})()"
    )
    deadline = time.monotonic() + 8
    observed = None
    while time.monotonic() < deadline:
        observed = evaluate(call, expression)
        if observed and observed["ready"]:
            return observed
        time.sleep(0.05)
    pytest.fail(f"authoring form did not converge to the current server base/token: {observed}")


def test_chrome_new_task_interview_spec_candidate_and_explicit_adoption(
    db_path, sample_repo, monkeypatch, tmp_path
):
    ModelFixture.calls = 0
    monkeypatch.setattr(
        "harness.authoring_worker.AuthoringWorker",
        lambda path, root, **kwargs: AuthoringWorker(
            path, root, provider_factory=ModelFixture, **kwargs
        ),
    )
    server = DashboardServer(db_path, None, project_root=sample_repo)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    server.poll_seconds = 0.1
    thread.start()
    try:
        with chrome(tmp_path / "chrome-profile", server.url, 1280, 900) as call:
            artifact_dir = Path(__file__).parents[1] / "../interview-ui-browser-artifacts"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            wait(call, 'Boolean(document.querySelector("#project-start textarea"))')
            _set_theme(call, "light")
            evaluate(
                call,
                (
                    """(() => {
              const request=document.querySelector('#project-start textarea');
"""
                    "              request.value='Build a task capture "
                    "tool that rejects empty input and keeps submitted "
                    "task data.';\n"
                    """              request.dispatchEvent(new Event('input',{bubbles:true}));
"""
                    "              document.querySelector('#project-sta"
                    "rt form').dispatchEvent(new Event('submit',{bubble"
                    "s:true,cancelable:true}));\n"
                    """              return true;
            })()"""
                ),
            )
            wait(call, 'location.search.includes("task=")')
            task_id = evaluate(call, "new URLSearchParams(location.search).get('task')")
            wait(
                call,
                (
                    "(async () => (await (await fetch('/api/aut"
                    "horing?task='+new URLSearchParams(location"
                    ".search).get('task'))).json()).questions.s"
                    "ome(row=>row.text==='What should happen wh"
                    "en the input is empty?'))()"
                ),
            )
            evaluate(call, "document.querySelector('#workspace-tab-interview').click(); true")
            wait(
                call,
                'document.body.innerText.includes("What should happen when the input is empty?")',
            )
            _wait_for_succeeded_generation(call)
            evaluate(
                call,
                (
                    "(()=>{const open=[...document.querySelectorAll('.authoring-"
                    "question')].find(card=>card.textContent.in"
                    "cludes('What should happen when the input "
                    "is empty?')).querySelector('button[aria-ha"
                    "spopup=dialog]'); window.__questionDrawerOpenerKey="
                    "open.dataset.workspaceFocus; open.click(); return true;})()"
                ),
            )
            wait(call, 'document.querySelector(".authoring-drawer")?.open===true')
            wait(
                call,
                "(() => {const dialog=document.querySelector('.authoring-drawer');"
                "return getComputedStyle(dialog).opacity==='1'&&"
                "dialog.getAnimations().every(animation=>animation.playState==='finished');})()",
            )
            _capture(call, artifact_dir / "interview-drawer-light.png")
            opened_question = evaluate(
                call,
                "document.querySelector('.authoring-drawer .authoring-question')?.textContent||''",
            )
            assert "What should happen when the input is empty?" in opened_question
            question_id = evaluate(
                call,
                (
                    "document.querySelector('.authoring-drawer "
                    ".authoring-question')?.dataset.questionId"
                ),
            )
            evaluate(
                call,
                (
                    """(() => {
"""
                    "              const field=document.querySelector('"
                    ".authoring-drawer [aria-label=\"Answer question '+Q"
                    "UESTION_ID+'\"]');\n"
                    """              field.value='Show a validation error and keep the ' +
              'entered values.';
              field.dispatchEvent(new Event('input',{bubbles:true}));
"""
                    "              [...document.querySelectorAll('.auth"
                    "oring-drawer button')].find(node=>node.textContent"
                    "==='Save answer').click();\n"
                    """              return true;
            })()"""
                ).replace("QUESTION_ID", json.dumps(str(question_id))),
            )
            wait(
                call,
                'document.body.innerText.includes("Your recorded answer: Show a validation error")',
            )
            wait(call, 'document.querySelector(".authoring-drawer")?.open===true')
            evaluate(call, "document.querySelector('.authoring-drawer button').click(); true")
            wait(call, 'document.querySelector(".authoring-drawer")?.open!==true')
            assert evaluate(
                call,
                "document.activeElement.dataset.workspaceFocus===window.__questionDrawerOpenerKey",
            )
            evaluate(call, "document.querySelector('#workspace-tab-interview').click(); true")
            revision_selector = f'[aria-label="Revised answer for question {question_id}"]'
            wait(call, f"Boolean(document.querySelector({json.dumps(revision_selector)}))")
            evaluate(
                call,
                "(()=>{window.__answerRevisionPosts=[];const original=window.fetch.bind(window);"
                "window.fetch=async(...args)=>{const response=await original(...args);"
                "if(String(args[0]).includes('/api/authoring/revise'))"
                "window.__answerRevisionPosts.push({request:JSON.parse(args[1].body),"
                "status:response.status,body:await response.clone().json()});"
                "return response;};return true;})()",
            )
            for index, revision in enumerate(
                (
                    "First retained answer revision.",
                    "Second successive answer revision.",
                )
            ):
                evaluate(
                    call,
                    (
                        """(() => {
                  const input=document.querySelector(REVISION_SELECTOR);
                  input.value=REVISION_TEXT; input.dispatchEvent(new Event('input',{bubbles:true}));
"""
                        "                  [...document.querySelectorAll('b"
                        "utton')].find(node=>node.textContent==='Save answe"
                        "r revision to history').click();\n"
                        """                  return true;
                })()"""
                    )
                    .replace("REVISION_SELECTOR", json.dumps(revision_selector))
                    .replace("REVISION_TEXT", json.dumps(revision)),
                )
                wait(call, f"window.__answerRevisionPosts.length==={index + 1}")
                posted = evaluate(call, f"window.__answerRevisionPosts[{index}]")
                assert posted["status"] == 200 and posted["request"]["body"] == revision, posted
                cleanup_condition = (
                    "(()=>{const task=new URLSearchParams(location.search).get('task');"
                    "const state=workspaceStates.get(task);"
                    "const buffer=state?.authoringBuffers?.["
                    f"{json.dumps(f'answer:{question_id}')}];"
                    f"const receipt=window.__answerRevisionPosts[{index}];"
                    "return Boolean(buffer&&!buffer.dirty&&receipt?.body?.base&&"
                    "JSON.stringify(buffer.base)===JSON.stringify(receipt.body.base)&&"
                    "JSON.stringify(state.authoring.base)===JSON.stringify(receipt.body.base)&&"
                    f"!state.authoringRequests?.[{json.dumps(f'answer-revision:{question_id}')}]"
                    ");})()"
                )
                deadline = time.monotonic() + 8
                settled = False
                while time.monotonic() < deadline:
                    if evaluate(call, cleanup_condition):
                        settled = True
                        break
                    time.sleep(0.05)
                if not settled:
                    diagnostic = evaluate(
                        call,
                        "(()=>{const task=new URLSearchParams(location.search).get('task');"
                        "const state=workspaceStates.get(task);"
                        "const buffer=state?.authoringBuffers?.["
                        f"{json.dumps(f'answer:{question_id}')}];"
                        "const storage=localStorage.getItem('myharness-authoring:'"
                        "+state.projectId+':'+task+':answer:'+"
                        f"{json.dumps(str(question_id))});"
                        "return {post:window.__answerRevisionPosts.at(-1),"
                        "buffer:buffer&&structuredClone(buffer),snapshotBase:state?.authoring?.base,"
                        "question:state?.authoring?.questions?.find(row=>row.id==="
                        f"{question_id}),field:document.querySelector({json.dumps(revision_selector)})?.value,"
                        "storage:storage&&JSON.parse(storage),text:document.body.innerText.slice(-1500)};})()",
                    )
                    pytest.fail(f"revision save did not settle to submitted buffer: {diagnostic}")
                wait(call, f"document.body.textContent.includes({json.dumps(revision)})")
            call("Page.navigate", {"url": evaluate(call, "location.href")})
            wait(call, 'document.body.textContent.includes("Second successive answer revision.")')
            wait(call, 'Boolean(document.querySelector("[aria-label=\\"Follow-up message\\"]"))')
            before_followup_jobs = evaluate(
                call,
                (
                    """(async () => {
"""
                    "              const value=await (await fetch('/api"
                    "/authoring?task='+encodeURIComponent(new URLSearch"
                    "Params(location.search).get('task')),{cache:'no-st"
                    "ore'})).json();\n"
                    """              return value.jobs.filter(row=>row.kind==='generate').length;
            })()"""
                ),
            )
            evaluate(
                call,
                (
                    """(() => {
              const followupOriginalFetch=window.fetch;
              window.failFollowupRead=true;
              window.fetch=(url,options)=>{
                if(String(url).startsWith('/api/authoring?')&&window.failFollowupRead){
                  window.failFollowupRead=false;
"""
                    "                  return Promise.resolve({ok:false"
                    ",status:503,json:async()=>({error:'fixture lost re"
                    "fresh after committed followup'})});\n"
                    """                }
                return followupOriginalFetch(url,options);
              };
              const field=document.querySelector('[aria-label="Follow-up message"]');
"""
                    "              field.value='Please include persiste"
                    "nce and the empty-input case in the complete draft"
                    ".';\n"
                    """              field.dispatchEvent(new Event('input',{bubbles:true}));
"""
                    "              [...document.querySelectorAll('butto"
                    "n')].find(node=>node.textContent==='Send to the in"
                    "terview model').click();\n"
                    """              return true;
            })()"""
                ),
            )
            wait(
                call,
                'document.body.innerText.includes("fixture lost refresh after committed followup")',
            )
            evaluate(
                call, "document.querySelector('.authoring-action-message button').click(); true"
            )
            wait(
                call,
                (
                    'document.body.innerText.includes("Please i'
                    "nclude persistence and the empty-input cas"
                    'e")'
                ),
            )
            _wait_for_succeeded_generation(call, before_followup_jobs + 1)
            after_followup = evaluate(
                call,
                (
                    """(async () => {
"""
                    "              const value=await (await fetch('/api"
                    "/authoring?task='+encodeURIComponent(new URLSearch"
                    "Params(location.search).get('task')),{cache:'no-st"
                    "ore'})).json();\n"
                    "              return {followups:value.contribution"
                    "s.filter(row=>row.kind==='followup'),generates:val"
                    "ue.jobs.filter(row=>row.kind==='generate').length}"
                    ";\n"
                    """            })()"""
                ),
            )
            assert len(after_followup["followups"]) == 1
            assert after_followup["generates"] == before_followup_jobs + 1
            evaluate(
                call,
                (
                    """(() => {
"""
                    '              document.querySelector(\'[role="tab"]'
                    '[aria-label="Spec"], #workspace-tab-spec\').click()'
                    ";\n"
                    """              return true;
            })()"""
                ),
            )
            wait(call, 'Boolean(document.querySelector(".spec-editor"))')
            evaluate(call, "refreshDashboard()")
            wait(
                call,
                (
                    "(async()=>{const task=new URLSearchParams(location.search).get('task');"
                    "const response=await fetch('/api/authoring?task='+encodeURIComponent(task),"
                    "{cache:'no-store'});const current=await response.json();"
                    "const state=workspaceStates.get(task);"
                    "const buffer=state?.authoringBuffers?.['spec:working'];"
                    "return Boolean(buffer&&!buffer.dirty&&current.base&&"
                    "current.jobs.every(job=>!['queued','running'].includes(job.state))&&"
                    "JSON.stringify(current.base)===JSON.stringify(state.authoring.base)&&"
                    "JSON.stringify(current.base)===JSON.stringify(buffer.base));})()"
                ),
            )
            _capture_authoring_posts(call)
            evaluate(
                call,
                (
                    """(() => {
              const editor=document.querySelector('[aria-label="Full working specification"]');
              editor.focus();
"""
                    "              const task=new URLSearchParams(location.search).get('task');"
                    "window.__specBaseAtFirstEdit=structuredClone(workspaceStates.get(task)"
                    ".authoringBuffers['spec:working'].base);\n"
                    "              editor.value='## Complete captured-t"
                    "ask specification\\n\\nThe service rejects empty inp"
                    "ut and preserves submitted values.\\n';\n"
                    """              editor.dispatchEvent(new Event('input',{bubbles:true}));
                  window.savedEditorText=editor.value;
                  const priorSpecFetch=window.fetch;
              window.fetch=async(url,options)=>{
                if(String(url).includes('/api/authoring/save-spec')&&!window.specSaveHeld){
                  const response=await priorSpecFetch(url,options);
                  window.specSaveHeld=true;
                  return await new Promise(resolve=>window.releaseSpecSave=()=>resolve(response));
                }
                return priorSpecFetch(url,options);
              };
"""
                    "              [...document.querySelectorAll('butto"
                    "n')].find(node=>node.textContent==='Save working s"
                    "pecification').click();\n"
                    """              return true;
            })()"""
                ),
            )
            original_spec_base = evaluate(call, "window.__specBaseAtFirstEdit")
            assert evaluate(
                call,
                (
                    "(() => { const editor=document.querySelect"
                    "or('[aria-label=\"Full working specificatio"
                    "n\"]'); return document.activeElement===edi"
                    "tor && document.querySelector('.spec-previ"
                    "ew').textContent.includes('Complete captur"
                    "ed-task specification'); })()"
                ),
            )
            evaluate(
                call,
                "document.querySelector('[aria-label=\"Full working specification\"]')"
                ".scrollIntoView({block:'center'}); true",
            )
            _capture(call, artifact_dir / "spec-editor-light.png")
            wait(call, "window.specSaveHeld===true")
            held_save = evaluate(
                call,
                "window.__authoringPosts.filter(item=>item.url.includes('/save-spec')).at(0)",
            )
            assert (
                held_save["status"] == 200
                and held_save["request"]["expected"] == original_spec_base
            ), held_save
            wait(
                call,
                (
                    "(async()=>{const task=new URLSearchParams(location.search).get('task');"
                    "const response=await fetch('/api/authoring?task='+encodeURIComponent(task),"
                    "{cache:'no-store'});const data=await response.json();"
                    "return data.working?.body===window.savedEditorText;})()"
                ),
            )
            evaluate(
                call,
                (
                    """(() => {
              const editor=document.querySelector('[aria-label="Full working specification"]');
"""
                    "              editor.value=window.savedEditorText+"
                    "'\\nA newer edit typed while the prior save was pen"
                    "ding.\\n';\n"
                    """              editor.dispatchEvent(new Event('input',{bubbles:true}));
              return true;
            })()"""
                ),
            )
            evaluate(call, "window.releaseSpecSave(); true")
            wait(
                call,
                (
                    'document.body.innerText.includes("Working '
                    'specification saved") || Boolean(document.'
                    'querySelector(".authoring-conflict-recover'
                    'y"))'
                ),
            )
            first_spec_request = evaluate(
                call,
                "window.__authoringPosts.filter(item=>item.url.includes('/save-spec'))"
                ".at(0)?.request",
            )
            assert first_spec_request["expected"] == original_spec_base, {
                "captured_at_first_edit": original_spec_base,
                "first_request": first_spec_request,
            }
            if evaluate(call, 'Boolean(document.querySelector(".authoring-conflict-recovery"))'):
                evaluate(
                    call,
                    (
                        "[...document.querySelectorAll('button')].f"
                        "ind(node=>node.textContent==='Refresh late"
                        "st state and keep my text').click(); true"
                    ),
                )
                wait(call, 'document.querySelector(".authoring-conflict-recovery")===null')
                assert evaluate(
                    call,
                    (
                        "document.querySelector('[aria-label=\"Full "
                        "working specification\"]').value.includes('"
                        "newer edit typed while the prior save was "
                        "pending')"
                    ),
                )
                evaluate(
                    call,
                    (
                        "[...document.querySelectorAll('button')].f"
                        "ind(node=>node.textContent==='Save working"
                        " specification').click(); true"
                    ),
                )
                wait(
                    call,
                    "window.__authoringPosts.filter(row=>row.url.includes('/save-spec')).at(-1)"
                    "?.status===200",
                )
            else:
                assert evaluate(
                    call,
                    "document.querySelector('[aria-label=\"Full working specification\"]')"
                    ".value.includes('newer edit typed while the prior save was pending')",
                ), (
                    "the successful first save must preserve text typed while its request "
                    "was pending"
                )
                assert evaluate(
                    call,
                    "![...document.querySelectorAll('button')].find(node=>"
                    "node.textContent==='Save working specification').disabled",
                ), "the newer local edit must remain saveable after the first save completes"
                evaluate(
                    call,
                    "[...document.querySelectorAll('button')].find(node=>"
                    "node.textContent==='Save working specification').click(); true",
                )
                wait(
                    call,
                    "window.__authoringPosts.filter(row=>row.url.includes('/save-spec')).at(-1)"
                    "?.status===409",
                )
                stale_spec_save = evaluate(
                    call,
                    "(() => {const state=workspaceStates.get(" + json.dumps(task_id) + ");"
                    "const row=window.__authoringPosts.filter(item=>"
                    "item.url.includes('/save-spec'))"
                    ".at(-1);const buffer=state.authoringBuffers['spec:working'];"
                    "return {request:row?.request,status:row?.status,body:row?.body,"
                    "bufferBase:buffer.base,serverBase:state.authoring.base,dirty:buffer.dirty,"
                    "value:buffer.value,originalBase:" + json.dumps(original_spec_base) + ","
                    "posts:window.__authoringPosts.filter(item=>item.url.includes('/save-spec'))};})()",
                )
                assert stale_spec_save["status"] == 409, stale_spec_save
                assert stale_spec_save["request"]["expected"] == original_spec_base, stale_spec_save
                assert "authoring source or revision changed" in stale_spec_save["body"]
                assert stale_spec_save["bufferBase"] == original_spec_base
                assert stale_spec_save["serverBase"] != original_spec_base
                assert stale_spec_save["dirty"] and (
                    "newer edit typed while the prior save was pending" in stale_spec_save["value"]
                ), stale_spec_save
                evaluate(
                    call,
                    "[...document.querySelectorAll('button')].find(node=>"
                    "node.textContent==='Keep my text and save against the current revision')"
                    ".click();true",
                )
                wait(
                    call,
                    "JSON.stringify(workspaceStates.get(" + json.dumps(task_id) + ")"
                    ".authoringBuffers['spec:working'].base)===JSON.stringify(workspaceStates.get("
                    + json.dumps(task_id)
                    + ").authoring.base)",
                )
                rebased_spec_state = evaluate(
                    call,
                    "(() => {const state=workspaceStates.get(" + json.dumps(task_id) + ");"
                    "const buffer=state.authoringBuffers['spec:working'];"
                    "return {base:buffer.base,serverBase:state.authoring.base,dirty:buffer.dirty,"
                    "value:buffer.value};})()",
                )
                assert rebased_spec_state["base"] == rebased_spec_state["serverBase"]
                assert rebased_spec_state["dirty"] and (
                    "newer edit typed while the prior save was pending"
                    in rebased_spec_state["value"]
                )
                evaluate(
                    call,
                    "[...document.querySelectorAll('button')].find(node=>"
                    "node.textContent==='Save working specification').click(); true",
                )
                wait(
                    call,
                    "window.__authoringPosts.filter(row=>row.url.includes('/save-spec')).at(-1)"
                    "?.status===200",
                )
            wait(
                call,
                (
                    "(async()=>{const value=await(await fetch('"
                    "/api/authoring?task='+encodeURIComponent(n"
                    "ew URLSearchParams(location.search).get('t"
                    "ask')),{cache:'no-store'})).json();return "
                    "value.working.body.includes('newer edit ty"
                    "ped while the prior save was pending')})()"
                ),
            )
            assert not evaluate(
                call, 'Boolean(document.querySelector(".authoring-conflict-recovery"))'
            )
            wait(
                call,
                (
                    "document.querySelector('[aria-label=\"Full "
                    "working specification\"]').value.includes('"
                    "newer edit typed while the prior save was "
                    "pending')"
                ),
            )
            evaluate(
                call,
                (
                    """(() => {
              const editor=document.querySelector('[aria-label="Full working specification"]');
              editor.value+='\\nA third edit verifies read-only retry after a lost refresh.\\n';
              editor.dispatchEvent(new Event('input',{bubbles:true}));
              document.querySelector('[aria-label="Full working specification"]').focus();
              window.failOneAuthoringRead=true;
              const priorReadFetch=window.fetch;
              window.fetch=(url,options)=>{
                if(String(url).startsWith('/api/authoring?')&&window.failOneAuthoringRead){
                  window.failOneAuthoringRead=false;
"""
                    "                  return Promise.resolve({ok:false"
                    ",status:503,json:async()=>({error:'fixture read in"
                    "terruption'})});\n"
                    """                }
                return priorReadFetch(url,options);
              };
"""
                    "              [...document.querySelectorAll('butto"
                    "n')].find(node=>node.textContent==='Save working s"
                    "pecification').click();\n"
                    """              return true;
            })()"""
                ),
            )
            wait(call, 'document.body.innerText.includes("fixture read interruption")')
            wait(call, 'Boolean(document.querySelector(".authoring-action-message button"))')
            evaluate(
                call, "document.querySelector('.authoring-action-message button').click(); true"
            )
            try:
                wait(
                    call,
                    'document.body.innerText.includes("The exact saved request and its server '
                    'result were confirmed.")',
                )
            except BaseException as error:
                retry_diagnostic = evaluate(
                    call,
                    "(() => {const state=workspaceStates.get(" + json.dumps(task_id) + ");"
                    "return JSON.stringify({message:state.authoringMessage,"
                    "requests:state.authoringRequests,"
                    "actions:state.authoringRequestActions,forms:state.authoringRequestForms,"
                    "buffer:state.authoringBuffers['spec:working'],"
                    "posts:window.__authoringPosts,body:document.body.innerText.slice(-1600)});})()",
                )
                raise AssertionError(
                    f"exact spec retry did not settle as expected: {retry_diagnostic}"
                ) from error
            wait(
                call,
                (
                    "(async () => (await (await fetch('/api/aut"
                    "horing?task='+encodeURIComponent(new URLSe"
                    "archParams(location.search).get('task'))))"
                    ".json()).working.body.includes('third edit"
                    " verifies read-only retry after a lost ref"
                    "resh'))()"
                ),
            )
            assert evaluate(
                call,
                (
                    "!workspaceStates.get(new URLSearchParams(l"
                    "ocation.search).get('task')).authoringBuff"
                    "ers['spec:working'].dirty"
                ),
            )
            assert evaluate(
                call,
                (
                    "document.querySelector('[aria-label=\"Full "
                    "working specification\"]').value.includes('"
                    "newer edit typed while the prior save was "
                    "pending')"
                ),
            )
            manual_saved = _http_json(server, f"/api/authoring?task={task_id}")[1]["working"]
            assert (
                "third edit verifies read-only retry after a lost refresh" in manual_saved["body"]
            )
            evaluate(call, "document.querySelector('#workspace-tab-review').click(); true")
            wait(
                call, 'Boolean(document.querySelector(".authoring-reconcile select option[value]"))'
            )
            candidate_id = evaluate(
                call,
                (
                    """(async () => {
              const select=document.querySelector('.authoring-reconcile select');
"""
                    "              const result=await fetch('/api/autho"
                    "ring?task='+encodeURIComponent(new URLSearchParams"
                    "(location.search).get('task')),{cache:'no-store'})"
                    ";\n"
                    """              const snapshot=await result.json();
"""
                    "              const candidate=snapshot.revisions.f"
                    "ilter(row=>row.attribution==='model_proposal').at("
                    "-1);\n"
                    """              if(!candidate) {
                throw new Error('model fixture did not persist a candidate revision');
              }
              select.value=candidate.id;
              document.querySelector('.authoring-reconcile button').click();
              return candidate.id;
            })()"""
                ),
            )
            candidate = _http_json(
                server, f"/api/authoring/revision?task={task_id}&revision={candidate_id}"
            )[1]
            wait(call, 'document.body.innerText.includes("Candidate logical diagram")')
            assert evaluate(
                call,
                (
                    """(() => {
              const select=document.querySelector('.authoring-reconcile select');
              const other=[...select.options].find(option=>option.value!==CANDIDATE_ID);
"""
                    "              if(!other) throw new Error('review f"
                    "low requires distinct working and model candidate "
                    "revisions');\n"
                    """              select.value=other.value;
              select.dispatchEvent(new Event('change',{bubbles:true}));
              const labels=[...document.querySelectorAll('.authoring-reconcile label')];
"""
                    "              if(labels.some(label=>label.querySel"
                    "ector('input')?.checked)) throw new Error('candida"
                    "te change retained an earlier acknowledgement');\n"
                    "              [...document.querySelectorAll('butto"
                    "n')].find(node=>node.textContent==='Preview explic"
                    "it adoption').click();\n"
                    """              return true;
            })()"""
                ).replace("CANDIDATE_ID", json.dumps(candidate_id)),
            )
            wait(
                call,
                (
                    'document.body.innerText.includes("Load the'
                    " selected candidate before previewing adop"
                    'tion")'
                ),
            )
            assert not evaluate(call, 'document.body.innerText.includes("Adoption preview")')
            evaluate(
                call,
                (
                    """(() => {
              const select=document.querySelector('.authoring-reconcile select');
              select.value=CANDIDATE_ID; select.dispatchEvent(new Event('change',{bubbles:true}));
"""
                    "              [...document.querySelectorAll('butto"
                    "n')].find(node=>node.textContent==='Load candidate"
                    " diff').click();\n"
                    """              return true;
            })()"""
                ).replace("CANDIDATE_ID", json.dumps(candidate_id)),
            )
            wait(call, 'document.body.innerText.includes("Candidate logical diagram")')
            evaluate(
                call,
                (
                    """(() => {
"""
                    "              const graph=[...document.querySelect"
                    "orAll('label')].find(node=>node.textContent.includ"
                    "es('I reviewed the selected logical graph'));\n"
                    "              const ack=[...document.querySelector"
                    "All('label')].find(node=>node.textContent.includes"
                    "('I acknowledge the listed source changes'));\n"
                    """              graph.querySelector('input').click();
              ack.querySelector('input').click();
"""
                    "              [...document.querySelectorAll('butto"
                    "n')].find(node=>node.textContent==='Preview explic"
                    "it adoption').click();\n"
                    """              return true;
            })()"""
                ),
            )
            wait(call, 'document.body.innerText.includes("Adoption preview")')
            evaluate(
                call,
                (
                    """(() => {
"""
                    "              const graph=[...document.querySelect"
                    "orAll('label')].find(node=>node.textContent.includ"
                    "es('I reviewed the selected logical graph'));\n"
                    "              const ack=[...document.querySelector"
                    "All('label')].find(node=>node.textContent.includes"
                    "('I acknowledge the listed source changes'));\n"
                    """              if(!graph.querySelector('input').checked) {
                graph.querySelector('input').click();
              }
              if(!ack.querySelector('input').checked) {
                ack.querySelector('input').click();
              }
              const originalFetch=window.fetch;
              window.fetch=(url,options)=>{
                if(String(url)==='/api/authoring/reconcile'&&!window.reconcileHttp){
                  return originalFetch(url,options).then(async response=>{
"""
                    "                    window.reconcileHttp={status:r"
                    "esponse.status,body:await response.clone().json()}"
                    ";\n"
                    """                    return response;
                  });
                }
                return originalFetch(url,options);
              };
"""
                    "              [...document.querySelectorAll('butto"
                    "n')].find(node=>node.textContent==='Adopt this rev"
                    "ision and request review').click();\n"
                    """              return true;
            })()"""
                ),
            )
            wait(call, "window.reconcileHttp?.status===200")
            actual_adoption = evaluate(call, "window.reconcileHttp.body")
            assert actual_adoption["adopted"] and actual_adoption["adopted"] != candidate_id, (
                actual_adoption
            )
            wait(call, 'document.body.innerText.includes("Revision adopted")')
            state = evaluate(
                call,
                (
                    """(async () => {
"""
                    "              const response=await fetch('/api/aut"
                    "horing?task='+encodeURIComponent(new URLSearchPara"
                    "ms(location.search).get('task')),{cache:'no-store'"
                    "});\n"
                    "              const value=await response.json(); r"
                    "eturn {adoptedId:value.adopted?.id,adopted:value.a"
                    "dopted?.body,graph:value.adopted?.graph?.value,pha"
                    "se:value.phase,revisions:value.revisions};\n"
                    """            })()"""
                ),
            )
            assert state["adoptedId"] == actual_adoption["adopted"], state
            adopted_detail = _http_json(
                server, f"/api/authoring/revision?task={task_id}&revision={state['adoptedId']}"
            )[1]
            assert adopted_detail["parent_id"] == candidate_id
            assert state["adopted"] == candidate["body"]
            assert state["graph"] == candidate["graph"]["value"]
            assert state["adopted"].startswith("## Task specification")
            assert len(state["graph"]["nodes"]) >= 2 and state["graph"]["edges"]
            _capture(call, artifact_dir / "desktop-light.png")
            evaluate(call, "document.querySelector('#workspace-tab-diagram').click(); true")
            wait(call, 'Boolean(document.querySelector(".workspace-edge text"))')
            evaluate(
                call,
                (
                    "(() => { const canvas=document.querySelect"
                    "or('.workspace-canvas'); canvas.scrollInto"
                    "View({block:'center'}); [...document.query"
                    "SelectorAll('.workspace-diagram-tools butt"
                    "on')].find(node=>node.textContent==='Fit d"
                    "iagram').click(); return true; })()"
                ),
            )
            drag = evaluate(
                call,
                (
                    """(() => {
              const node=document.querySelector('.workspace-node');
"""
                    "              const box=node.getBoundingClientRect"
                    "(); const state=workspaceStates.get(new URLSearchP"
                    "arams(location.search).get('task'));\n"
                    """              return {id:node.dataset.nodeId,x:box.left+box.width/2,
                y:box.top+box.height/2,
                original:state.source.graph.nodes.find(row=>row.id===node.dataset.nodeId).x};
            })()"""
                ),
            )
            call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mousePressed",
                    "x": drag["x"],
                    "y": drag["y"],
                    "button": "left",
                    "buttons": 1,
                },
            )
            call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseMoved",
                    "x": drag["x"] + 18,
                    "y": drag["y"] + 8,
                    "button": "left",
                    "buttons": 1,
                },
            )
            call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "x": drag["x"] + 18,
                    "y": drag["y"] + 8,
                    "button": "left",
                    "buttons": 0,
                },
            )
            wait(
                call,
                (
                    "workspaceStates.get(new URLSearchParams(lo"
                    "cation.search).get('task')).operations.som"
                    "e(op=>op.type==='node_position')"
                ),
            )
            moved = evaluate(
                call,
                """(() => {
              const state=workspaceStates.get(new URLSearchParams(location.search).get('task'));
              return state.operations.find(op=>op.type==='node_position');
            })()""",
            )
            assert abs(moved["x"] - drag["original"]) >= 5
            edge_label = evaluate(
                call,
                (
                    """(() => {
              const text=document.querySelector('.workspace-edge text');
"""
                    "              return {visible:text.firstChild?.tex"
                    "tContent||text.textContent,full:text.querySelector"
                    "('title')?.textContent||''};\n"
                    """            })()"""
                ),
            )
            assert edge_label["full"] == "preserves submitted task data after restart and recovery"
            assert len(edge_label["visible"]) < len(edge_label["full"])
            view_before_resize = evaluate(
                call,
                "(() => {const task=new URLSearchParams(location.search).get('task');"
                "const state=workspaceStates.get(task);return {fit:state.diagramView?.fit,"
                "viewBox:document.querySelector('.workspace-canvas svg')"
                ".getAttribute('viewBox')};})()",
            )
            assert view_before_resize["fit"] is True, view_before_resize
            assert (
                evaluate(call, "document.querySelector('.workspace-canvas svg').style.width")
                == "100%"
            )
            _capture(call, artifact_dir / "diagram-fit-light.png")
            _set_theme(call, "dark")
            _capture(call, artifact_dir / "diagram-fit-dark.png")
            call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
            )
            evaluate(
                call,
                "window.__savedProposalSseId=null;window.__svgBeforeSavedSse=null;"
                "window.__svgAtSavedSse=null;window.__diagramViewAtSavedSse=null;"
                "window.__baselineProposalIds=(workspaceData.task.workspace.proposals||[])"
                ".map(proposal=>proposal.id);window.__priorSseMessage=source.onmessage;"
                "source.onmessage=function(event){const priorSvg=document.querySelector("
                "'.workspace-canvas svg');const data=JSON.parse(event.data);"
                "const added=(data.task?.workspace?.proposals||[]).find(proposal=>"
                "!window.__baselineProposalIds.includes(proposal.id));"
                "const result=window.__priorSseMessage.call(this,event);if(added){"
                "window.__savedProposalSseId=added.id;window.__svgBeforeSavedSse=priorSvg;"
                "window.__svgAtSavedSse=document.querySelector('.workspace-canvas svg');"
                "window.__diagramViewAtSavedSse=workspaceStates.get("
                "new URLSearchParams(location.search).get('task')).diagramView;}"
                "return result;};true",
            )
            evaluate(
                call,
                "(() => {const node=document.querySelector('.workspace-node');"
                "node.dispatchEvent(new MouseEvent('click',{bubbles:true}));"
                "const x=[...document.querySelectorAll('.workspace-inspector label')]"
                ".find(label=>label.textContent.startsWith('Horizontal position'))"
                ".querySelector('input');"
                "x.value=String(Number(x.value)+38);"
                "x.dispatchEvent(new Event('input',{bubbles:true}));"
                "[...document.querySelectorAll('.workspace-inspector button')]"
                ".find(button=>button.textContent==='Propose position').click();"
                "return true;})()",
            )
            evaluate(
                call,
                "document.querySelector('.workspace-savebar button').click();true",
            )
            wait(
                call,
                "document.querySelector('.workspace-savebar [role=status]')?.textContent"
                ".includes('Saved proposal')",
            )
            wait(
                call,
                "window.__savedProposalSseId===workspaceStates.get("
                "new URLSearchParams(location.search).get('task')).savedId",
            )
            wait(
                call,
                "window.__svgAtSavedSse!==window.__svgBeforeSavedSse",
            )
            assert evaluate(call, "window.__diagramViewAtSavedSse?.fit") is True
            assert (
                evaluate(call, "window.__svgAtSavedSse?.getAttribute('viewBox')")
                == view_before_resize["viewBox"]
            )
            mobile_view = evaluate(
                call,
                "(() => {const task=new URLSearchParams(location.search).get('task');"
                "const state=workspaceStates.get(task),svg=document.querySelector("
                "'.workspace-canvas svg');return {fit:state.diagramView?.fit,"
                "viewBox:svg.getAttribute('viewBox'),width:svg.style.width,"
                "nodes:[...svg.querySelectorAll('.workspace-node')]"
                ".map(node=>{const r=node.getBoundingClientRect();"
                "return {left:r.left,right:r.right};})};})()",
            )
            assert mobile_view["fit"] is True, mobile_view
            assert mobile_view["viewBox"] == view_before_resize["viewBox"], mobile_view
            assert mobile_view["width"] == "100%", mobile_view
            assert all(
                node["left"] >= 0 and node["right"] <= 390 for node in mobile_view["nodes"]
            ), mobile_view
            _set_theme(call, "dark")
            _capture(call, artifact_dir / "mobile-dark.png")
            _set_theme(call, "light")
            _capture(call, artifact_dir / "mobile-light.png")
            first_task = task_id
            first_project_id = evaluate(
                call,
                "workspaceStates.get(" + json.dumps(first_task) + ").projectId",
            )
            evaluate(call, "document.querySelector('#workspace-tab-interview').click(); true")
            wait(call, 'Boolean(document.querySelector("[aria-label=\\"Follow-up message\\"]"))')
            evaluate(
                call,
                """(() => {
              const field=document.querySelector('[aria-label="Follow-up message"]');
              field.value='Keep this first task draft while I start another interview.';
              field.dispatchEvent(new Event('input',{bubbles:true}));
              document.querySelector('.workspace-new-interview').click();
              return true;
            })()""",
            )
            wait(call, 'document.querySelector(".authoring-new-task-dialog")?.open===true')
            evaluate(
                call,
                """(() => {
              const form=document.querySelector('.authoring-new-task-dialog #project-start form');
              const request=form.querySelector('textarea');
              request.value='Create a second task through the populated workspace interview form.';
              request.dispatchEvent(new Event('input',{bubbles:true}));
              form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
              return true;
            })()""",
            )
            wait(
                call, f'new URLSearchParams(location.search).get("task")!=={json.dumps(first_task)}'
            )
            second_task = evaluate(call, "new URLSearchParams(location.search).get('task')")
            assert second_task and second_task != first_task
            wait(
                call,
                (
                    'document.body.innerText.includes("Create a'
                    " second task through the populated workspa"
                    'ce interview form")'
                ),
            )
            _wait_for_succeeded_generation(call)
            preserved = evaluate(
                call,
                (
                    """(() => {
"""
                    "              const value=localStorage.getItem('myharness-authoring:'"
                    "+PROJECT_ID+':'+FIRST_TASK+':followup');\n"
                    """              return value ? JSON.parse(value).value : null;
            })()"""
                )
                .replace("PROJECT_ID", json.dumps(first_project_id))
                .replace("FIRST_TASK", json.dumps(first_task)),
            )
            assert preserved == "Keep this first task draft while I start another interview.", (
                preserved
            )
            first_snapshot = evaluate(
                call,
                (
                    """(async () => {
"""
                    "              const response=await fetch('/api/aut"
                    "horing?task='+encodeURIComponent(FIRST_TASK),{cach"
                    "e:'no-store'});\n"
                    """              const value=await response.json();
"""
                    "              return {working:value.working?.body,"
                    "adopted:value.adopted?.body,graph:value.adopted?.g"
                    "raph?.value};\n"
                    """            })()"""
                ).replace("FIRST_TASK", json.dumps(first_task)),
            )
            assert first_snapshot["working"] == candidate["body"]
            assert first_snapshot["adopted"].startswith("## Task specification")
            assert len(first_snapshot["graph"]["nodes"]) >= 2
            retained_manual = _http_json(
                server, f"/api/authoring/revision?task={first_task}&revision={manual_saved['id']}"
            )[1]
            assert retained_manual["body"] == manual_saved["body"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_chrome_rework1_command_recovery_after_stale_regenerate_base(
    db_path, sample_repo, monkeypatch, tmp_path
):
    ModelFixture.calls = 0
    monkeypatch.setattr(
        "harness.authoring_worker.AuthoringWorker",
        lambda path, root, **kwargs: AuthoringWorker(
            path, root, provider_factory=ModelFixture, **kwargs
        ),
    )
    server = DashboardServer(db_path, None, project_root=sample_repo)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    server.poll_seconds = 0.1
    thread.start()
    try:
        with chrome(tmp_path / "chrome-profile", server.url, 1280, 900) as call:
            task_id = _start_rework1_fixture_task(
                call, "Build a task service with recoverable model requests."
            )
            evaluate(call, "document.querySelector('#workspace-tab-interview').click(); true")
            wait(
                call,
                "document.querySelector('#workspace-tab-interview')?.getAttribute('aria-selected')==='true'",
            )
            wait(
                call,
                "(async()=>{const task="
                + json.dumps(task_id)
                + ";const response=await fetch('/api/authoring?task='+encodeURIComponent(task),"
                "{cache:'no-store'});const snapshot=await response.json();"
                "const ui=workspaceStates.get(task)?.authoring;"
                "return Boolean(ui&&JSON.stringify(ui.base)===JSON.stringify(snapshot.base));})()",
            )
            _capture_authoring_posts(call)
            before = _http_json(server, f"/api/authoring?task={task_id}")[1]
            evaluate(
                call,
                "window.__regenStream={source,handler:source.onmessage,queued:[]};"
                "source.onmessage=event=>window.__regenStream.queued.push(event);true",
            )
            external_status, external = _http_json(
                server,
                "/api/authoring/followup",
                method="POST",
                body={
                    "task": task_id,
                    "expected": before["base"],
                    "request_id": str(uuid.uuid4()),
                    "body": "A concurrent client advanced the authoring base.",
                },
            )
            assert external_status == 200, external
            wait(
                call,
                "window.__regenStream.queued.some(event=>JSON.parse(event.data).task?.authoring?."
                "contributions?.some(item=>item.id==="
                + json.dumps(external["contribution"])
                + "))",
            )
            evaluate(
                call,
                "[...document.querySelectorAll('button')]"
                ".find(item=>item.textContent==='Request model response').click();true",
            )
            wait(
                call,
                "document.querySelector('.authoring-action-message')?.innerText.includes("
                "'authoring source or revision changed; reload')",
            )
            rejected = evaluate(
                call,
                "(()=>{const item=window.__authoringPosts.at(-1);"
                "return {status:item.status,body:item.body,request:item.request};})()",
            )
            assert rejected["status"] == 409, rejected
            assert rejected["request"]["expected"] == before["base"], rejected
            assert "authoring source or revision changed" in rejected["body"], rejected
            assert evaluate(
                call,
                "Boolean([...document.querySelectorAll('.authoring-action-message button')]"
                ".find(item=>item.textContent==='Refresh latest state and keep my text'))",
            )
            workspace_key = f"myharness-workspace:{before['project']['id']}:{task_id}"
            stored = evaluate(
                call,
                "JSON.parse(localStorage.getItem(" + json.dumps(workspace_key) + "))",
            )
            exact_payload = stored["authoringRequests"]["regenerate"]
            assert exact_payload == rejected["request"], {
                "stored": exact_payload,
                "submitted": rejected["request"],
            }
            exact_retry = evaluate(
                call,
                "(()=>{const payload="
                + json.dumps(exact_payload)
                + ";return fetch('/api/authoring/regenerate',{method:'POST',"
                "headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)"
                "}).then(async response=>({status:response.status,"
                "body:await response.text()}));})()",
            )
            assert exact_retry["status"] == 409, exact_retry
            assert "authoring source or revision changed" in exact_retry["body"], exact_retry
            retry_entry = evaluate(
                call,
                "window.__authoringPosts.at(-1).request",
            )
            assert retry_entry == exact_payload, {"retry": retry_entry, "expected": exact_payload}

            flushed = evaluate(
                call,
                "(()=>{const probe=window.__regenStream;const event=probe.queued.find(item=>"
                "JSON.parse(item.data).task?.authoring?.contributions?.some(row=>row.id==="
                + json.dumps(external["contribution"])
                + "));if(!event)return false;probe.source.onmessage=probe.handler;"
                "probe.handler.call(probe.source,event);return true;})()",
            )
            assert flushed is True
            wait(
                call,
                "document.body.innerText.includes('A concurrent client advanced "
                "the authoring base.')",
            )
            posts_before_local_reject = evaluate(call, "window.__authoringPosts.length")
            evaluate(
                call,
                "[...document.querySelectorAll('button')]"
                ".find(item=>item.textContent==='Request model response').click();true",
            )
            wait(
                call,
                "document.querySelector('.authoring-action-message')?.innerText.includes("
                "'A previous request with different text is unresolved')",
            )
            assert evaluate(call, "window.__authoringPosts.length") == posts_before_local_reject
            assert evaluate(
                call,
                "Boolean([...document.querySelectorAll('.authoring-action-message button')]"
                ".find(item=>item.textContent==='Refresh latest state and keep my text'))",
            )

            evaluate(
                call,
                "[...document.querySelectorAll('.authoring-action-message button')]"
                ".find(item=>item.textContent==='Refresh latest state and keep my text')"
                ".click();true",
            )
            wait(
                call,
                "document.querySelector('.authoring-action-message')?.innerText.includes("
                "'Latest server state loaded')",
            )
            recovered = evaluate(
                call,
                "JSON.parse(localStorage.getItem(" + json.dumps(workspace_key) + "))",
            )
            assert recovered.get("authoringConflict") is None, recovered
            assert "regenerate" not in recovered.get("authoringRequests", {}), recovered
            assert any(
                row["key"] == "regenerate"
                and row["outcome"] == "rejected-stale-base"
                and row["payload"] == exact_payload
                for row in recovered.get("authoringRequestHistory", [])
            ), recovered
            generate_count_before = len(
                [
                    job
                    for job in _http_json(server, f"/api/authoring?task={task_id}")[1]["jobs"]
                    if job["kind"] == "generate"
                ]
            )
            evaluate(
                call,
                "window.__retryFetch=window.fetch;window.__droppedRegenerateAck=false;"
                "window.fetch=async(...args)=>{const response=await window.__retryFetch(...args);"
                "if(String(args[0]).includes('/api/authoring/regenerate')"
                "&&args[1]?.method==='POST'&&!window.__droppedRegenerateAck){"
                "window.__droppedRegenerateAck=true;"
                "throw new TypeError('simulated lost acknowledgement');}"
                "return response;};true",
            )
            evaluate(
                call,
                "[...document.querySelectorAll('button')]"
                ".find(item=>item.textContent==='Request model response').click();true",
            )
            wait(
                call,
                "document.querySelector('.authoring-action-message')?.innerText.includes("
                "'simulated lost acknowledgement')",
            )
            evaluate(call, "window.fetch=window.__retryFetch;true")
            accepted = evaluate(
                call,
                "window.__authoringPosts.at(-1)",
            )
            assert accepted["status"] == 202, accepted
            assert accepted["request"]["request_id"] != exact_payload["request_id"], accepted
            assert accepted["request"]["expected"] == external["base"], accepted
            assert accepted["request"]["host"] == exact_payload["host"], accepted
            unknown_state = evaluate(
                call,
                "JSON.parse(localStorage.getItem(" + json.dumps(workspace_key) + "))",
            )
            assert unknown_state.get("authoringConflict") is None, unknown_state
            assert unknown_state["authoringRequests"]["regenerate"] == accepted["request"]
            assert evaluate(
                call,
                "Boolean([...document.querySelectorAll('.authoring-action-message button')]"
                ".find(item=>item.textContent==='Retry exact saved request'))",
            )
            assert not evaluate(
                call,
                "Boolean([...document.querySelectorAll('.authoring-action-message button')]"
                ".find(item=>item.textContent==='Refresh latest state and keep my text'))",
            )
            evaluate(
                call,
                "[...document.querySelectorAll('.authoring-action-message button')]"
                ".find(item=>item.textContent==='Retry exact saved request').click();true",
            )
            wait(
                call,
                "document.querySelector('.authoring-action-message')?.innerText.includes("
                "'The exact saved request and its server result were confirmed')",
            )
            _wait_for_succeeded_generation(call, count=2)
            jobs = _http_json(server, f"/api/authoring?task={task_id}")[1]["jobs"]
            generate_count_after = len([job for job in jobs if job["kind"] == "generate"])
            assert generate_count_after == generate_count_before + 1, jobs
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_chrome_rework1_command_recovery_after_phase_gate_base_change(
    controller, db_path, sample_repo, monkeypatch, tmp_path
):
    from harness.interview import InterviewEngine
    from harness.investigation import InvestigationReport

    task = controller.create_task(
        "Build an interview with a complete set of recorded answers.", "trivial"
    )
    controller.record_investigation(
        task.id,
        InvestigationReport(str(sample_repo), None, task.request, (), (), (), ()),
    )
    controller.advance(task.id)
    engine = InterviewEngine(controller, task.id)
    while engine.sync_due() or engine.upcoming():
        for question in engine.next(group=True):
            engine.answer(question.id, "The recorded baseline provides the current requirement.")
    assert controller.get_task(task.id).phase.value == "interview"

    class NoQuestionModelFixture:
        def __init__(self, host):
            assert host == "codex"

        def run(self, inputs, *, cancelled=lambda: False):
            assert inputs["request"] and not cancelled()
            return _model_output(include_question=False)

    monkeypatch.setattr(
        "harness.authoring_worker.AuthoringWorker",
        lambda path, root, **kwargs: AuthoringWorker(
            path, root, provider_factory=NoQuestionModelFixture, **kwargs
        ),
    )
    server = DashboardServer(db_path, task.id, project_root=sample_repo)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    server.poll_seconds = 0.1
    thread.start()
    try:
        url = f"{server.url.rstrip('/')}/?task={task.id}&tab=interview"
        with chrome(tmp_path / "chrome-profile", url, 1280, 900) as call:
            wait(
                call,
                "Boolean([...document.querySelectorAll('button')].find(item=>"
                "item.textContent==='Start / resume interview'))",
            )
            evaluate(
                call,
                "[...document.querySelectorAll('button')]"
                ".find(item=>item.textContent==='Start / resume interview').click();true",
            )
            _wait_for_succeeded_generation(call)
            evaluate(call, "document.querySelector('#workspace-tab-requirements').click();true")
            wait(
                call,
                "document.querySelector('#workspace-tab-requirements')?.getAttribute('aria-selected')==='true'",
            )
            wait(
                call,
                "Boolean([...document.querySelectorAll('button')].find("
                "item=>item.textContent==='Continue to next phase'))",
            )
            before = _http_json(server, f"/api/authoring?task={task.id}")[1]
            assert before["questions"] and all(
                question["answer"] for question in before["questions"]
            )
            wait(
                call,
                "(async()=>{const task="
                + json.dumps(task.id)
                + ";const response=await fetch('/api/authoring?task='+encodeURIComponent(task),"
                "{cache:'no-store'});const snapshot=await response.json();"
                "const ui=workspaceStates.get(task)?.authoring;"
                "return Boolean(ui&&JSON.stringify(ui.base)===JSON.stringify(snapshot.base));})()",
            )
            before = _http_json(server, f"/api/authoring?task={task.id}")[1]
            _capture_authoring_posts(call)
            evaluate(
                call,
                "window.__phaseStream={source,handler:source.onmessage,queued:[]};"
                "source.onmessage=event=>window.__phaseStream.queued.push(event);true",
            )
            external_status, external = _http_json(
                server,
                "/api/authoring/followup",
                method="POST",
                body={
                    "task": task.id,
                    "expected": before["base"],
                    "request_id": str(uuid.uuid4()),
                    "body": "A concurrent edit advanced the phase-action base.",
                },
            )
            assert external_status == 200, external
            wait(
                call,
                "window.__phaseStream.queued.some(event=>JSON.parse(event.data).task?.authoring?."
                "contributions?.some(item=>item.id==="
                + json.dumps(external["contribution"])
                + "))",
            )
            evaluate(
                call,
                "[...document.querySelectorAll('button')]"
                ".find(item=>item.textContent==='Continue to next phase').click();true",
            )
            wait(
                call,
                "document.querySelector('.authoring-action-message')?.innerText.includes("
                "'authoring source or revision changed; reload')",
            )
            rejected = evaluate(
                call,
                "(()=>{const item=window.__authoringPosts.at(-1);"
                "return {status:item.status,body:item.body,request:item.request};})()",
            )
            assert rejected["status"] == 409, rejected
            assert rejected["request"]["expected"] == before["base"], rejected
            assert "authoring source or revision changed" in rejected["body"], rejected
            exact_retry = evaluate(
                call,
                "(()=>{const payload="
                + json.dumps(rejected["request"])
                + ";return fetch('/api/authoring/planning',{method:'POST',"
                "headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)"
                "}).then(async response=>({status:response.status,"
                "body:await response.text()}));})()",
            )
            assert exact_retry["status"] == 409, exact_retry
            assert "authoring source or revision changed" in exact_retry["body"], exact_retry
            flushed = evaluate(
                call,
                "(()=>{const probe=window.__phaseStream;const event=probe.queued.find(item=>"
                "JSON.parse(item.data).task?.authoring?.contributions?.some(row=>row.id==="
                + json.dumps(external["contribution"])
                + "));if(!event)return false;probe.source.onmessage=probe.handler;"
                "probe.handler.call(probe.source,event);return true;})()",
            )
            assert flushed is True
            wait(
                call,
                "document.body.innerText.includes('A concurrent edit advanced "
                "the phase-action base.')",
            )
            posts_before_local_reject = evaluate(call, "window.__authoringPosts.length")
            evaluate(
                call,
                "[...document.querySelectorAll('button')]"
                ".find(item=>item.textContent==='Continue to next phase').click();true",
            )
            wait(
                call,
                "document.querySelector('.authoring-action-message')?.innerText.includes("
                "'A previous request with different text is unresolved')",
            )
            assert evaluate(call, "window.__authoringPosts.length") == posts_before_local_reject
            evaluate(
                call,
                "[...document.querySelectorAll('.authoring-action-message button')]"
                ".find(item=>item.textContent==='Refresh latest state and keep my text')"
                ".click();true",
            )
            wait(
                call,
                "document.querySelector('.authoring-action-message')?.innerText.includes("
                "'Latest server state loaded')",
            )
            workspace_key = f"myharness-workspace:{before['project']['id']}:{task.id}"
            recovered = evaluate(
                call,
                "JSON.parse(localStorage.getItem(" + json.dumps(workspace_key) + "))",
            )
            assert recovered.get("authoringConflict") is None, recovered
            assert "phase-advance" not in recovered.get("authoringRequests", {}), recovered
            assert any(
                row["key"] == "phase-advance"
                and row["outcome"] == "rejected-stale-base"
                and row["payload"] == rejected["request"]
                for row in recovered.get("authoringRequestHistory", [])
            ), recovered
            evaluate(
                call,
                "[...document.querySelectorAll('button')]"
                ".find(item=>item.textContent==='Continue to next phase').click();true",
            )
            wait(
                call,
                "(async()=>{const value=await(await fetch('/api/authoring?task='+"
                + json.dumps(task.id)
                + ")).json();return value.phase==='design';})()",
            )
            assert _http_json(server, f"/api/authoring?task={task.id}")[1]["phase"] == "design"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_chrome_rework1_buffers_structured_interview_design_and_test_plan_gates(
    db_path, sample_repo, monkeypatch, tmp_path
):
    ModelFixture.calls = 0
    monkeypatch.setattr(
        "harness.authoring_worker.AuthoringWorker",
        lambda path, root, **kwargs: AuthoringWorker(
            path, root, provider_factory=ModelFixture, **kwargs
        ),
    )
    server = DashboardServer(db_path, None, project_root=sample_repo)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    server.poll_seconds = 0.1
    thread.start()
    try:
        with chrome(tmp_path / "chrome-profile", server.url, 1280, 900) as call:
            wait(call, 'Boolean(document.querySelector("#project-start textarea"))')
            evaluate(
                call,
                (
                    """(() => {
              const request=document.querySelector('#project-start textarea');
"""
                    "              request.value='Build a small, reliab"
                    "le task capture tool with validation and durable s"
                    "torage.';\n"
                    """              request.dispatchEvent(new Event('input',{bubbles:true}));
"""
                    "              document.querySelector('#project-sta"
                    "rt form').dispatchEvent(new Event('submit',{bubble"
                    "s:true,cancelable:true}));\n"
                    """              return true;
            })()"""
                ),
            )
            wait(call, 'location.search.includes("task=")')
            wait(
                call,
                (
                    "(async () => (await (await fetch('/api/aut"
                    "horing?task='+new URLSearchParams(location"
                    ".search).get('task'))).json()).questions.s"
                    "ome(row=>row.text==='What should happen wh"
                    "en the input is empty?'))()"
                ),
            )
            evaluate(call, "document.querySelector('#workspace-tab-interview').click(); true")
            wait(
                call,
                'document.body.innerText.includes("What should happen when the input is empty?")',
            )
            _wait_for_succeeded_generation(call)
            evaluate(call, "document.querySelector('#workspace-tab-interview').click(); true")
            pending_count = evaluate(
                call,
                (
                    "document.querySelectorAll('.workspace-pane"
                    "l .authoring-question textarea[aria-label^"
                    '=\\"Answer question \\"]\').length'
                ),
            )
            assert pending_count > 0, (
                "the real startup/model fixture should expose structured interview questions"
            )
            for _index in range(50):
                question = evaluate(
                    call,
                    (
                        """(() => {
"""
                        "                  const field=document.querySelect"
                        "or('.workspace-panel .authoring-question textarea["
                        'aria-label^="Answer question "]\');\n'
                        """                  if(!field) return null;
                  const card=field.closest('.authoring-question');
                  const id=card.dataset.questionId;
"""
                        "                  field.value='The tool should val"
                        "idate the input and preserve the submitted task da"
                        "ta.';\n"
                        """                  field.dispatchEvent(new Event('input',{bubbles:true}));
"""
                        "                  [...card.querySelectorAll('butto"
                        "n')].find(node=>node.textContent==='Save answer')."
                        "click();\n"
                        """                  return id;
                })()"""
                    ),
                )
                if question is None:
                    break
                wait(
                    call,
                    (
                        """(async () => {
"""
                        "                  const value=await (await fetch('"
                        "/api/authoring?task='+encodeURIComponent(new URLSe"
                        "archParams(location.search).get('task')),{cache:'n"
                        "o-store'})).json();\n"
                        "                  return value.questions.find(row="
                        ">row.id===Number(QUESTION_ID))?.answer?.includes('"
                        "The tool should validate');\n"
                        """                })()"""
                    ).replace("QUESTION_ID", json.dumps(question)),
                )
                question_card = (
                    f"document.querySelector('.authoring-question[data-question-id=\""
                    f"{question}\"]')"
                )
                wait(
                    call,
                    question_card
                    + "?.textContent.includes('Your recorded answer: The tool should validate')",
                )
            assert (
                evaluate(
                    call,
                    (
                        "document.querySelectorAll('.workspace-pane"
                        "l .authoring-question textarea[aria-label^"
                        '=\\"Answer question \\"]\').length'
                    ),
                )
                == 0
            )
            decision = evaluate(
                call,
                (
                    "Boolean([...document.querySelectorAll('but"
                    "ton')].find(node=>node.textContent==='Acce"
                    "pted recommendation'))"
                ),
            )
            assert decision, (
                "model recommendation should expose explicit developer decision controls"
            )
            evaluate(
                call,
                (
                    "[...document.querySelectorAll('button')].f"
                    "ind(node=>node.textContent==='Accepted rec"
                    "ommendation').click(); true"
                ),
            )
            wait(call, 'document.body.innerText.includes("Decision status: accepted")')
            evaluate(call, "document.querySelector('#workspace-tab-requirements').click(); true")
            wait(
                call,
                (
                    'Boolean([...document.querySelectorAll("but'
                    'ton")].find(node=>node.textContent==="Cont'
                    'inue to next phase"))'
                ),
            )
            evaluate(
                call,
                (
                    "[...document.querySelectorAll('button')].f"
                    "ind(node=>node.textContent==='Continue to "
                    "next phase').click(); true"
                ),
            )
            wait(
                call,
                'document.querySelector(".authoring-statusbar")?.textContent.includes("· design")',
            )
            evaluate(call, "document.querySelector('#workspace-tab-plan').click(); true")
            wait(call, 'Boolean(document.querySelector("[aria-label=\\"Design description\\"]"))')
            evaluate(
                call,
                "(() => {const text=document.querySelector('[aria-label=\"Design description\"]');"
                "text.value='Use a task service that validates before persistence "
                "and isolates task records.';"
                "text.dispatchEvent(new Event('input',{bubbles:true}));"
                "const section=document.querySelector('[aria-label=\"Design section\"]');"
                "section.value='interactions';"
                "section.dispatchEvent(new Event('change',{bubbles:true}));"
                "const delegated=document.querySelector("
                "'[aria-label=\"Delegate this design entry\"]');"
                "delegated.checked=true;"
                "delegated.dispatchEvent(new Event('change',{bubbles:true}));"
                "document.querySelector('#workspace-tab-interview').click();"
                "document.querySelector('#workspace-tab-plan').click();return location.href;})()",
            )
            design_url = evaluate(call, "location.href")
            call("Page.navigate", {"url": design_url})
            wait(call, 'Boolean(document.querySelector("[aria-label=\\"Design description\\"]"))')
            design_draft = evaluate(
                call,
                "({section:document.querySelector('[aria-label=\"Design section\"]')?.value,"
                "text:document.querySelector('[aria-label=\"Design description\"]')?.value,"
                "delegated:document.querySelector("
                "'[aria-label=\"Delegate this design entry\"]')?.checked})",
            )
            assert design_draft == {
                "section": "interactions",
                "text": (
                    "Use a task service that validates before persistence "
                    "and isolates task records."
                ),
                "delegated": True,
            }
            evaluate(
                call,
                "[...document.querySelectorAll('button')]"
                ".find(node=>node.textContent==='Add design entry').click();true",
            )
            wait(call, 'document.body.innerText.includes("isolates task records")')
            evaluate(
                call,
                (
                    "[...document.querySelectorAll('button')].f"
                    "ind(node=>node.textContent==='Continue to "
                    "next phase').click(); true"
                ),
            )
            wait(
                call,
                (
                    'document.querySelector(".authoring-statusb'
                    'ar")?.textContent.includes("· test_plan")'
                ),
            )
            wait(call, 'Boolean(document.querySelector("[aria-label=\\"Scenario\\"]"))')
            source = evaluate(
                call,
                (
                    """(async () => {
"""
                    "              const response=await fetch('/api/aut"
                    "horing?task='+encodeURIComponent(new URLSearchPara"
                    "ms(location.search).get('task')),{cache:'no-store'"
                    "});\n"
                    "              const value=await response.json(); r"
                    "eturn `Q${value.questions.find(row=>row.status==='"
                    "answered').id}`;\n"
                    """            })()"""
                ),
            )
            evaluate(
                call,
                "(() => {const set=(label,value,type='input')=>{const field="
                'document.querySelector(`[aria-label="${label}"]`);'
                "field.value=value;field.dispatchEvent(new Event(type,{bubbles:true}));};"
                "set('Test category','unit','change');"
                "set('Scenario','A persisted task survives reload after "
                "invalid input is rejected.');"
                "set('Coverage references'," + json.dumps(source) + ");"
                "set('Mocking approach','mock','change');"
                "set('Mocking reason','Use a controlled repository boundary to verify "
                "persistence and failure behavior.');"
                "document.querySelector('#workspace-tab-interview').click();"
                "document.querySelector('#workspace-tab-plan').click();return location.href;})()",
            )
            scenario_url = evaluate(call, "location.href")
            call("Page.navigate", {"url": scenario_url})
            wait(call, 'Boolean(document.querySelector("[aria-label=\\"Scenario\\"]"))')
            scenario_draft = evaluate(
                call,
                "({category:document.querySelector('[aria-label=\"Test category\"]')?.value,"
                "text:document.querySelector('[aria-label=\"Scenario\"]')?.value,"
                "covers:document.querySelector('[aria-label=\"Coverage references\"]')?.value,"
                "mock:document.querySelector('[aria-label=\"Mocking approach\"]')?.value,"
                "reason:document.querySelector('[aria-label=\"Mocking reason\"]')?.value})",
            )
            assert scenario_draft == {
                "category": "unit",
                "text": "A persisted task survives reload after invalid input is rejected.",
                "covers": source,
                "mock": "mock",
                "reason": (
                    "Use a controlled repository boundary to verify persistence "
                    "and failure behavior."
                ),
            }
            evaluate(
                call,
                "[...document.querySelectorAll('button')]"
                ".find(node=>node.textContent==='Add scenario').click();true",
            )
            wait(call, 'document.body.innerText.includes("controlled repository boundary")')
            assert "Mocking: mock · Use a controlled repository boundary" in evaluate(
                call, "document.body.innerText"
            )
            _capture_authoring_posts(call)
            for category in ("integration", "e2e", "regression", "edge_case", "failure"):
                wait(
                    call,
                    "(()=>{const scenario=[...document.querySelectorAll('[aria-label]')].find("
                    "node=>node.getAttribute('aria-label')==='Scenario');"
                    "const button=[...document.querySelectorAll('button')].find(node=>"
                    "node.textContent==='Add scenario');return scenario?.value===''&&"
                    "button&&!button.disabled})()",
                )
                posts_before = evaluate(call, "window.__authoringPosts.length")
                scenario_text = (
                    f"{category} case confirms saved task data survives the expected boundary."
                )
                submit_state = evaluate(
                    call,
                    (
                        "(() => {const set=(label,value,type='input')=>{"
                        "const field=document.querySelector('[aria-label=\\\"'+label+'\\\"]');"
                        "field.value=value;field.dispatchEvent(new Event(type,{bubbles:true}));};"
                        "set('Test category',CATEGORY,'change');"
                        "set('Scenario',SCENARIO);"
                        "set('Coverage references',SOURCE_REF);"
                        "set('Mocking approach','real','change');"
                        "set('Mocking reason','Exercise the configured dependency "
                        "boundary for this category.');"
                        "const button=[...document.querySelectorAll('button')].find(node=>"
                        "node.textContent==='Add scenario');const disabled=button.disabled;"
                        "button.click();const category=[...document.querySelectorAll("
                        "'[aria-label]')]"
                        ".find(node=>node.getAttribute('aria-label')==='Test category').value;"
                        "return {disabled,category};})()"
                    )
                    .replace("CATEGORY", json.dumps(category))
                    .replace("SCENARIO", json.dumps(scenario_text))
                    .replace("SOURCE_REF", json.dumps(source)),
                )
                assert not submit_state["disabled"], {"category": category, **submit_state}
                wait(call, f"window.__authoringPosts.length>{posts_before}")
                planning_post = evaluate(call, f"window.__authoringPosts[{posts_before}]")
                assert planning_post["status"] == 200, {
                    "category": category,
                    "submitted": submit_state,
                    "post": planning_post,
                }
                scenario_saved = f"document.body.innerText.includes({json.dumps(scenario_text)})"
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline and not evaluate(call, scenario_saved):
                    time.sleep(0.05)
                assert evaluate(call, scenario_saved), evaluate(call, "document.body.innerText")
                wait(
                    call,
                    "(()=>{const scenario=[...document.querySelectorAll('[aria-label]')].find("
                    "node=>node.getAttribute('aria-label')==='Scenario');"
                    "const button=[...document.querySelectorAll('button')].find(node=>"
                    "node.textContent==='Add scenario');return scenario?.value===''&&"
                    "button&&!button.disabled})()",
                )
            wait(
                call,
                (
                    "![...document.querySelectorAll('button')]."
                    "find(node=>node.textContent==='Approve tes"
                    "t plan').disabled"
                ),
            )
            evaluate(
                call,
                (
                    "[...document.querySelectorAll('button')].f"
                    "ind(node=>node.textContent==='Approve test"
                    " plan').click(); true"
                ),
            )
            wait(call, 'document.body.innerText.includes("Test plan approved")')
            evaluate(
                call,
                (
                    "[...document.querySelectorAll('button')].f"
                    "ind(node=>node.textContent==='Continue to "
                    "next phase').click(); true"
                ),
            )
            wait(
                call,
                (
                    'document.querySelector(".authoring-statusb'
                    'ar")?.textContent.includes("· final_review'
                    '")'
                ),
            )
            evaluate(call, "document.querySelector('#workspace-tab-review').click(); true")
            assert evaluate(call, "Boolean(document.querySelector('.authoring-reconcile'))")
            assert evaluate(call, "Boolean(document.querySelector('#approvals'))")
            snapshot = evaluate(
                call,
                (
                    """(async () => {
"""
                    "              const response=await fetch('/api/aut"
                    "horing?task='+encodeURIComponent(new URLSearchPara"
                    "ms(location.search).get('task')),{cache:'no-store'"
                    "});\n"
                    """              const value=await response.json();
"""
                    "              return {phase:value.phase,questions:"
                    "value.questions.filter(row=>row.status==='answered"
                    "').length,\n"
                    "                design:value.planning.design,scena"
                    "rios:value.planning.scenarios,testPlan:value.plann"
                    "ing.test_plan};\n"
                    """            })()"""
                ),
            )
            assert snapshot["phase"] == "final_review", snapshot
            assert snapshot["questions"] >= 1
            assert any("isolates task records" in row["statement"] for row in snapshot["design"])
            assert any(row.get("mock") == "mock" for row in snapshot["scenarios"])
            assert snapshot["testPlan"]["approved"]
            # The UI reaches final review and presents adoption/review controls;
            # it does not authorize or run implementation.
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_chrome_conflict_recovery_keeps_answer_and_archives_rejected_receipt(
    db_path, sample_repo, monkeypatch, tmp_path
):
    ModelFixture.calls = 0
    import threading

    host_probe_started = threading.Event()
    release_host_probe = threading.Event()

    def delayed_host_probe(host):
        host_probe_started.set()
        if not release_host_probe.wait(8):
            raise TimeoutError("test did not release delayed host probe")
        return {"host": host, "available": False, "error": "delayed browser fixture"}

    monkeypatch.setattr("harness.dashboard.authoring_view.availability", delayed_host_probe)
    monkeypatch.setattr(
        "harness.authoring_worker.AuthoringWorker",
        lambda path, root, **kwargs: AuthoringWorker(
            path, root, provider_factory=ModelFixture, **kwargs
        ),
    )
    server = DashboardServer(db_path, None, project_root=sample_repo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    server.poll_seconds = 0.1
    thread.start()
    try:
        with chrome(tmp_path / "chrome-profile", server.url, 1280, 900) as call:
            wait(call, 'Boolean(document.querySelector("#project-start textarea"))')
            evaluate(
                call,
                (
                    """(() => {
              const request=document.querySelector('#project-start textarea');
              request.value='Build a task service that validates and preserves data.';
              request.dispatchEvent(new Event('input',{bubbles:true}));
"""
                    "              document.querySelector('#project-sta"
                    "rt form').dispatchEvent(new Event('submit',{bubble"
                    "s:true,cancelable:true}));\n"
                    """              return true;
            })()"""
                ),
            )
            wait(call, 'location.search.includes("task=")')
            wait(
                call,
                'document.body.innerText.includes("What should happen when the input is empty?")',
            )
            _wait_for_succeeded_generation(call)
            task_id = evaluate(call, "new URLSearchParams(location.search).get('task')")
            evaluate(call, "document.querySelector('#workspace-tab-interview').click(); true")
            question_id = evaluate(
                call,
                (
                    """(() => {
"""
                    "              const card=[...document.querySelecto"
                    "rAll('.authoring-question')].find(node=>node.query"
                    'Selector(\'textarea[aria-label^="Answer question "]'
                    "'));\n"
                    """              return card?.dataset.questionId;
            })()"""
                ),
            )
            assert question_id is not None
            evaluate(
                call,
                (
                    """(() => {
"""
                    "              const card=document.querySelector('."
                    'authoring-question[data-question-id="QUESTION_ID"]'
                    "');\n"
                    "              card.querySelector('textarea[aria-la"
                    "bel^=\"Answer question \"]').value='Keep this answer"
                    " while another client adds context.';\n"
                    "              card.querySelector('textarea[aria-la"
                    'bel^="Answer question "]\').dispatchEvent(new Event'
                    "('input',{bubbles:true}));\n"
                    """              card.querySelector('button[aria-haspopup="dialog"]').click();
              return true;
            })()"""
                ).replace("QUESTION_ID", str(question_id)),
            )
            wait(call, 'document.querySelector(".authoring-drawer")?.open===true')
            wait(
                call,
                (
                    'document.querySelector(".authoring-drawer '
                    'textarea[aria-label^=\\"Answer question \\"]'
                    '")?.value.includes("another client adds co'
                    'ntext")'
                ),
            )
            assert host_probe_started.wait(5), (
                "the authoring panel should request host availability"
            )
            concurrent = evaluate(
                call,
                (
                    """(async () => {
"""
                    "              const latest=await (await fetch('/ap"
                    "i/authoring?task='+TASK_ID,{cache:'no-store'})).js"
                    "on();\n"
                    "              const response=await fetch('/api/aut"
                    "horing/followup',{method:'POST',headers:{'Content-"
                    "Type':'application/json'},body:JSON.stringify({\n"
                    "                task:TASK_ID, expected:latest.base"
                    ", body:'Context committed by another client while "
                    "the answer drawer is open.', request_id:crypto.ran"
                    "domUUID()\n"
                    """              })});
"""
                    "              const value=await response.json(); i"
                    "f(!response.ok) throw new Error(value.error||('HTT"
                    "P '+response.status));\n"
                    "              window.concurrentFollowup=value; ret"
                    "urn {base:(await (await fetch('/api/authoring?task"
                    "='+TASK_ID)).json()).base};\n"
                    """            })()"""
                ).replace("TASK_ID", json.dumps(task_id)),
            )
            assert concurrent["base"]
            wait(
                call,
                (
                    "JSON.stringify(workspaceStates.get(TASK_ID"
                    ").authoring.base)===JSON.stringify(window."
                    "concurrentFollowup.base)"
                ).replace("TASK_ID", json.dumps(task_id)),
            )
            release_host_probe.set()
            wait(
                call,
                "workspaceStates.get(TASK_ID).authoring.hosts?.length===2".replace(
                    "TASK_ID", json.dumps(task_id)
                ),
            )
            latest_state = evaluate(
                call,
                (
                    """(() => {
              const state=workspaceStates.get(TASK_ID).authoring;
"""
                    "              return {base:state.base,hasFollowup:"
                    "state.contributions.some(row=>row.payload?.body==="
                    "'Context committed by another client while the ans"
                    "wer drawer is open.')};\n"
                    """            })()"""
                ).replace("TASK_ID", json.dumps(task_id)),
            )
            assert latest_state["base"] == concurrent["base"] and latest_state["hasFollowup"], (
                latest_state
            )
            evaluate(
                call,
                (
                    """(() => {
"""
                    "              const button=[...document.querySelec"
                    "torAll('.authoring-drawer button')].find(node=>nod"
                    "e.textContent==='Save answer');\n"
                    """              button.click(); return true;
            })()"""
                ),
            )
            wait(
                call,
                'Boolean(document.querySelector(".authoring-drawer .authoring-conflict-recovery"))',
            )
            error_text = evaluate(
                call,
                (
                    'document.querySelector(".authoring-drawer '
                    '.authoring-conflict-recovery").textContent'
                ),
            )
            assert "changed" in error_text.lower() and "keep my text" in error_text.lower()
            evaluate(
                call,
                (
                    """(() => {
"""
                    "              [...document.querySelectorAll('.auth"
                    "oring-drawer button')].find(node=>node.textContent"
                    "==='Refresh latest state and keep my text').click("
                    ");\n"
                    """              return true;
            })()"""
                ),
            )
            wait(
                call,
                'document.querySelector(".authoring-drawer .authoring-conflict-recovery")===null',
            )
            assert (
                evaluate(
                    call,
                    (
                        'document.querySelector(".authoring-drawer '
                        'textarea[aria-label^=\\"Answer question \\"]'
                        '").value'
                    ),
                )
                == "Keep this answer while another client adds context."
            )
            evaluate(
                call,
                (
                    """(() => {
"""
                    "              [...document.querySelectorAll('.auth"
                    "oring-drawer button')].find(node=>node.textContent"
                    "==='Save answer').click();\n"
                    """              return true;
            })()"""
                ),
            )
            wait(
                call,
                "document.querySelector('.authoring-drawer')?.textContent.includes("
                + json.dumps(f"Answer for Q{question_id} saved")
                + ")",
            )
            wait(
                call,
                "(()=>{const state=workspaceStates.get("
                + json.dumps(task_id)
                + ");const current=state.authoring.interview.current_question_id;"
                + "return current!=="
                + json.dumps(int(question_id))
                + "&&Number(document.querySelector('.authoring-drawer .authoring-question')"
                "?.dataset.questionId)===current;})()",
            )
            snapshot = evaluate(
                call,
                (
                    """(async () => {
"""
                    "              const response=await fetch('/api/aut"
                    "horing?task='+TASK_ID,{cache:'no-store'}); const v"
                    "alue=await response.json();\n"
                    """              return {
                questions:value.questions,
                contributions:value.contributions
              };
            })()"""
                ).replace("TASK_ID", json.dumps(task_id)),
            )
            assert (
                next(row for row in snapshot["questions"] if row["id"] == int(question_id))[
                    "answer"
                ]
                == "Keep this answer while another client adds context."
            )
            assert sum(item["kind"] == "followup" for item in snapshot["contributions"]) == 1
            project_id = evaluate(
                call,
                "workspaceStates.get(new URLSearchParams(location.search).get('task')).projectId",
            )
            history = evaluate(
                call,
                (
                    """(() => {
"""
                    "              const value=JSON.parse(localStorage."
                    "getItem(KEY)); return value.authoringRequestHistor"
                    "y;\n"
                    """            })()"""
                ).replace("KEY", json.dumps(f"myharness-workspace:{project_id}:{task_id}")),
            )
            assert history and history[-1]["outcome"] == "rejected-stale-base"
            assert history[-1]["payload"]["request_id"]
    finally:
        release_host_probe.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_chrome_successful_spec_save_preserves_typing_during_delayed_ack(
    db_path, sample_repo, monkeypatch, tmp_path
):
    ModelFixture.calls = 0
    monkeypatch.setattr(
        "harness.authoring_worker.AuthoringWorker",
        lambda path, root, **kwargs: AuthoringWorker(
            path, root, provider_factory=ModelFixture, **kwargs
        ),
    )
    server = DashboardServer(db_path, None, project_root=sample_repo)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    server.poll_seconds = 0.1
    thread.start()
    try:
        with chrome(tmp_path / "chrome-profile", server.url, 1280, 900) as call:
            wait(call, 'Boolean(document.querySelector("#project-start textarea"))')
            evaluate(
                call,
                (
                    """(() => {
              const field=document.querySelector('#project-start textarea');
              field.value='Build a task service that validates before it saves.';
              field.dispatchEvent(new Event('input',{bubbles:true}));
"""
                    "              document.querySelector('#project-sta"
                    "rt form').dispatchEvent(new Event('submit',{bubble"
                    "s:true,cancelable:true}));\n"
                    """              return true;
            })()"""
                ),
            )
            wait(call, 'location.search.includes("task=")')
            _wait_for_succeeded_generation(call)
            task_id = evaluate(call, "new URLSearchParams(location.search).get('task')")
            jobs_before = len(_http_json(server, f"/api/authoring?task={task_id}")[1]["jobs"])
            # Reload after the actual model job settles so the editor's base is
            # the observed current server generation for this successful-save case.
            call("Page.navigate", {"url": evaluate(call, "location.href")})
            wait(call, 'Boolean(document.querySelector(".authoring-statusbar"))')
            evaluate(call, "document.querySelector('#workspace-tab-spec').click(); true")
            wait(call, 'Boolean(document.querySelector(".spec-editor"))')
            evaluate(
                call,
                (
                    """(() => {
              const editor=document.querySelector('[aria-label="Full working specification"]');
              editor.value='## Saved version A\\n\\nThe task validates before it saves.\\n';
              editor.dispatchEvent(new Event('input',{bubbles:true}));
              const priorFetch=window.fetch;
              window.fetch=(url,options)=>{
                if(String(url).includes('/api/authoring/save-spec')&&!window.heldSpecAck){
                  window.heldSpecAck=true;
"""
                    "                  return priorFetch(url,options).t"
                    "hen(response=>new Promise(resolve=>{window.release"
                    "SpecAck=()=>resolve(response)}));\n"
                    """                }
                return priorFetch(url,options);
              };
"""
                    "              [...document.querySelectorAll('butto"
                    "n')].find(node=>node.textContent==='Save working s"
                    "pecification').click();\n"
                    """              return true;
            })()"""
                ),
            )
            wait(call, 'typeof window.releaseSpecAck==="function"')
            committed_a = _http_json(server, f"/api/authoring?task={task_id}")[1]
            assert "Saved version A" in committed_a["working"]["body"]
            evaluate(
                call,
                """(() => {
              const editor=document.querySelector('[aria-label="Full working specification"]');
              editor.value+='\\nA newer edit typed before the successful ' +
                'acknowledgement arrived.\\n';
              editor.dispatchEvent(new Event('input',{bubbles:true}));
              window.releaseSpecAck();
              return true;
            })()""",
            )
            wait(call, 'document.body.innerText.includes("Working specification saved")')
            assert evaluate(
                call,
                (
                    "document.querySelector('[aria-label=\"Full "
                    "working specification\"]').value.includes('"
                    "newer edit typed before the successful ack"
                    "nowledgement')"
                ),
            )
            assert evaluate(
                call,
                'workspaceStates.get(TASK).authoringBuffers["spec:working"].dirty'.replace(
                    "TASK", json.dumps(task_id)
                ),
            )
            original_base = evaluate(
                call,
                "workspaceStates.get(" + json.dumps(task_id) + ").authoringBuffers["
                "'spec:working'].base",
            )
            assert original_base != committed_a["base"]
            _capture_authoring_posts(call)
            evaluate(
                call,
                (
                    "[...document.querySelectorAll('button')].f"
                    "ind(node=>node.textContent==='Save working"
                    " specification').click(); true"
                ),
            )
            wait(
                call,
                "window.__authoringPosts?.some(row=>row.url.includes('/save-spec')&&"
                "row.status===409)",
            )
            stale_save = evaluate(
                call,
                "(() => {const state=workspaceStates.get(" + json.dumps(task_id) + ");"
                "const row=window.__authoringPosts.filter(item=>item.url.includes('/save-spec'))"
                ".at(-1);const buffer=state.authoringBuffers['spec:working'];"
                "return {status:row?.status,body:row?.body,base:buffer.base,dirty:buffer.dirty,"
                "value:buffer.value,serverBase:state.authoring.base};})()",
            )
            assert stale_save["status"] == 409, stale_save
            assert "authoring source or revision changed" in stale_save["body"], stale_save
            assert stale_save["base"] == original_base, stale_save
            assert stale_save["serverBase"] == committed_a["base"], stale_save
            assert stale_save["dirty"] and (
                "newer edit typed before the successful acknowledgement" in stale_save["value"]
            ), stale_save
            evaluate(
                call,
                "[...document.querySelectorAll('button')].find(node=>"
                "node.textContent==='Keep my text and save against the current revision')"
                ".click();true",
            )
            rebased = evaluate(
                call,
                "(() => {const state=workspaceStates.get(" + json.dumps(task_id) + ");"
                "const buffer=state.authoringBuffers['spec:working'];"
                "return {base:buffer.base,serverBase:state.authoring.base,dirty:buffer.dirty,"
                "value:buffer.value};})()",
            )
            assert rebased["base"] == rebased["serverBase"] == committed_a["base"], rebased
            assert rebased["dirty"] and (
                "newer edit typed before the successful acknowledgement" in rebased["value"]
            ), rebased
            evaluate(
                call,
                "[...document.querySelectorAll('button')].find(node=>"
                "node.textContent==='Save working specification').click();true",
            )
            wait(
                call,
                "window.__authoringPosts.filter(row=>row.url.includes('/save-spec')).at(-1)?.status===200",
            )
            wait(
                call,
                (
                    "(async()=>{const value=await(await fetch('"
                    "/api/authoring?task='+encodeURIComponent(n"
                    "ew URLSearchParams(location.search).get('t"
                    "ask')),{cache:'no-store'})).json();return "
                    "value.working.body.includes('newer edit ty"
                    "ped before the successful acknowledgement'"
                    ")})()"
                ),
            )
            saved = _http_json(server, f"/api/authoring?task={task_id}")[1]
            assert (
                "newer edit typed before the successful acknowledgement" in saved["working"]["body"]
            )
            assert saved["working"]["body"] == rebased["value"]
            assert len(saved["jobs"]) == jobs_before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_chrome_loads_and_revises_paginated_older_conversation_history(
    db_path, sample_repo, monkeypatch, tmp_path
):
    ModelFixture.calls = 0
    monkeypatch.setattr(
        "harness.authoring_worker.AuthoringWorker",
        lambda path, root, **kwargs: AuthoringWorker(
            path, root, provider_factory=ModelFixture, **kwargs
        ),
    )
    server = DashboardServer(db_path, None, project_root=sample_repo)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    server.poll_seconds = 0.1
    thread.start()
    try:
        with chrome(tmp_path / "chrome-profile", server.url, 1280, 900) as call:
            wait(call, 'Boolean(document.querySelector("#project-start textarea"))')
            evaluate(
                call,
                (
                    """(() => {
              const field=document.querySelector('#project-start textarea');
              field.value='Build an interview service with durable conversation history.';
              field.dispatchEvent(new Event('input',{bubbles:true}));
"""
                    "              document.querySelector('#project-sta"
                    "rt form').dispatchEvent(new Event('submit',{bubble"
                    "s:true,cancelable:true}));\n"
                    """              return true;
            })()"""
                ),
            )
            wait(call, 'location.search.includes("task=")')
            _wait_for_succeeded_generation(call)
            task_id = evaluate(call, "new URLSearchParams(location.search).get('task')")
            snapshot = _http_json(server, f"/api/authoring?task={task_id}")[1]
            base = snapshot["base"]
            for index in range(207):
                body = f"Saved older conversation {index:03d}."
                status, result = _http_json(
                    server,
                    "/api/authoring/followup",
                    method="POST",
                    body={
                        "task": task_id,
                        "expected": base,
                        "body": body,
                        "request_id": str(uuid.uuid4()),
                    },
                )
                assert status == 200, result
                base = result["base"]
            latest = _http_json(server, f"/api/authoring?task={task_id}")[1]
            assert latest["history_truncated"] is True
            assert len(latest["contributions"]) <= 200
            assert _http_json(server, f"/api/authoring/history?task={task_id}&limit=200")[1][
                "next_before"
            ]
            call("Page.navigate", {"url": f"{server.url.rstrip('/')}/?task={task_id}&tab=history"})
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not evaluate(
                call, 'Boolean(document.querySelector(".authoring-statusbar"))'
            ):
                time.sleep(0.1)
            assert evaluate(call, 'Boolean(document.querySelector(".authoring-statusbar"))'), (
                evaluate(call, "document.body.innerText.slice(0,1000)")
            )
            evaluate(call, "document.querySelector('#workspace-tab-history').click(); true")
            wait(call, 'Boolean(document.querySelector(".authoring-conversation-history"))')
            evaluate(
                call,
                (
                    "[...document.querySelectorAll('.authoring-"
                    "conversation-history button')].find(node=>"
                    "node.textContent==='Load earlier conversat"
                    "ion').click(); true"
                ),
            )
            wait(call, 'document.body.innerText.includes("Saved older conversation 000.")')
            record = evaluate(
                call,
                (
                    "[...document.querySelectorAll('.authoring-"
                    "message')].find(node=>node.textContent.inc"
                    "ludes('Saved older conversation 000.'))?.t"
                    "extContent||''"
                ),
            )
            assert record
            evaluate(
                call,
                (
                    """(() => {
"""
                    "              const card=[...document.querySelecto"
                    "rAll('.authoring-message')].find(node=>node.textCo"
                    "ntent.includes('Saved older conversation 000.'));\n"
                    "              const details=card.querySelector('de"
                    "tails'); if(!details) throw new Error('older contr"
                    "ibution has no revision control');\n"
                    """              details.open=true;
              const field=details.querySelector('textarea');
              field.value='Revised older conversation remains in history.';
              field.dispatchEvent(new Event('input',{bubbles:true}));
"""
                    "              [...details.querySelectorAll('button"
                    "')].find(node=>node.textContent==='Save revision t"
                    "o history').click();\n"
                    """              return true;
            })()"""
                ),
            )
            wait(
                call,
                (
                    'document.body.innerText.includes("Revised '
                    'older conversation remains in history.")'
                ),
            )
            revised = _http_json(server, f"/api/authoring?task={task_id}")[1]
            assert any(
                row["kind"] == "revision"
                and row["payload"].get("body") == "Revised older conversation remains in history."
                for row in revised["contributions"]
            )
            call("Page.navigate", {"url": evaluate(call, "location.href")})
            wait(call, 'Boolean(document.querySelector(".authoring-conversation-history"))')
            wait(call, 'document.body.innerText.includes("Saved older conversation 000.")')
            assert evaluate(
                call,
                (
                    'document.body.innerText.includes("Revised '
                    'older conversation remains in history.")'
                ),
            )
            original_has_revision_control = evaluate(
                call,
                (
                    "(() => { const card=[...document.querySele"
                    "ctorAll('.authoring-message')].find(node=>"
                    "node.textContent.includes('Saved older con"
                    "versation 000.')); return Boolean(card?.qu"
                    "erySelector('details')); })()"
                ),
            )
            assert not original_has_revision_control
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_chrome_legacy_spec_edit_does_not_require_an_available_host(
    controller, db_path, sample_repo, monkeypatch, tmp_path
):
    from harness.approval import ApprovalGate
    from harness.spec_store import SpecStore
    from tests.complete_task import settle
    from tests.execution_helpers import isolated_launch_environment

    browser_home = os.environ.get("HOME")
    isolated_launch_environment(monkeypatch, sample_repo.parent / "launch-fixture")
    monkeypatch.chdir(sample_repo)
    task_id = controller.create_task(
        "Maintain an existing approved task without an Interview model", "trivial"
    ).id
    settle(controller, task_id)
    controller.advance(task_id)
    ApprovalGate(
        controller, task_id, SpecStore(sample_repo / "docs/specs" / task_id), sample_repo
    ).approve()
    monkeypatch.setattr(
        "harness.dashboard.authoring_view.availability",
        lambda host: {"host": host, "available": False, "error": "unavailable in browser fixture"},
    )
    server = DashboardServer(db_path, None, project_root=sample_repo)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    server.poll_seconds = 0.1
    thread.start()
    try:
        browser_env = os.environ.copy()
        if browser_home:
            browser_env["HOME"] = browser_home
        with chrome(
            tmp_path / "chrome-profile",
            f"{server.url.rstrip('/')}/?task={task_id}&tab=spec",
            1280,
            900,
            process_env=browser_env,
        ) as call:
            wait(call, 'Boolean(document.querySelector(".spec-editor"))')
            hosts = evaluate(
                call,
                (
                    """(async () => {
"""
                    "              const value=await (await fetch('/api"
                    "/authoring?task='+new URLSearchParams(location.sea"
                    "rch).get('task')+'&hosts=1')).json();\n"
                    """              return value.hosts;
            })()"""
                ),
            )
            assert hosts and all(not row["available"] for row in hosts), hosts
            before = evaluate(
                call,
                (
                    """(async () => {
"""
                    "              const value=await (await fetch('/api"
                    "/authoring?task='+new URLSearchParams(location.sea"
                    "rch).get('task'))).json();\n"
                    "              return {base:value.base,approved:val"
                    "ue.approved,effective:value.effective_body,jobs:va"
                    "lue.jobs};\n"
                    """            })()"""
                ),
            )
            assert not before["jobs"]
            approvals_before = controller.conn.execute(
                "SELECT * FROM approvals WHERE task_id=?", (task_id,)
            ).fetchall()
            spec_path = (
                sample_repo / "docs/specs" / task_id / f"v{before['approved']['version']}.md"
            )
            spec_bytes_before = spec_path.read_bytes()
            evaluate(
                call,
                (
                    """(() => {
              const editor=document.querySelector('[aria-label="Full working specification"]');
              editor.value=`## Existing task specification

Validate inputs before saving records.
`;
              editor.dispatchEvent(new Event('input',{bubbles:true}));
"""
                    "              const save=[...document.querySelecto"
                    "rAll('button')].find(node=>node.textContent==='Sav"
                    "e working specification');\n"
                    """              if(save.disabled) {
                throw new Error('manual save is disabled for an existing task');
              }
              save.click(); return true;
            })()"""
                ),
            )
            wait(call, 'document.body.innerText.includes("Working specification saved")')
            after = evaluate(
                call,
                (
                    """(async () => {
"""
                    "              const value=await (await fetch('/api"
                    "/authoring?task='+new URLSearchParams(location.sea"
                    "rch).get('task'),{cache:'no-store'})).json();\n"
                    "              return {working:value.working,approv"
                    "ed:value.approved,effective:value.effective_body,j"
                    "obs:value.jobs};\n"
                    """            })()"""
                ),
            )
            assert after["working"]["body"].startswith("## Existing task specification")
            assert not after["jobs"]
            assert after["effective"] == before["effective"]
            assert (
                controller.conn.execute(
                    "SELECT * FROM approvals WHERE task_id=?", (task_id,)
                ).fetchall()
                == approvals_before
            )
            assert spec_path.read_bytes() == spec_bytes_before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _start_rework1_fixture_task(call, request):
    wait(call, 'Boolean(document.querySelector("#project-start textarea"))')
    evaluate(
        call,
        "(() => {const field=document.querySelector('#project-start textarea');"
        + "field.value="
        + json.dumps(request)
        + ";"
        + "field.dispatchEvent(new Event('input',{bubbles:true}));"
        + "document.querySelector('#project-start form').dispatchEvent("
        + "new Event('submit',{bubbles:true,cancelable:true}));return true;})()",
    )
    wait(call, 'location.search.includes("task=")')
    task_id = evaluate(call, "new URLSearchParams(location.search).get('task')")
    wait(
        call,
        "(async()=>{const value=await(await fetch('/api/authoring?task='+"
        "encodeURIComponent(new URLSearchParams(location.search).get('task'))))"
        ".json();return value.questions?.some(row=>row.text==='What should happen "
        "when the input is empty?')})()",
    )
    _wait_for_succeeded_generation(call)
    return task_id


def test_chrome_rework1_buffers_preserve_structured_forms_across_tabs_and_reload(
    db_path, sample_repo, monkeypatch, tmp_path
):
    ModelFixture.calls = 0
    monkeypatch.setattr(
        "harness.authoring_worker.AuthoringWorker",
        lambda path, root, **kwargs: AuthoringWorker(
            path, root, provider_factory=ModelFixture, **kwargs
        ),
    )
    server = DashboardServer(db_path, None, project_root=sample_repo)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    server.poll_seconds = 0.1
    thread.start()
    try:
        with chrome(tmp_path / "chrome-profile", server.url, 1280, 900) as call:
            _start_rework1_fixture_task(call, "Build a task service with reviewable validation.")
            task_id = evaluate(call, "new URLSearchParams(location.search).get('task')")
            evaluate(call, "document.querySelector('#workspace-tab-interview').click(); true")
            wait(call, 'Boolean(document.querySelector(".authoring-question"))')
            wait(
                call,
                "(async()=>{const value=await(await fetch('/api/authoring?task='+"
                "encodeURIComponent(new URLSearchParams(location.search).get('task'))))"
                ".json();return value.recommendations?.length>0})()",
            )
            proposal_title = evaluate(
                call,
                "(async()=>{const value=await(await fetch('/api/authoring?task='+"
                "encodeURIComponent(new URLSearchParams(location.search).get('task'))))"
                ".json();return value.recommendations[0].title})()",
            )
            response_label = f"Response to {proposal_title}"
            response_selector = f"[aria-label={json.dumps(response_label)}]"
            wait(call, f"Boolean(document.querySelector({json.dumps(response_selector)}))")
            evaluate(
                call,
                "(() => {const field=document.querySelector("
                + json.dumps(response_selector)
                + ");if(!field)throw new Error('recommendation response form missing');"
                "field.value='Reject because this task must preserve valid data.';"
                "field.dispatchEvent(new Event('input',{bubbles:true}));"
                "const host=document.querySelector('[aria-label=\"Model for the next response\"]');"
                "host.value='claude';host.dispatchEvent(new Event('change',{bubbles:true}));"
                "return true;})()",
            )
            focus_before = _http_json(server, f"/api/authoring?task={task_id}")[1]
            focus_response = "A"
            evaluate(
                call,
                "(() => {const field=document.querySelector("
                + json.dumps(response_selector)
                + ");field.value='A';field.focus();field.setSelectionRange(1,1);"
                "field.dispatchEvent(new Event('input',{bubbles:true}));return true;})()",
            )
            status, first_result = _http_json(
                server,
                "/api/authoring/followup",
                method="POST",
                body={
                    "task": task_id,
                    "expected": focus_before["base"],
                    "request_id": str(uuid.uuid4()),
                    "body": "A live update while a structured response is focused.",
                },
            )
            assert status == 200
            wait(
                call,
                "document.body.innerText.includes("
                '"A live update while a structured response is focused.")',
            )
            active_form = evaluate(
                call,
                "(() => {const field=document.querySelector("
                + json.dumps(response_selector)
                + ");return {active:document.activeElement===field,value:field?.value,"
                "start:field?.selectionStart};})()",
            )
            assert active_form == {"active": True, "value": focus_response, "start": 1}
            evaluate(
                call,
                "(() => {const field=document.querySelector(" + json.dumps(response_selector) + ");"
                "field.value='A persisted before tab change';"
                "field.dispatchEvent(new Event('input',{bubbles:true}));"
                "document.querySelector('#workspace-tab-requirements').click();return true;})()",
            )
            wait(
                call,
                'document.querySelector("#workspace-tab-requirements")?.getAttribute('
                '"aria-selected")==="true"',
            )
            evaluate(
                call,
                "(() => {document.querySelector('#workspace-tab-interview').click();"
                "const field=document.querySelector(" + json.dumps(response_selector) + ");"
                "field.value='B newer than serialized workspace state';"
                "field.dispatchEvent(new Event('input',{bubbles:true}));return location.href;})()",
            )
            url = evaluate(call, "location.href")
            call("Page.navigate", {"url": url})
            wait(
                call,
                "document.querySelector("
                + json.dumps(response_selector)
                + ')?.value==="B newer than serialized workspace state"',
            )
            evaluate(
                call,
                "(() => {const field=document.querySelector(" + json.dumps(response_selector) + ");"
                "field.value='Reject because this task must preserve valid data.';"
                "field.dispatchEvent(new Event('input',{bubbles:true}));return true;})()",
            )
            evaluate(call, "document.querySelector('#workspace-tab-requirements').click(); true")
            wait(
                call,
                'document.querySelector("#workspace-tab-requirements")?.getAttribute('
                '"aria-selected")==="true"',
            )
            evaluate(
                call,
                """(() => {
                  const topic=document.querySelector('[aria-label="Question topic"]');
                  topic.value='Recovery policy';
                  topic.dispatchEvent(new Event('input',{bubbles:true}));
                  const wording=document.querySelector('[aria-label="Question wording"]');
                  wording.value='How should a recovered task be shown?';
                  wording.dispatchEvent(new Event('input',{bubbles:true}));
                  const decision=document.querySelector(
                    '[aria-label="Developer decision required"]');
                  decision.checked=true;
                  decision.dispatchEvent(new Event('change',{bubbles:true}));
                  const phase=document.querySelector('[aria-label="Earlier phase"]');
                  phase.value='design';
                  phase.dispatchEvent(new Event('change',{bubbles:true}));
                  const reason=document.querySelector('[aria-label="Reason for returning"]');
                  reason.value='Clarify the durable recovery behavior.';
                  reason.dispatchEvent(new Event('input',{bubbles:true}));
                  return true;
                })()""",
            )
            url = evaluate(call, "location.href")
            call("Page.navigate", {"url": url})
            wait(
                call,
                'document.querySelector("#workspace-tab-requirements")?.getAttribute('
                '"aria-selected")==="true"',
            )
            values = evaluate(
                call,
                """(() => ({
                  topic:document.querySelector('[aria-label="Question topic"]')?.value,
                  wording:document.querySelector('[aria-label="Question wording"]')?.value,
                  decision:document.querySelector(
                    '[aria-label="Developer decision required"]')?.checked,
                  phase:document.querySelector('[aria-label="Earlier phase"]')?.value,
                  reason:document.querySelector('[aria-label="Reason for returning"]')?.value
                }))()""",
            )
            assert values == {
                "topic": "Recovery policy",
                "wording": "How should a recovered task be shown?",
                "decision": True,
                "phase": "design",
                "reason": "Clarify the durable recovery behavior.",
            }
            evaluate(call, "document.querySelector('#workspace-tab-interview').click(); true")
            wait(
                call,
                'document.querySelector("#workspace-tab-interview")?.getAttribute('
                '"aria-selected")==="true"',
            )
            assert (
                evaluate(
                    call,
                    "document.querySelector(" + json.dumps(response_selector) + ")?.value",
                )
                == "Reject because this task must preserve valid data."
            )
            assert (
                evaluate(
                    call,
                    "document.querySelector('[aria-label=\\\"Model for the next "
                    "response\\\"]')?.value",
                )
                == "claude"
            )
            assert "Selected response host: claude" in evaluate(
                call,
                "document.querySelector('[aria-label=\"Model for the next response\"]')"
                ".closest('.authoring-card').textContent",
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_chrome_rework1_revisions_apply_successive_current_and_retired_targets(
    db_path, sample_repo, monkeypatch, tmp_path
):
    ModelFixture.calls = 0
    monkeypatch.setattr(
        "harness.authoring_worker.AuthoringWorker",
        lambda path, root, **kwargs: AuthoringWorker(
            path, root, provider_factory=ModelFixture, **kwargs
        ),
    )
    server = DashboardServer(db_path, None, project_root=sample_repo)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    server.poll_seconds = 0.1
    thread.start()
    try:
        with chrome(tmp_path / "chrome-profile", server.url, 1280, 900) as call:
            artifact_dir = Path(__file__).parents[1] / "../interview-ui-browser-artifacts"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            task_id = _start_rework1_fixture_task(
                call, "Build a task service with explicit response history."
            )
            _set_theme(call, "light")
            _capture_authoring_posts(call)
            evaluate(call, "document.querySelector('#workspace-tab-interview').click(); true")
            wait(call, 'Boolean(document.querySelector(".authoring-question"))')
            question_id = evaluate(
                call,
                "document.querySelector('.authoring-question').dataset.questionId",
            )
            wait(
                call,
                "(async()=>{const task="
                + json.dumps(task_id)
                + ";const current=await(await fetch('/api/authoring?task='+"
                "encodeURIComponent(task),{cache:'no-store'})).json();"
                "const state=workspaceStates.get(task);"
                "const buffer=state.authoringBuffers['answer:'+"
                + json.dumps(str(question_id))
                + "];const base=JSON.stringify(current.base);"
                "return JSON.stringify(state.authoring?.base)===base&&"
                "JSON.stringify(buffer.base)===base&&!buffer.dirty})()",
            )
            evaluate(
                call,
                "(() => {const card=document.querySelector('.authoring-question[data-question-id="
                + json.dumps(str(question_id))
                + "]');const field=card.querySelector('[aria-label^=\"Answer question\"]');"
                "field.value='The service rejects empty values and keeps valid input.';"
                "field.dispatchEvent(new Event('input',{bubbles:true}));"
                "[...card.querySelectorAll('button')].find(item=>item.textContent==="
                "'Save answer').click();return true;})()",
            )
            answer_posted = (
                "window.__authoringPosts.some(item=>item.url.includes('/answer')"
                "&&item.status===200)"
            )
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not evaluate(call, answer_posted):
                time.sleep(0.05)
            assert evaluate(call, answer_posted), evaluate(
                call, "JSON.stringify(window.__authoringPosts)"
            )
            answer_state = _http_json(server, f"/api/authoring?task={task_id}")[1]
            assert any(
                row["id"] == int(question_id)
                and row["answer"] == "The service rejects empty values and keeps valid input."
                for row in answer_state["questions"]
            )
            current_id = int(question_id)
            for correction in (
                "The service rejects empty values and preserves corrected input.",
                "The service rejects empty values and preserves all valid input.",
            ):
                question_target = f"question-response:{current_id}"
                card_selector = f'[data-revision-target="{question_target}"]'
                wait(call, f"Boolean(document.querySelector({json.dumps(card_selector)}))")
                evaluate(
                    call,
                    "(() => {const card=document.querySelector(" + json.dumps(card_selector) + ");"
                    "const mode=card.querySelector('[aria-label=\"Response type\"]');"
                    "mode.value='defer';mode.dispatchEvent(new Event('change',{bubbles:true}));"
                    "if(!card.querySelector('[aria-label=\"Deferral reason or reference\"]'))"
                    "throw new Error('deferral controls did not update for selected mode');"
                    "mode.value='answer';mode.dispatchEvent(new Event('change',{bubbles:true}));"
                    "const field=card.querySelector('[aria-label=\"Corrected answer\"]');"
                    "field.value=" + json.dumps(correction) + ";"
                    "field.dispatchEvent(new Event('input',{bubbles:true}));"
                    "const save=[...card.querySelectorAll('button')].find(item=>item.textContent==="
                    "'Save correction to history');if(save.disabled)"
                    "throw new Error('save stayed disabled after edit');"
                    "save.click();return true;})()",
                )
                wait(call, 'document.body.innerText.includes("A correction is saved in history")')
                evaluate(
                    call,
                    "[...document.querySelectorAll(" + json.dumps(card_selector + " button") + ")]"
                    ".find(item=>item.textContent==='Apply this saved correction').click(); true",
                )
                wait(
                    call,
                    "(async()=>{const value=await(await fetch('/api/authoring?task='+"
                    + json.dumps(task_id)
                    + ")).json();return value.questions.some(row=>row.answer==="
                    + json.dumps(correction)
                    + ")})()",
                )
                snapshot = _http_json(server, f"/api/authoring?task={task_id}")[1]
                current_question = next(
                    row for row in snapshot["questions"] if row["answer"] == correction
                )
                assert current_question["id"] != current_id
                current_id = current_question["id"]
            snapshot = _http_json(server, f"/api/authoring?task={task_id}")[1]
            assert any(
                item["id"] == int(question_id) for item in snapshot["historical"]["questions"]
            )
            assert any(
                item["target"] == f"question-response:{question_id}" and not item["editable"]
                for item in snapshot["revision_targets"]
            )
            call("Page.navigate", {"url": evaluate(call, "location.href")})
            wait(call, 'Boolean(document.querySelector(".authoring-revision-target"))')
            wait(call, 'document.documentElement.dataset.theme==="light"')
            assert evaluate(call, "document.body.innerText.includes('This is a retired record')")
            assert evaluate(
                call, "Boolean(document.querySelector('[aria-label=\"Corrected answer\"]'))"
            )
            current_target_selector = f'[data-revision-target="question-response:{current_id}"]'
            evaluate(
                call,
                "document.querySelector("
                + json.dumps(current_target_selector)
                + ").scrollIntoView({block:'center'});true",
            )
            _capture(call, artifact_dir / "revision-controls-desktop-current-light.png")
            evaluate(
                call,
                "[...document.querySelectorAll('.authoring-revision-target')].find(item=>"
                "item.textContent.includes('This is a retired record'))"
                ".scrollIntoView({block:'center'});true",
            )
            _capture(call, artifact_dir / "revision-controls-desktop-retired-light.png")
            call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
            )
            wait(call, "window.innerWidth===390")
            evaluate(
                call,
                "document.querySelector("
                + json.dumps(current_target_selector)
                + ").scrollIntoView({block:'center'});true",
            )
            _capture(call, artifact_dir / "revision-controls-mobile-current-light.png")
            evaluate(
                call,
                "[...document.querySelectorAll('.authoring-revision-target')].find(item=>"
                "item.textContent.includes('This is a retired record'))"
                ".scrollIntoView({block:'center'});true",
            )
            _capture(call, artifact_dir / "revision-controls-mobile-retired-light.png")
            call("Emulation.clearDeviceMetricsOverride")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_chrome_rework1_revisions_cover_legacy_records_and_later_phase_isolation(
    controller, db_path, sample_repo, monkeypatch, tmp_path
):
    from harness.cli_decision import registry_for
    from harness.interview import InterviewEngine
    from harness.investigation import InvestigationReport
    from harness.state_models import ProposalStatus, SkipSource
    from tests.complete_task import settle

    ModelFixture.calls = 0
    task = controller.create_task("Review existing task records through the UI", "trivial")
    controller.record_investigation(
        task.id,
        InvestigationReport(str(sample_repo), None, task.request, (), (), (), ()),
    )
    controller.advance(task.id)
    registry = registry_for(controller, task.id, str(sample_repo / "docs" / "decisions"))
    engine = InterviewEngine(controller, task.id, registry)
    answered = engine.ask("Legacy answer", "What is the existing answer?", decision=False)
    engine.answer(answered.id, "Legacy answer stays effective until a correction is applied.")
    deferred = engine.ask("Legacy deferral", "What is the existing deferral?", decision=False)
    engine.skip(deferred.id, SkipSource.CODEBASE, "Legacy codebase investigation is pending.")
    decision_question = engine.ask(
        "Legacy decision", "Should the existing behavior be retained?", decision=True
    )
    proposal = engine.propose(
        decision_question.id,
        "Retain existing behavior",
        "Keep the prior validated behavior.",
        "The task already depends on this behavior.",
        "Changes could break existing records.",
    )
    engine.decide(proposal.id, ProposalStatus.ACCEPTED, None)
    settle(controller, task.id)
    assert str(controller.get_task(task.id).phase) == "final_review"

    monkeypatch.setattr(
        "harness.authoring_worker.AuthoringWorker",
        lambda path, root, **kwargs: AuthoringWorker(
            path, root, provider_factory=ModelFixture, **kwargs
        ),
    )
    server = DashboardServer(db_path, None, project_root=sample_repo)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    server.poll_seconds = 0.1
    thread.start()
    try:
        with chrome(
            tmp_path / "chrome-profile",
            f"{server.url.rstrip('/')}/?task={task.id}&tab=interview",
            1280,
            900,
        ) as call:
            wait(
                call,
                "Boolean([...document.querySelectorAll('button')].find(item=>"
                "item.textContent==='Start / resume interview'))",
            )
            evaluate(
                call,
                "[...document.querySelectorAll('button')]"
                ".find(item=>item.textContent==='Start / resume interview').click();true",
            )
            _wait_for_succeeded_generation(call)
            _capture_authoring_posts(call)
            initial = _http_json(server, f"/api/authoring?task={task.id}")[1]
            targets = {item["target"]: item for item in initial["revision_targets"]}
            answer_target = f"question-response:{answered.id}"
            deferral_target = f"question-response:{deferred.id}"
            decision_target = f"recommendation:{proposal.id}"
            assert targets[answer_target]["original"]["answer"].startswith("Legacy answer")
            assert targets[deferral_target]["original"]["skip_source"] == "codebase"
            assert targets[decision_target]["original"]["status"] == "accepted"
            assert targets[decision_target]["provenance"]

            changes = (
                (
                    answer_target,
                    "defer",
                    {"source": "codebase", "ref": "Recheck the stored response."},
                ),
                (deferral_target, "unknown", {}),
            )
            for target, mode, fields in changes:
                selector = f'[data-revision-target="{target}"]'
                wait(call, f"Boolean(document.querySelector({json.dumps(selector)}))")
                buffer_key = f"record-revision:{target}"
                _wait_for_authoring_form_baseline(call, task.id, target, buffer_key)
                evaluate(
                    call,
                    "(() => {const card=document.querySelector(" + json.dumps(selector) + ");"
                    "const mode=card.querySelector('[aria-label=\"Response type\"]');"
                    "mode.value=" + json.dumps(mode) + ";"
                    "mode.dispatchEvent(new Event('change',{bubbles:true}));return true;})()",
                )
                for label, value in fields.items():
                    field_label = (
                        "Deferral type" if label == "source" else "Deferral reason or reference"
                    )
                    event_type = "change" if label == "source" else "input"
                    evaluate(
                        call,
                        "(() => {const field=document.querySelector("
                        + json.dumps(selector)
                        + ");const input=field.querySelector("
                        + json.dumps(f'[aria-label="{field_label}"]')
                        + ");input.value="
                        + json.dumps(value)
                        + ";"
                        "input.dispatchEvent(new Event("
                        + json.dumps(event_type)
                        + ",{bubbles:true}));return true;})()",
                    )
                if mode == "unknown":
                    assert not evaluate(
                        call,
                        "Boolean(document.querySelector("
                        + json.dumps(selector + ' [aria-label="Deferral reason or reference"]')
                        + "))",
                    )
                evaluate(
                    call,
                    "(() => {const card=document.querySelector(" + json.dumps(selector) + ");"
                    "const save=[...card.querySelectorAll('button')].find(item=>"
                    "item.textContent==='Save correction to history');"
                    "if(save.disabled)throw new Error('legacy correction save stayed disabled');"
                    "save.click();return true;})()",
                )
                saved_message = (
                    f"document.querySelector({json.dumps(selector)})?.innerText.includes("
                    '"A correction is saved in history")'
                )
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline and not evaluate(call, saved_message):
                    time.sleep(0.05)
                if not evaluate(call, saved_message):
                    diagnostic = evaluate(
                        call,
                        "(async()=>{const task="
                        + json.dumps(task.id)
                        + ";const targetId="
                        + json.dumps(target)
                        + ";const key="
                        + json.dumps(buffer_key)
                        + ";const response=await fetch('/api/authoring?task='"
                        "+encodeURIComponent(task),"
                        "{cache:'no-store'});const snapshot=await response.json();"
                        "const target=snapshot.revision_targets.find(row=>row.target===targetId);"
                        "const storageKey='myharness-authoring:'"
                        "+snapshot.project.id+':'+task+':'+key;"
                        "const stored=JSON.parse(localStorage.getItem(storageKey));"
                        "const item=window.__authoringPosts.at(-1);"
                        "return {status:item?.status,body:item?.body,request:item?.request,"
                        "buffer:{base:stored?.base,token:stored?.token,dirty:stored?.dirty,"
                        "generation:stored?.generation,value:stored?.value},"
                        "server:{base:snapshot.base,targetToken:target?.revision,"
                        "targetStatus:target?.status}};})()",
                    )
                    detail = json.dumps(diagnostic, sort_keys=True)
                    pytest.fail(f"legacy revision save did not complete: {detail}")

            decision_selector = f'[data-revision-target="{decision_target}"]'
            wait(call, f"Boolean(document.querySelector({json.dumps(decision_selector)}))")
            _wait_for_authoring_form_baseline(
                call, task.id, decision_target, f"record-revision:{decision_target}"
            )
            evaluate(
                call,
                "(() => {const card=document.querySelector(" + json.dumps(decision_selector) + ");"
                "const status=card.querySelector('[aria-label=\"Recommendation decision\"]');"
                "status.value='rejected';status.dispatchEvent(new Event('change',{bubbles:true}));"
                "const save=[...card.querySelectorAll('button')].find(item=>"
                "item.textContent==='Save correction to history');"
                "if(save.disabled)throw new Error('legacy decision save stayed disabled');"
                "save.click();return true;})()",
            )
            wait(
                call,
                f"document.querySelector({json.dumps(decision_selector)})?.innerText.includes("
                '"A correction is saved in history")',
            )
            pending = _http_json(server, f"/api/authoring?task={task.id}")[1]
            current = {item["target"]: item for item in pending["revision_targets"]}
            assert pending["phase"] == "final_review"
            assert current[answer_target]["status"] == "pending"
            assert current[answer_target]["effective"]["mode"] == "defer"
            assert current[deferral_target]["status"] == "pending"
            assert current[deferral_target]["effective"]["mode"] == "unknown"
            assert current[decision_target]["status"] == "pending"
            assert current[decision_target]["effective"]["status"] == "rejected"
            assert current[decision_target]["provenance"]
            current_answer = next(row for row in pending["questions"] if row["id"] == answered.id)
            current_deferral = next(row for row in pending["questions"] if row["id"] == deferred.id)
            current_decision = next(
                row for row in pending["recommendations"] if row["id"] == proposal.id
            )
            assert current_answer["answer"] == (
                "Legacy answer stays effective until a correction is applied."
            )
            assert current_deferral["skip_source"] == "codebase"
            assert current_deferral["skip_reason"] == ("Legacy codebase investigation is pending.")
            assert current_decision["status"] == "accepted"
            duplicate_answers = [
                row
                for row in pending["questions"]
                if row["id"] != answered.id and row["text"] == answered.text
            ]
            assert not duplicate_answers
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_chrome_rework1_async_drawer_sse_conflict_and_successful_pending_typing(
    db_path, sample_repo, monkeypatch, tmp_path
):
    ModelFixture.calls = 0
    monkeypatch.setattr(
        "harness.authoring_worker.AuthoringWorker",
        lambda path, root, **kwargs: AuthoringWorker(
            path, root, provider_factory=ModelFixture, **kwargs
        ),
    )
    server = DashboardServer(db_path, None, project_root=sample_repo)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    server.poll_seconds = 0.1
    thread.start()
    try:
        with chrome(tmp_path / "chrome-profile", server.url, 1280, 900) as call:
            task_id = _start_rework1_fixture_task(
                call, "Build a task service with reviewable answers."
            )
            evaluate(call, "document.querySelector('#workspace-tab-interview').click(); true")
            wait(call, 'Boolean(document.querySelector(".authoring-question"))')
            question_id = evaluate(
                call,
                "document.querySelector('.authoring-question').dataset.questionId",
            )
            evaluate(
                call,
                "[...document.querySelectorAll('.authoring-question button')]"
                ".find(item=>item.textContent.includes('in drawer')).click(); true",
            )
            wait(call, 'document.querySelector(".authoring-drawer")?.open===true')
            evaluate(
                call,
                """(() => {
                  const field=document.querySelector(
                    '.authoring-drawer [aria-label^="Answer question"]');
                  field.value='Draft retained through server refresh.';
                  field.focus();
                  field.dispatchEvent(new Event('input',{bubbles:true}));
                  field.setSelectionRange(field.value.length,field.value.length);
                  window.__drawerFocusProbe={showModal:[],before:{
                    key:field.dataset.workspaceFocus,active:document.activeElement===field,
                    value:field.value,start:field.selectionStart,end:field.selectionEnd,
                    dialogOpen:field.closest('dialog')?.open||false,
                    drawerOpen:workspaceStates.get(
                      new URLSearchParams(location.search).get('task'))?.drawerOpen}};
                  window.__drawerFocusProbe.queued=[];
                  window.__drawerFocusProbe.stream=source;
                  window.__drawerFocusProbe.handler=source.onmessage;
                  source.onmessage=function(event){window.__drawerFocusProbe.queued.push(event);};
                  const nativeShowModal=HTMLDialogElement.prototype.showModal;
                  HTMLDialogElement.prototype.showModal=function(){
                    window.__drawerFocusProbe.showModal.push({connected:this.isConnected,
                      task:new URLSearchParams(location.search).get('task'),at:performance.now()});
                    return nativeShowModal.call(this);
                  };
                  return true;
                })()""",
            )
            before = _http_json(server, f"/api/authoring?task={task_id}")[1]
            status, first_result = _http_json(
                server,
                "/api/authoring/followup",
                method="POST",
                body={
                    "task": task_id,
                    "expected": before["base"],
                    "request_id": str(uuid.uuid4()),
                    "body": "SSE context added while the answer drawer is open.",
                },
            )
            assert status == 200, first_result
            first_frame_condition = (
                "(()=>{const task="
                + json.dumps(task_id)
                + ";const first="
                + json.dumps(first_result["contribution"])
                + ";return window.__drawerFocusProbe.queued.some(event=>{"
                "const snapshot=JSON.parse(event.data);const authoring=snapshot.task?.authoring;"
                "return snapshot.task?.id===task&&authoring?.contributions?.some(item=>"
                "item.id===first);});})()"
            )
            wait(call, first_frame_condition)
            latest_after_first_followup = _http_json(server, f"/api/authoring?task={task_id}")[1]
            second_status, second_result = _http_json(
                server,
                "/api/authoring/followup",
                method="POST",
                body={
                    "task": task_id,
                    "expected": latest_after_first_followup["base"],
                    "request_id": str(uuid.uuid4()),
                    "body": "A second queued server update while the answer drawer is open.",
                },
            )
            assert second_status == 200, second_result
            latest_after_followup = _http_json(server, f"/api/authoring?task={task_id}")[1]
            second_frame_condition = (
                "(()=>{const task="
                + json.dumps(task_id)
                + ";const first="
                + json.dumps(first_result["contribution"])
                + ";const second="
                + json.dumps(second_result["contribution"])
                + ";return window.__drawerFocusProbe.queued.some(event=>{"
                "const snapshot=JSON.parse(event.data);const authoring=snapshot.task?.authoring;"
                "const ids=new Set(authoring?.contributions?.map(item=>item.id)||[]);"
                "return snapshot.task?.id===task&&ids.has(first)&&ids.has(second);});})()"
            )
            wait(call, second_frame_condition)
            burst = evaluate(
                call,
                "(() => {const probe=window.__drawerFocusProbe;const queued=probe.queued.splice(0);"
                "const snapshots=queued.map(event=>JSON.parse(event.data));"
                "probe.stream.onmessage=probe.handler;"
                "for(const event of queued)probe.handler.call(probe.stream,event);"
                "return snapshots.map(snapshot=>({task:snapshot.task?.id,"
                "base:snapshot.task?.authoring?.base,"
                "ids:(snapshot.task?.authoring?.contributions||[]).map(item=>item.id)}));})()",
            )
            assert len(burst) >= 2, burst
            assert all(snapshot["task"] == task_id for snapshot in burst), burst
            first_frame = next(
                (
                    snapshot
                    for snapshot in burst
                    if first_result["contribution"] in snapshot["ids"]
                    and second_result["contribution"] not in snapshot["ids"]
                ),
                None,
            )
            second_frame = next(
                (
                    snapshot
                    for snapshot in burst
                    if first_result["contribution"] in snapshot["ids"]
                    and second_result["contribution"] in snapshot["ids"]
                ),
                None,
            )
            assert first_frame and second_frame, burst
            assert first_frame["base"] != second_frame["base"], burst
            wait(
                call,
                'document.body.innerText.includes("SSE context added while '
                'the answer drawer is open.")',
            )
            wait(
                call,
                'document.body.innerText.includes("A second queued server update while '
                'the answer drawer is open.")',
            )
            try:
                wait(
                    call,
                    "(()=>{const dialog=document.querySelector('.authoring-drawer[open]');"
                    "const field=dialog?.querySelector(\"[aria-label^='Answer question']\");"
                    "return dialog?.contains(document.activeElement)&&"
                    "document.activeElement===field})()",
                )
            except BaseException as error:
                focus_diagnostic = evaluate(
                    call,
                    "(() => {const dialog=document.querySelector('.authoring-drawer');"
                    "const field=dialog?.querySelector('[aria-label^=\"Answer question\"]');"
                    "const state=workspaceStates.get(new URLSearchParams(location.search)"
                    ".get('task'));"
                    "return JSON.stringify({expectedKey:window.__drawerFocusProbe?.before?."
                    "key||null,"
                    "fieldKey:field?.dataset.workspaceFocus||null,activeTag:document.activeElement?.tagName||null,"
                    "activeKey:document.activeElement?.dataset.workspaceFocus||null,"
                    "activeLabel:document.activeElement?.getAttribute('aria-label')||null,"
                    "activeInsideDrawer:Boolean(dialog?.contains(document.activeElement)),"
                    "dialogConnected:Boolean(dialog?.isConnected),dialogOpen:Boolean(dialog?.open),"
                    "drawerOpen:state?.drawerOpen,drawerReplacing:state?.drawerReplacing,"
                    "value:field?.value,start:field?.selectionStart,end:field?.selectionEnd,"
                    "showModal:window.__drawerFocusProbe?.showModal||[]});})()",
                )
                raise AssertionError(
                    f"drawer focus did not restore after SSE: {focus_diagnostic}"
                ) from error
            focus_state = evaluate(
                call,
                "(() => {const dialog=document.querySelector('.authoring-drawer[open]');"
                "const field=dialog?.querySelector('[aria-label^=\"Answer question\"]');"
                "return JSON.stringify({inside:dialog?.contains(document.activeElement),"
                "active:document.activeElement===field,"
                "value:field?.value,start:field?.selectionStart,end:field?.selectionEnd,"
                "expectedKey:field?.dataset.workspaceFocus,"
                "activeKey:document.activeElement?.dataset.workspaceFocus||null,"
                "activeLabel:document.activeElement?.getAttribute('aria-label')||null,"
                "activeTag:document.activeElement?.tagName||null});})()",
            )
            focus_state = json.loads(focus_state)
            assert focus_state["inside"] and focus_state["active"], (
                f"expected key={focus_state['expectedKey']!r}; "
                f"active key={focus_state['activeKey']!r}; "
                f"label={focus_state['activeLabel']!r}; tag={focus_state['activeTag']!r}"
            )
            assert focus_state["value"] == "Draft retained through server refresh."
            assert focus_state["start"] == focus_state["end"] == len(focus_state["value"])
            evaluate(
                call,
                "(() => {const probe=window.__drawerFocusProbe;probe.queued=[];"
                "probe.stream=source;probe.handler=source.onmessage;"
                "source.onmessage=event=>probe.queued.push(event);return true;})()",
            )
            before_interaction_burst = _http_json(server, f"/api/authoring?task={task_id}")[1]
            first_interaction_status, _ = _http_json(
                server,
                "/api/authoring/followup",
                method="POST",
                body={
                    "task": task_id,
                    "expected": before_interaction_burst["base"],
                    "request_id": str(uuid.uuid4()),
                    "body": "A third server update queued before a keyboard interaction.",
                },
            )
            assert first_interaction_status == 200
            wait(call, "window.__drawerFocusProbe.queued.length>=1")
            after_first_interaction_update = _http_json(server, f"/api/authoring?task={task_id}")[1]
            second_interaction_status, _ = _http_json(
                server,
                "/api/authoring/followup",
                method="POST",
                body={
                    "task": task_id,
                    "expected": after_first_interaction_update["base"],
                    "request_id": str(uuid.uuid4()),
                    "body": "A fourth server update queued before a keyboard interaction.",
                },
            )
            assert second_interaction_status == 200
            wait(call, "window.__drawerFocusProbe.queued.length>=2")
            interaction_burst = evaluate(
                call,
                "(() => {const probe=window.__drawerFocusProbe;const queued=probe.queued.splice(0);"
                "const handler=probe.handler;handler.call(probe.stream,queued[0]);"
                "const control=[...document.querySelectorAll("
                "'.workspace-panel .authoring-question button')].find(item=>"
                "item.textContent==='Save answer');"
                "if(!control)throw new Error('no live keyless button');"
                "control.focus();control.dispatchEvent(new KeyboardEvent('keydown',"
                "{key:'x',bubbles:true}));"
                "const focusedBeforeSecond=control===document.activeElement;"
                "handler.call(probe.stream,queued[1]);probe.stream.onmessage=handler;"
                "return {count:queued.length,focusedBeforeSecond,"
                "controlKey:control.dataset.workspaceFocus||null,"
                "bases:queued.map(event=>JSON.parse(event.data).task?.authoring?.base)};})()",
            )
            assert interaction_burst["count"] >= 2, interaction_burst
            assert interaction_burst["focusedBeforeSecond"], interaction_burst
            assert interaction_burst["controlKey"] is None, interaction_burst
            assert interaction_burst["bases"][0] != interaction_burst["bases"][1], interaction_burst
            wait(
                call,
                'document.body.innerText.includes("A fourth server update queued before a keyboard '
                'interaction.")',
            )
            wait(call, 'document.querySelector(".authoring-drawer[open]")!==null')
            after_interaction_focus = evaluate(
                call,
                "(() => {const dialog=document.querySelector('.authoring-drawer[open]');"
                "const field=dialog?.querySelector('[aria-label^=\"Answer question\"]');"
                "return {inside:dialog?.contains(document.activeElement),activeAnswer:"
                "document.activeElement===field,activeLabel:document.activeElement?.getAttribute('aria-label'),"
                "drawerOpen:workspaceStates.get(new URLSearchParams(location.search)"
                ".get('task'))?.drawerOpen};})()",
            )
            assert after_interaction_focus["inside"] and after_interaction_focus["drawerOpen"]
            assert not after_interaction_focus["activeAnswer"], after_interaction_focus
            deferral_focus = evaluate(
                call,
                "(() => {const dialog=document.querySelector('.authoring-drawer[open]');"
                "const details=[...dialog.querySelectorAll('details')].find(item=>"
                "item.querySelector('summary')?.textContent.includes('Defer with a reason'));"
                "details.open=true;const field=dialog.querySelector("
                "'[aria-label=\"Deferral reason\"]');"
                "field.value='Inspect the retained importer before changing this answer.';"
                "field.focus();field.setSelectionRange(8,8);"
                "field.dispatchEvent(new Event('input',{bubbles:true}));"
                "const background=document.querySelector("
                "'.workspace-panel .authoring-question [aria-label=\"Deferral reason\"]');"
                "return {drawerKey:field.dataset.workspaceFocus,"
                "backgroundKey:background?.dataset.workspaceFocus};})()",
            )
            assert deferral_focus["drawerKey"] != deferral_focus["backgroundKey"]
            deferral_base = _http_json(server, f"/api/authoring?task={task_id}")[1]["base"]
            status, _ = _http_json(
                server,
                "/api/authoring/followup",
                method="POST",
                body={
                    "task": task_id,
                    "expected": deferral_base,
                    "request_id": str(uuid.uuid4()),
                    "body": "A second live update while the deferral editor is focused.",
                },
            )
            assert status == 200
            wait(
                call,
                "document.body.innerText.includes("
                '"A second live update while the deferral editor is focused.")',
            )
            wait(
                call,
                "(()=>{const dialog=document.querySelector('.authoring-drawer[open]');"
                "const field=dialog?.querySelector('[aria-label=\\\"Deferral reason\\\"]');"
                "return dialog?.contains(document.activeElement)&&"
                "document.activeElement===field})()",
            )
            deferral_state = evaluate(
                call,
                "(() => {const dialog=document.querySelector('.authoring-drawer[open]');"
                "const field=dialog?.querySelector('[aria-label=\"Deferral reason\"]');"
                "return {inside:dialog?.contains(document.activeElement),"
                "active:document.activeElement===field,value:field?.value,start:field?.selectionStart,"
                "end:field?.selectionEnd,key:field?.dataset.workspaceFocus,"
                "activeKey:document.activeElement?.dataset.workspaceFocus,"
                "disclosureOpen:field?.closest('details')?.open};})()",
            )
            assert deferral_state["inside"] and deferral_state["active"], deferral_state
            assert deferral_state["disclosureOpen"]
            assert deferral_state["key"] == deferral_focus["drawerKey"]
            assert deferral_state["activeKey"] == deferral_state["key"]
            assert deferral_state["value"] == (
                "Inspect the retained importer before changing this answer."
            )
            assert deferral_state["start"] == deferral_state["end"] == 8
            closed_drawer = evaluate(
                call,
                "(()=>{const dialog=document.querySelector('.authoring-drawer[open]');"
                "window.drawerOldDialog=dialog;window.drawerCloseTrace=[];"
                "dialog.addEventListener('close',()=>{window.drawerCloseEventSeen=true;"
                "const state=workspaceStates.get("
                + json.dumps(task_id)
                + ");window.drawerCloseTrace.push({connected:dialog.isConnected,open:dialog.open,"
                "drawerOpen:state.drawerOpen,replacing:state.drawerReplacing});},{once:true});"
                "const button=[...dialog.querySelectorAll('button')].find("
                "item=>item.textContent==='Close interview');button.click();"
                "return {open:dialog.open,hasOpen:dialog.hasAttribute('open'),"
                "remembered:workspaceStates.get(" + json.dumps(task_id) + ").drawerOpen};})()",
            )
            assert closed_drawer == {"open": False, "hasOpen": False, "remembered": False}
            wait(call, 'document.querySelector(".authoring-drawer[open]")===null')
            wait(call, "window.drawerCloseEventSeen===true")
            wait(
                call,
                "document.activeElement?.dataset.workspaceFocus==="
                "workspaceStates.get(" + json.dumps(task_id) + ").drawerFocusReturnKey",
            )
            close_focus_settled = evaluate(
                call,
                "(async()=>{await new Promise(resolve=>setTimeout(resolve,0));"
                "return document.activeElement?.dataset.workspaceFocus==="
                "workspaceStates.get(" + json.dumps(task_id) + ").drawerFocusReturnKey;})()",
            )
            assert close_focus_settled, "drawer close focus-return task did not settle"
            background_focus = evaluate(
                call,
                "(() => {const card=document.querySelector('.workspace-panel .authoring-question');"
                "const details=[...card.querySelectorAll('details')].find(item=>"
                "item.querySelector('summary')?.textContent.includes('Defer with a reason'));"
                "details.open=true;const field=card.querySelector("
                "'[aria-label=\"Deferral reason\"]');"
                "field.value='Background draft with the drawer closed.';"
                "field.focus();field.setSelectionRange(11,11);"
                "field.dispatchEvent(new Event('input',{bubbles:true}));"
                "return {key:field.dataset.workspaceFocus,start:field.selectionStart,"
                "active:document.activeElement===field,open:details.open};})()",
            )
            assert background_focus["active"] and background_focus["open"], background_focus
            latest_before_background_sse = _http_json(server, f"/api/authoring?task={task_id}")[1]
            status, _ = _http_json(
                server,
                "/api/authoring/followup",
                method="POST",
                body={
                    "task": task_id,
                    "expected": latest_before_background_sse["base"],
                    "request_id": str(uuid.uuid4()),
                    "body": "A live update while the closed drawer remains mounted.",
                },
            )
            assert status == 200
            wait(
                call,
                "document.body.innerText.includes("
                '"A live update while the closed drawer remains mounted.")',
            )
            background_state = evaluate(
                call,
                "(() => {const field=document.querySelector("
                "'.workspace-panel .authoring-question [aria-label=\"Deferral reason\"]');"
                "const dialog=document.querySelector('.authoring-drawer:not([open])');"
                "return {active:document.activeElement===field,"
                "dialogClosed:Boolean(dialog),key:field?.dataset.workspaceFocus,"
                "activeKey:document.activeElement?.dataset.workspaceFocus,value:field?.value,"
                "start:field?.selectionStart,end:field?.selectionEnd,"
                "disclosureOpen:field?.closest('details')?.open};})()",
            )
            assert background_state["active"] and background_state["dialogClosed"], background_state
            assert background_state["disclosureOpen"]
            assert background_state["key"] == background_focus["key"]
            assert background_state["activeKey"] == background_state["key"]
            assert background_state["value"] == "Background draft with the drawer closed."
            assert background_state["start"] == background_state["end"] == 11
            evaluate(
                call,
                "window.drawerReopenShowModal=[];window.drawerOriginalShowModal="
                "HTMLDialogElement.prototype.showModal;"
                "HTMLDialogElement.prototype.showModal=function(){"
                "const state=workspaceStates.get("
                + json.dumps(task_id)
                + ");const row={className:this.className,connected:this.isConnected,"
                "sameAsClosed:this===window.drawerOldDialog,drawerOpen:state.drawerOpen,"
                "replacing:state.drawerReplacing,oldConnected:window.drawerOldDialog.isConnected,"
                "oldOpen:window.drawerOldDialog.open};"
                "try{window.drawerOriginalShowModal.call(this);row.open=this.open;}"
                "catch(error){row.error=String(error);};window.drawerReopenShowModal.push(row);};true",
            )
            reopen_state = evaluate(
                call,
                "(()=>{const button=document.querySelector("
                "'.workspace-top-actions button[aria-haspopup=\"dialog\"]');"
                "button.click();const state=workspaceStates.get("
                + json.dumps(task_id)
                + ");return {label:button.textContent,drawerOpen:state.drawerOpen,"
                "question:state.drawerQuestionId,dialogs:[...document.querySelectorAll('dialog')]"
                ".map(item=>({className:item.className,open:item.open,connected:item.isConnected}))};})()",
            )
            assert reopen_state["drawerOpen"], reopen_state
            wait(call, "window.drawerReopenShowModal.length>0")
            reopen_state["after"] = evaluate(
                call,
                "({dialogs:[...document.querySelectorAll('dialog')].map(item=>({"
                "className:item.className,open:item.open,connected:item.isConnected})),"
                "state:workspaceStates.get("
                + json.dumps(task_id)
                + ").drawerOpen,showModal:window.drawerReopenShowModal,"
                "closeTrace:window.drawerCloseTrace})",
            )
            assert evaluate(call, "Boolean(document.querySelector('.authoring-drawer[open]'))"), (
                json.dumps(reopen_state)
            )
            evaluate(
                call,
                "HTMLDialogElement.prototype.showModal=window.drawerOriginalShowModal;true",
            )
            evaluate(
                call,
                "(() => {const prior=window.fetch;window.lastAnswerResponse=null;"
                "window.fetch=async(...args)=>{const response=await prior(...args);"
                "if(String(args[0]).includes('/api/authoring/answer'))"
                "window.lastAnswerResponse={status:response.status,"
                "body:await response.clone().text(),request:JSON.parse(args[1].body)};"
                "return response;};"
                "[...document.querySelectorAll('.authoring-drawer button')]"
                ".find(item=>item.textContent==='Save answer').click();return true;})()",
            )
            wait(call, "window.lastAnswerResponse!==null")
            save_feedback = evaluate(
                call,
                "({body:document.body.innerText,drawerOpen:document.querySelector("
                "'.authoring-drawer[open]')!==null,message:document.querySelector("
                "'.authoring-drawer .authoring-action-message')?.innerText,"
                "response:window.lastAnswerResponse,latestBase:"
                + json.dumps(latest_after_followup["base"])
                + ",storedBuffer:(()=>{const key=Object.keys(localStorage).find(item=>"
                + "item.endsWith("
                + json.dumps(":answer:" + str(question_id))
                + "));"
                + "return key?JSON.parse(localStorage.getItem(key)):null;})()})",
            )
            assert save_feedback["response"]["status"] == 409, save_feedback
            wait(
                call,
                'Boolean(document.querySelector(".authoring-drawer .authoring-conflict-recovery"))',
            )
            assert "authoring source or revision changed" in save_feedback["message"], save_feedback
            assert evaluate(
                call,
                'Boolean(document.querySelector(".authoring-drawer .authoring-conflict-recovery"))',
            )
            evaluate(
                call,
                "[...document.querySelectorAll('.authoring-drawer button')]"
                ".find(item=>item.textContent==='Refresh latest state and keep my text')"
                ".click(); true",
            )
            wait(call, 'document.body.innerText.includes("Latest server state loaded")')
            retained = evaluate(
                call,
                """document.querySelector(
                  '.authoring-drawer [aria-label^="Answer question"]')?.value""",
            )
            assert retained == "Draft retained through server refresh."
            evaluate(
                call,
                """(() => {
                  const field=document.querySelector(
                    '.authoring-drawer [aria-label^="Answer question"]');
                  field.value='Saved answer A.';
                  field.dispatchEvent(new Event('input',{bubbles:true}));
                  const prior=window.fetch;
                  window.fetch=(url,options)=>{
                    if(String(url).includes('/api/authoring/answer')&&!window.answerAckHeld){
                      window.answerAckHeld=true;
                      return prior(url,options).then(response=>new Promise(resolve=>{
                        window.releaseAnswerAck=()=>resolve(response);
                      }));
                    }
                    return prior(url,options);
                  };
                  [...document.querySelectorAll('.authoring-drawer button')]
                    .find(item=>item.textContent==='Save answer').click();
                  return true;
                })()""",
            )
            wait(call, 'typeof window.releaseAnswerAck==="function"')
            committed = _http_json(server, f"/api/authoring?task={task_id}")[1]
            saved_question = next(
                row for row in committed["questions"] if row["id"] == int(question_id)
            )
            assert saved_question["answer"] == "Saved answer A."
            wait(
                call,
                'document.querySelector(".authoring-drawer[open]")?.innerText.includes('
                '"Your recorded answer: Saved answer A.")',
            )
            evaluate(
                call,
                """(() => {
                  const dialog=document.querySelector('.authoring-drawer[open]');
                  const revise=[...dialog.querySelectorAll('details')].find(item=>
                    item.querySelector('summary')?.textContent==='Revise your answer');
                  revise.open=true;
                  const field=revise.querySelector('[aria-label^="Revised answer for question"]');
                  field.value='Newer answer B typed while A is pending.';
                  field.dispatchEvent(new Event('input',{bubbles:true}));
                  window.releaseAnswerAck();
                  return true;
                })()""",
            )
            correction_selector = f'[data-revision-target="question-response:{question_id}"]'
            correction_input = correction_selector + ' [aria-label="Corrected answer"]'
            wait(
                call,
                f"document.querySelector({json.dumps(correction_input)})?.value"
                '.includes("Newer answer B")',
            )
            base_before_recovery = evaluate(
                call,
                "(()=>{const key=Object.keys(localStorage).find(item=>item.endsWith("
                + json.dumps(":record-revision:question-response:" + str(question_id))
                + "));return JSON.parse(localStorage.getItem(key)).base;})()",
            )
            assert base_before_recovery != committed["base"]
            evaluate(
                call,
                "(() => {const card=document.querySelector("
                + json.dumps(correction_selector)
                + ");[...card.querySelectorAll('button')]"
                ".find(item=>item.textContent==='Save correction to history').click();"
                "return true;})()",
            )
            wait(call, 'Boolean(document.querySelector(".authoring-conflict-recovery"))')
            evaluate(
                call,
                "[...document.querySelectorAll('.authoring-conflict-recovery button')]"
                ".find(item=>item.textContent==="
                "'Refresh latest state and keep my text').click();true",
            )
            wait(call, 'document.body.innerText.includes("Latest server state loaded")')
            retained_b = evaluate(
                call, f"document.querySelector({json.dumps(correction_input)})?.value"
            )
            assert retained_b == ("Newer answer B typed while A is pending.")
            latest_for_revision = _http_json(server, f"/api/authoring?task={task_id}")[1]
            recovered_base = evaluate(
                call,
                "(()=>{const key=Object.keys(localStorage).find(item=>item.endsWith("
                + json.dumps(":record-revision:question-response:" + str(question_id))
                + "));return JSON.parse(localStorage.getItem(key)).base;})()",
            )
            assert recovered_base == latest_for_revision["base"]
            evaluate(
                call,
                "(() => {const card=document.querySelector("
                + json.dumps(correction_selector)
                + ");[...card.querySelectorAll('button')]"
                ".find(item=>item.textContent==='Save correction to history').click();"
                "return true;})()",
            )
            wait(call, 'document.body.innerText.includes("A correction is saved in history")')
            evaluate(
                call,
                "[...document.querySelectorAll("
                + json.dumps(correction_selector + " button")
                + ")]"
                ".find(item=>item.textContent==='Apply this saved correction').click(); true",
            )
            wait(
                call,
                "(async()=>{const value=await(await fetch('/api/authoring?task='+"
                + json.dumps(task_id)
                + ")).json();return value.questions.some(row=>row.answer==="
                "'Newer answer B typed while A is pending.')})()",
            )
            final = _http_json(server, f"/api/authoring?task={task_id}")[1]
            assert (
                sum(
                    row["answer"] == "Newer answer B typed while A is pending."
                    for row in final["questions"]
                )
                == 1
            )
            original_followup_base = final["base"]
            wait(
                call,
                "(async()=>{const task="
                + json.dumps(task_id)
                + ";const api=await(await fetch('/api/authoring?task='+"
                "encodeURIComponent(task),{cache:'no-store'})).json();"
                "const state=workspaceStates.get(task);"
                "const buffer=state.authoringBuffers.followup;"
                "const base=JSON.stringify(api.base);"
                "return JSON.stringify(state.authoring?.base)===base"
                "&&JSON.stringify(buffer.base)===base&&!state.saving})()",
            )
            followup_baseline = evaluate(
                call,
                "(()=>{const state=workspaceStates.get("
                + json.dumps(task_id)
                + ");return {base:state.authoringBuffers.followup.base,"
                "snapshotBase:state.authoring.base,value:state.authoringBuffers.followup.value,"
                "dirty:state.authoringBuffers.followup.dirty};})()",
            )
            assert followup_baseline["base"] == original_followup_base, followup_baseline
            evaluate(
                call,
                "(() => {const field=document.querySelector('[aria-label=\"Follow-up message\"]');"
                "field.value='UI follow-up A committed while its acknowledgement is held.';"
                "field.dispatchEvent(new Event('input',{bubbles:true}));"
                "window.followupBufferRef=workspaceStates.get("
                + json.dumps(task_id)
                + ").authoringBuffers.followup;const prior=window.fetch;"
                "window.followupAckHeld=false;"
                "window.fetch=async(...args)=>{const response=await prior(...args);"
                "if(String(args[0]).includes('/api/authoring/followup')&&"
                "JSON.parse(args[1].body).body==="
                "'UI follow-up A committed while its acknowledgement is held.'"
                "&&!window.followupAckHeld){window.followupAckHeld=true;"
                "window.followupARequest=JSON.parse(args[1].body);"
                "window.followupAResponse={status:response.status,"
                "body:await response.clone().text()};"
                "return new Promise(resolve=>window.releaseFollowupAck=()=>resolve(response));}"
                "return response;};"
                "[...document.querySelectorAll('button')].find(item=>"
                "item.textContent==='Save follow-up only').click();return true;})()",
            )
            wait(call, 'typeof window.releaseFollowupAck==="function"')
            held_followup_response = evaluate(call, "window.followupAResponse")
            held_followup_request = evaluate(call, "window.followupARequest")
            assert (
                held_followup_response["status"] == 200
                and held_followup_request["expected"] == final["base"]
            ), {
                "baseline": followup_baseline,
                "request": held_followup_request,
                "response": held_followup_response,
            }
            after_a = _http_json(server, f"/api/authoring?task={task_id}")[1]
            assert (
                sum(
                    item["payload"].get("body")
                    == "UI follow-up A committed while its acknowledgement is held."
                    for item in after_a["contributions"]
                )
                == 1
            )
            wait(
                call,
                "document.body.innerText.includes("
                '"UI follow-up A committed while its acknowledgement is held.")',
            )
            evaluate(
                call,
                "(() => {const field=document.querySelector('[aria-label=\"Follow-up message\"]');"
                "field.value='UI follow-up B typed after SSE while A is still pending.';"
                "field.dispatchEvent(new Event('input',{bubbles:true}));return true;})()",
            )
            status, _ = _http_json(
                server,
                "/api/authoring/followup",
                method="POST",
                body={
                    "task": task_id,
                    "expected": after_a["base"],
                    "request_id": str(uuid.uuid4()),
                    "body": "Another connection changed the conversation during A.",
                },
            )
            assert status == 200
            wait(
                call,
                "document.body.innerText.includes("
                '"Another connection changed the conversation during A.")',
            )
            pending_followup = evaluate(
                call,
                "({sameObject:workspaceStates.get("
                + json.dumps(task_id)
                + ").authoringBuffers.followup===window.followupBufferRef,"
                "value:document.querySelector('[aria-label=\"Follow-up message\"]')?.value,"
                "base:workspaceStates.get("
                + json.dumps(task_id)
                + ").authoringBuffers.followup.base})",
            )
            assert pending_followup["sameObject"]
            assert pending_followup["value"] == (
                "UI follow-up B typed after SSE while A is still pending."
            )
            assert pending_followup["base"] == original_followup_base
            evaluate(call, "window.releaseFollowupAck();true")
            wait(
                call,
                "workspaceStates.get("
                + json.dumps(task_id)
                + ").authoringRequests?.['followup-only']===undefined",
            )
            settled_followup = evaluate(
                call,
                "({value:workspaceStates.get("
                + json.dumps(task_id)
                + ").authoringBuffers.followup.value,base:workspaceStates.get("
                + json.dumps(task_id)
                + ").authoringBuffers.followup.base,dirty:workspaceStates.get("
                + json.dumps(task_id)
                + ").authoringBuffers.followup.dirty})",
            )
            assert settled_followup == {
                "value": "UI follow-up B typed after SSE while A is still pending.",
                "base": original_followup_base,
                "dirty": True,
            }
            evaluate(
                call,
                "[...document.querySelectorAll('button')]"
                ".find(item=>item.textContent==='Save follow-up only').click();true",
            )
            wait(call, 'Boolean(document.querySelector(".authoring-conflict-recovery"))')
            evaluate(
                call,
                "[...document.querySelectorAll('.authoring-conflict-recovery button')]"
                ".find(item=>item.textContent==='Refresh latest state and keep my text')"
                ".click();true",
            )
            wait(call, 'document.body.innerText.includes("Latest server state loaded")')
            assert (
                evaluate(
                    call,
                    "document.querySelector('[aria-label=\"Follow-up message\"]')?.value",
                )
                == "UI follow-up B typed after SSE while A is still pending."
            )
            evaluate(
                call,
                "[...document.querySelectorAll('button')]"
                ".find(item=>item.textContent==='Save follow-up only').click();true",
            )
            wait(
                call,
                "(async()=>{const value=await(await fetch('/api/authoring?task='+"
                + json.dumps(task_id)
                + ")).json();return value.contributions.some(item=>item.payload?.body==="
                + json.dumps("UI follow-up B typed after SSE while A is still pending.")
                + ")})()",
            )
            final_followup = _http_json(server, f"/api/authoring?task={task_id}")[1]
            for body in (
                "UI follow-up A committed while its acknowledgement is held.",
                "Another connection changed the conversation during A.",
                "UI follow-up B typed after SSE while A is still pending.",
            ):
                assert (
                    sum(
                        item["payload"].get("body") == body
                        for item in final_followup["contributions"]
                    )
                    == 1
                )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_chrome_rework1_async_unknown_retry_consent_is_bound_to_attempt_and_claim(
    db_path, sample_repo, monkeypatch, tmp_path
):
    class CrashingProvider:
        def __init__(self, _host):
            pass

        def run(self, _inputs, *, cancelled=lambda: False):
            raise RuntimeError("fixture provider process exited unexpectedly")

    monkeypatch.setattr(
        "harness.authoring_worker.AuthoringWorker",
        lambda path, root, **kwargs: AuthoringWorker(
            path, root, provider_factory=CrashingProvider, **kwargs
        ),
    )
    server = DashboardServer(db_path, None, project_root=sample_repo)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    server.poll_seconds = 0.1
    thread.start()
    try:
        with chrome(tmp_path / "chrome-profile", server.url, 1280, 900) as call:
            wait(call, 'Boolean(document.querySelector("#project-start textarea"))')
            evaluate(
                call,
                "(() => {const form=document.querySelector('#project-start form');"
                "const request=form.querySelector('textarea');"
                "request.value='Exercise safe retry after a provider outcome becomes unknown.';"
                "request.dispatchEvent(new Event('input',{bubbles:true}));"
                "form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));"
                "return true;})()",
            )
            wait(call, 'location.search.includes("task=")')
            task_id = evaluate(call, "new URLSearchParams(location.search).get('task')")
            _capture_authoring_posts(call)
            wait(
                call,
                "(async()=>{const value=await(await fetch('/api/authoring?task='+"
                + json.dumps(task_id)
                + ")).json();return value.jobs?.some(job=>job.settlement==='unknown'&&"
                + "['failed','stale','cancelled','interrupted'].includes(job.state))})()",
            )
            initial = _http_json(server, f"/api/authoring?task={task_id}")[1]
            first_job = next(
                job
                for job in initial["jobs"]
                if job["settlement"] == "unknown"
                and job["state"] in {"failed", "stale", "cancelled", "interrupted"}
            )
            assert first_job["settlement"] == "unknown"
            unknown_row = f'.authoring-job[data-job-id="{first_job["id"]}"]'
            checkbox = unknown_row + " input[type=checkbox]"
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not evaluate(
                call, f"Boolean(document.querySelector({json.dumps(checkbox)}))"
            ):
                time.sleep(0.05)
            checkbox_state = evaluate(
                call,
                "(()=>{const row=document.querySelector(" + json.dumps(unknown_row) + ");"
                "const state=workspaceStates.get(" + json.dumps(task_id) + ");"
                "return {row:row?.innerText,html:row?.outerHTML,"
                "uiJob:state?.authoring?.jobs?.find(job=>job.id==="
                + json.dumps(first_job["id"])
                + ")};})()",
            )
            assert evaluate(call, f"Boolean(document.querySelector({json.dumps(checkbox)}))"), (
                checkbox_state
            )
            controls = evaluate(
                call,
                "(() => {const row=document.querySelector(" + json.dumps(unknown_row) + ");"
                "return {checked:row.querySelector('input[type=checkbox]').checked,"
                "disabled:row.querySelector('button').disabled,"
                "label:row.querySelector('input[type=checkbox]').closest('label')?.textContent};})()",
            )
            assert controls["checked"] is False
            assert controls["disabled"] is True
            assert "outcome is unknown" in controls["label"]
            evaluate(
                call,
                "(() => {const row=document.querySelector(" + json.dumps(unknown_row) + ");"
                "row.querySelector('input[type=checkbox]').click();return true;})()",
            )
            assert evaluate(
                call,
                "!document.querySelector(" + json.dumps(unknown_row + " button") + ").disabled",
            )
            evaluate(
                call,
                "document.querySelector(" + json.dumps(unknown_row + " button") + ").click();true",
            )
            attempt_advanced = (
                "(async()=>{const value=await(await fetch('/api/authoring?task='+"
                + json.dumps(task_id)
                + ")).json();return value.jobs?.some(job=>job.id==="
                + json.dumps(first_job["id"])
                + "&&job.attempt>"
                + str(first_job["attempt"])
                + ")})()"
            )
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not evaluate(call, attempt_advanced):
                time.sleep(0.05)
            latest_after_retry_click = _http_json(server, f"/api/authoring?task={task_id}")[1]
            latest_job = next(
                job for job in latest_after_retry_click["jobs"] if job["id"] == first_job["id"]
            )
            assert latest_job["attempt"] > first_job["attempt"], {
                "job": {
                    key: latest_job[key]
                    for key in ("id", "state", "attempt", "claim", "settlement")
                },
                "posts": evaluate(
                    call,
                    "window.__authoringPosts.filter(item=>item.url.includes('/retry-job'))",
                ),
            }
            server.authoring_worker.start()
            wait(
                call,
                "(async()=>{const value=await(await fetch('/api/authoring?task='+"
                + json.dumps(task_id)
                + ")).json();return value.jobs?.some(job=>job.id==="
                + json.dumps(first_job["id"])
                + "&&job.settlement==='unknown'&&job.attempt>"
                + str(first_job["attempt"])
                + ")})()",
            )
            latest = _http_json(server, f"/api/authoring?task={task_id}")[1]["jobs"][-1]
            assert latest["id"] == first_job["id"]
            assert latest["attempt"] > first_job["attempt"]
            assert latest["claim"] != first_job["claim"]
            controls = evaluate(
                call,
                "(() => {const row=document.querySelector(" + json.dumps(unknown_row) + ");"
                "return {checked:row.querySelector('input[type=checkbox]').checked,"
                "disabled:row.querySelector('button').disabled};})()",
            )
            assert controls == {"checked": False, "disabled": True}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
