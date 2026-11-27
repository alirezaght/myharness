"""Real browser coverage for bounded attached-session history and material review."""

import json
import tempfile
import threading
import uuid
from pathlib import Path

import pytest

from harness.authoring_worker import AuthoringWorker
from harness.dashboard.server import DashboardServer
from tests.planning_session_fixture import attached_payload, request
from tests.planning_session_fixture import session as _session_fixture
from tests.test_dashboard_authoring_browser import CHROME, _capture, _http_json
from tests.test_dashboard_interview_recovery_browser import click_text, enter, set_theme
from tests.test_dashboard_live_view_state_browser import chrome, evaluate, wait

pytestmark = [pytest.mark.browser, pytest.mark.skipif(CHROME is None, reason="Chrome unavailable")]
ARTIFACTS = Path(tempfile.gettempdir()) / "harness-interview-recovery-artifacts" / "outputs"


def _server(db_path, task, root):
    server = DashboardServer(db_path, task, project_root=root)
    server.poll_seconds = 0.1
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    return server, thread


def _shutdown(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)
    assert not thread.is_alive()


@pytest.fixture
def planning_session(controller, tmp_path):
    return _session_fixture.__wrapped__(controller, tmp_path)


def _select_candidate(call, candidate_id, before_release=None):
    wait(call, "Boolean(document.querySelector('select[aria-label=\"Candidate revision\"]'))")
    evaluate(
        call,
        "(()=>{const select=document.querySelector('select[aria-label=\"Candidate revision\"]');"
        + "select.value="
        + json.dumps(candidate_id)
        + ";select.dispatchEvent(new Event('change',{bubbles:true}));return true;})()",
    )
    evaluate(
        call,
        "(()=>{window.__candidateReads=[];window.__candidateErrors=[];"
        "window.addEventListener('error',event=>window.__candidateErrors.push("
        "String(event.message)));window.__candidateGate=new Promise(resolve=>"
        "window.__releaseCandidate=resolve);const original=window.fetch;"
        "window.__candidateOriginal=original;"
        "window.fetch=async(...args)=>{const response=await original(...args);"
        "if(String(args[0]).includes('/api/authoring/revision')){"
        "window.__candidateReads.push({status:response.status,url:String(args[0]),"
        "body:await response.clone().text()});await window.__candidateGate;}"
        "return response;};return true;})()",
    )
    click_text(call, "Load candidate diff")
    wait(call, "window.__candidateReads.length===1")
    reads = evaluate(call, "window.__candidateReads")
    assert len(reads) == 1 and reads[0]["status"] == 200, reads
    assert candidate_id in reads[0]["url"]
    if before_release is not None:
        before_release()
    evaluate(call, "window.__releaseCandidate();true")
    wait(
        call,
        "window.__candidateReads.length===1 && "
        "![...document.querySelectorAll('button')].find("
        "button=>button.textContent==='Load candidate diff')?.disabled",
    )
    rendered = evaluate(
        call,
        "(()=>({heading:document.querySelector('.candidate-diff h3')?.textContent||null,"
        "diff:document.querySelector('.candidate-diff')?.innerText||null,"
        "selected:document.querySelector('select[aria-label=\"Candidate revision\"]')?.value,"
        "errors:window.__candidateErrors}))()",
    )
    assert rendered["heading"] and "Candidate" in rendered["heading"], {
        "reads": reads,
        "rendered": rendered,
    }
    assert rendered["selected"] == candidate_id, {"expected": candidate_id, "rendered": rendered}
    evaluate(call, "window.fetch=window.__candidateOriginal;delete window.__candidateOriginal;true")


def _preview_adoption(call):
    evaluate(
        call,
        "(()=>{window.__previewPosts=[];const original=window.fetch;"
        "window.__previewOriginal=original;window.fetch=async(...args)=>{"
        "const response=await original(...args);"
        "if(String(args[0]).includes('/api/authoring/reconcile-preview'))"
        "window.__previewPosts.push({status:response.status,body:await response.clone().text()});"
        "return response;};return true;})()",
    )
    click_text(call, "Preview explicit adoption")
    wait(
        call,
        "window.__previewPosts.length===1 || Boolean(document.querySelector("
        "'.reconcile-preview')?.textContent)",
    )
    assert evaluate(call, "window.__previewPosts.length===1"), evaluate(
        call, 'document.querySelector(".reconcile-preview")?.innerText || "preview missing"'
    )
    response = evaluate(call, "window.__previewPosts[0]")
    assert response["status"] == 200, response
    wait(call, 'Boolean(document.querySelector(".reconcile-preview button"))')
    evaluate(call, "window.fetch=window.__previewOriginal;delete window.__previewOriginal;true")


def _preview_then_change_selection(call, next_candidate_id):
    evaluate(
        call,
        "(()=>{window.__heldPreview=[];window.__previewReturned=false;"
        "let release;window.__previewGate=new Promise(resolve=>release=resolve);"
        "window.__releasePreview=release;const original=window.fetch;"
        "window.__previewOriginal=original;window.fetch=async(...args)=>{"
        "const response=await original(...args);"
        "if(String(args[0]).includes('/api/authoring/reconcile-preview')){"
        "window.__heldPreview.push({status:response.status,body:await response.clone().text()});"
        "await window.__previewGate;window.__previewReturned=true;}"
        "return response;};return true;})()",
    )
    click_text(call, "Preview explicit adoption")
    wait(call, "window.__heldPreview.length===1")
    preview = evaluate(call, "window.__heldPreview[0]")
    assert preview["status"] == 200, preview
    evaluate(
        call,
        "(()=>{const select=document.querySelector('select[aria-label=\"Candidate revision\"]');"
        "select.value=" + json.dumps(next_candidate_id) + ";"
        "select.dispatchEvent(new Event('change',{bubbles:true}));return true;})()",
    )
    assert evaluate(call, 'document.querySelector(".reconcile-preview").children.length===0')
    evaluate(call, "window.__releasePreview();true")
    wait(call, "window.__previewReturned===true")
    assert evaluate(call, 'document.querySelector(".reconcile-preview").children.length===0')
    assert not evaluate(call, 'document.body.innerText.includes("Adoption preview")')
    evaluate(call, "window.fetch=window.__previewOriginal;delete window.__previewOriginal;true")


def _confirm_adoption(call):
    evaluate(
        call,
        "[...document.querySelectorAll('.authoring-reconcile > label input[type=checkbox]')]"
        ".forEach(box=>{if(!box.checked)box.click()});true",
    )


def test_chrome_attached_session_history_loads_older_events_without_overflow(
    planning_session, db_path, tmp_path, monkeypatch
):
    bridge, receipt, caller, capability = planning_session
    old_text = "Older retained event 000"
    with bridge._transaction():
        for index in range(55):
            bridge.store.append_event(
                "session.task_message",
                {"text": old_text if index == 0 else f"Recent retained event {index:03d}"},
                origin="attached_agent",
                deliverable=False,
            )
    monkeypatch.setattr(AuthoringWorker, "start", lambda self: False)
    server, thread = _server(db_path, bridge.task_id, bridge.root)
    try:
        page = _http_json(server, f"/api/session/events?task={bridge.task_id}&limit=50")[1]
        initial_events = page["session"]["events"]
        assert len(initial_events) == 50
        assert any(
            event["payload"].get("text") == "Recent retained event 054" for event in initial_events
        )
        assert not any(event["payload"].get("text") == old_text for event in initial_events)
        with chrome(
            tmp_path / "chrome-session-history",
            server.url + f"?task={bridge.task_id}&tab=interview",
            390,
            844,
        ) as call:
            wait(call, 'Boolean(document.querySelector(".authoring-conversation-history"))')
            _set_theme = set_theme
            _set_theme(call, "dark")
            wait(call, "document.body.innerText.includes('Recent retained event 054')")
            assert evaluate(
                call,
                "document.body.innerText.includes('Published by the invoking agent')",
            )
            assert not evaluate(
                call,
                "document.body.innerText.includes('not yet received by the agent')",
            )
            assert not evaluate(
                call, "document.body.innerText.includes(" + json.dumps(old_text) + ")"
            )
            assert evaluate(
                call,
                "Boolean([...document.querySelectorAll('button')].find(b=>"
                "b.textContent==='Load earlier conversation events'))",
            )
            click_text(call, "Load earlier conversation events")
            wait(
                call,
                "document.body.innerText.includes(" + json.dumps(old_text) + ")",
            )
            assert evaluate(call, "document.documentElement.scrollWidth<=innerWidth+1")
            snapshot = _http_json(server, f"/api/session/events?task={bridge.task_id}&limit=50")[1]
            assert len(snapshot["session"]["events"]) == 50
            assert snapshot["session"]["nextBefore"] is not None
    finally:
        _shutdown(server, thread)


def test_chrome_attached_session_copy_and_send_controls_follow_liveness(
    planning_session, db_path, tmp_path, monkeypatch
):
    bridge, _, _, _ = planning_session
    monkeypatch.setattr(AuthoringWorker, "start", lambda self: False)
    server, thread = _server(db_path, bridge.task_id, bridge.root)
    try:
        initial = _http_json(server, f"/api/dashboard?task={bridge.task_id}")[1]
        assert initial["task"]["session"]["snapshot"]["state"] in {"busy", "listening"}
        with chrome(
            tmp_path / "chrome-session-liveness",
            server.url + f"?task={bridge.task_id}&tab=interview",
            390,
            844,
        ) as call:
            wait(call, 'Boolean(document.querySelector(".shared-session-panel"))')
            live_copy = evaluate(
                call, 'document.querySelector(".shared-session-panel p")?.innerText||""'
            )
            assert "delivered to the same conversation" in live_copy, live_copy
            assert evaluate(
                call,
                "![...document.querySelectorAll('.shared-session-panel p')].some(node=>"
                "node.textContent.includes('delivery resumes only after "
                "the same agent reconnects'))",
            )
            evaluate(
                call,
                "[...document.querySelectorAll('.shared-session-panel summary')]"
                ".find(summary=>summary.textContent==='Session controls and "
                "technical details').click();true",
            )
            evaluate(
                call,
                "(()=>{window.__sessionActions=[];const original=window.fetch;"
                "window.__sessionFetch=original;window.fetch=async(...args)=>{"
                "const response=await original(...args);"
                "if(String(args[0]).includes('/api/session/action'))window.__sessionActions.push({"
                "status:response.status,body:await response.clone().text()});"
                "return response;};return true;})()",
            )
            click_text(call, "Pause shared session")
            wait(call, "window.__sessionActions.length===1")
            action = evaluate(call, "window.__sessionActions[0]")
            assert action["status"] == 200, action
            wait(call, "document.body.innerText.includes('Shared session paused')")
            status = _http_json(server, f"/api/dashboard?task={bridge.task_id}")[1]
            snapshot = status["task"]["session"]["snapshot"]
            assert snapshot["mode"] == "attached" and snapshot["state"] == "expired"
            stale_copy = evaluate(
                call, 'document.querySelector(".shared-session-panel p")?.innerText||""'
            )
            assert f"session is {snapshot['state']}" in stale_copy, stale_copy
            assert "remain saved on this task" in stale_copy, stale_copy
            assert "delivery resumes only after the same agent reconnects" in stale_copy, stale_copy
            assert evaluate(
                call,
                "[...document.querySelectorAll('.shared-session-panel button')].every(button=>"
                "button.textContent!=='Pause shared session' || !button.disabled)",
            )
            assert evaluate(
                call,
                "(()=>{const buttons=[...document.querySelectorAll('button')];"
                "const send=buttons.find(button=>button.textContent.includes('Send after "
                "Codex session recovery'));"
                "const response=buttons.find(button=>button.textContent.includes('Request "
                "Codex response after session recovery'));"
                "const save=buttons.find(button=>button.textContent==='Save follow-up only');"
                "return Boolean(send?.disabled&&response?.disabled&&save&&!save.disabled);})()",
            )
            followup_text = "Keep this follow-up saved while the invoking session reconnects."
            enter(call, 'textarea[aria-label="Follow-up message"]', followup_text)
            click_text(call, "Save follow-up only")
            wait(call, "document.body.innerText.includes('Follow-up saved as context')")
            saved_authoring = _http_json(server, f"/api/authoring?task={bridge.task_id}")[1]
            assert any(
                item["kind"] == "followup" and item["payload"].get("body") == followup_text
                for item in saved_authoring["contributions"]
            ), saved_authoring["contributions"]
            assert evaluate(call, "document.documentElement.scrollWidth<=innerWidth+1")
    finally:
        _shutdown(server, thread)


def test_chrome_manual_candidate_has_no_material_form_and_requires_explicit_adoption(
    controller, db_path, tmp_path, monkeypatch
):
    root = tmp_path / "manual-spec"
    root.mkdir()
    task = controller.create_task("Review a manually drafted account export specification")
    monkeypatch.setattr(AuthoringWorker, "start", lambda self: False)
    server, thread = _server(db_path, task.id, root)
    try:
        with chrome(
            tmp_path / "chrome-no-material",
            server.url + f"?task={task.id}&tab=spec",
            1280,
            900,
        ) as call:
            wait(call, 'Boolean(document.querySelector(".spec-editor"))')
            set_theme(call, "light")
            body = "## Account export\n\nExport every selected account as CSV.\n"
            enter(call, ".spec-editor", body)
            click_text(call, "Save working specification")
            wait(call, "document.body.innerText.includes('Working specification saved')")
            snapshot = _http_json(server, f"/api/authoring?task={task.id}")[1]
            candidate_id = snapshot["working"]["id"]
            click_text(call, "Review")
            _select_candidate(call, candidate_id)
            _confirm_adoption(call)
            _preview_adoption(call)
            wait(call, "!document.querySelector('.session-material-review')")
            assert evaluate(call, "document.body.innerText.includes('Adoption preview')")
            click_text(
                call,
                "Adopt this revision and request review",
                'document.querySelector(".reconcile-preview")',
            )
            wait(call, "document.body.innerText.includes('Revision adopted')")
            adopted = _http_json(server, f"/api/authoring?task={task.id}")[1]
            assert adopted["adopted"]["body"] == body
            assert adopted["adopted"]["id"] != candidate_id
            assert not evaluate(call, "Boolean(document.querySelector('.shared-session-panel'))")
            assert evaluate(call, "document.documentElement.scrollWidth<=innerWidth+1")
    finally:
        _shutdown(server, thread)


def test_chrome_attached_material_subset_and_stale_preview_require_explicit_refresh(
    controller, db_path, tmp_path, monkeypatch, planning_session
):
    bridge, receipt, caller, capability = planning_session
    bridge.publish(attached_payload(bridge, receipt, "material-review-source"), caller, capability)
    bridge.publish(
        request(bridge, receipt, "material-review-first-message")
        | {
            "consumes": [],
            "content": {"task_message": {"text": "Keep the account export auditable."}},
        },
        caller,
        capability,
    )
    monkeypatch.setattr(AuthoringWorker, "start", lambda self: False)
    server, thread = _server(db_path, bridge.task_id, bridge.root)
    try:
        snapshot = _http_json(server, f"/api/authoring?task={bridge.task_id}")[1]
        for body in (
            "External review note one: keep exports traceable.",
            "External review note two: retain selected account order.",
        ):
            status, _ = _http_json(
                server,
                "/api/authoring/followup",
                method="POST",
                body={
                    "task": bridge.task_id,
                    "expected": snapshot["base"],
                    "request_id": uuid.uuid4().hex,
                    "body": body,
                },
            )
            assert status == 200
            snapshot = _http_json(server, f"/api/authoring?task={bridge.task_id}")[1]
        with chrome(
            tmp_path / "chrome-material-subset",
            server.url + f"?task={bridge.task_id}&tab=review",
            1280,
            900,
        ) as call:
            wait(
                call, "Boolean(document.querySelector('select[aria-label=\"Candidate revision\"]'))"
            )
            set_theme(call, "light")
            candidate = next(
                row
                for row in reversed(snapshot["revisions"])
                if row["attribution"] == "model_proposal"
            )
            preview_status, preview = _http_json(
                server,
                "/api/authoring/reconcile-preview",
                method="POST",
                body={"task": bridge.task_id, "revision": candidate["id"]},
            )
            assert preview_status == 200
            note_one = next(
                item
                for item in preview["material"]["inputs"]
                if "External review note one" in json.dumps(item["payload"])
            )
            assert note_one["payload"]["input"]["body"] == (
                "External review note one: keep exports traceable."
            )
            evaluate(
                call,
                "(()=>{window.__reviewSection=document.querySelector('.authoring-reconcile');"
                "window.__reviewRerenders=0;new MutationObserver(()=>{const current="
                "document.querySelector('.authoring-reconcile');if(current&&current!==window.__reviewSection)"
                "{window.__reviewRerenders+=1;window.__reviewSection=current;}}).observe("
                "document.getElementById('main'),{childList:true,subtree:true});return true;})()",
            )

            def publish_during_candidate_read():
                current = _http_json(server, f"/api/authoring?task={bridge.task_id}")[1]
                status, result = _http_json(
                    server,
                    "/api/authoring/followup",
                    method="POST",
                    body={
                        "task": bridge.task_id,
                        "expected": current["base"],
                        "request_id": uuid.uuid4().hex,
                        "body": "Concurrent note while inspecting: preserve account order.",
                    },
                )
                assert status == 200, result
                wait(call, "window.__reviewRerenders>0")

            _select_candidate(call, candidate["id"], before_release=publish_during_candidate_read)
            _confirm_adoption(call)
            alternate = snapshot["working"]["id"]
            assert alternate != candidate["id"]
            _preview_then_change_selection(call, alternate)
            _select_candidate(call, candidate["id"])
            _confirm_adoption(call)
            _preview_adoption(call)
            wait(call, 'Boolean(document.querySelector(".session-material-review"))')
            assert evaluate(
                call,
                "document.body.innerText.includes("
                + json.dumps("External review note one: keep exports traceable.")
                + ")",
            )
            ids = evaluate(
                call,
                "[...document.querySelectorAll('.session-material-item input[type=checkbox]')]"
                ".map(node=>node.getAttribute('aria-label').slice('Include event '.length, "
                "-' in this reconciliation'.length))",
            )
            assert len(ids) >= 2
            assert len(ids) >= 3
            initial_pending = set(bridge.status()["material"]["pending_material_ids"])
            assert set(ids) == initial_pending
            first_id = note_one["input_id"]
            assert first_id in ids
            rationale_text = (
                "This request is represented in the selected specification and "
                "remains pending until reviewed."
            )
            evaluate(
                call,
                "(()=>{const id=" + json.dumps(first_id) + ";"
                "const card=[...document.querySelectorAll('.session-material-item')]"
                ".find(item=>item.querySelector('input[type=checkbox]')?.getAttribute("
                "'aria-label').includes(id+' in this reconciliation'));"
                "const include=card.querySelector('input[type=checkbox]');include.click();"
                "const decision=card.querySelector('select[aria-label^=\"Handling for event\"]');"
                "decision.value='incorporated';"
                "decision.dispatchEvent(new Event('change',{bubbles:true}));"
                "const reason=card.querySelector('textarea');reason.value="
                + json.dumps(rationale_text)
                + ";"
                "reason.dispatchEvent(new Event('input',{bubbles:true}));"
                "const refs=card.querySelector('select[multiple]');refs.options[0].selected=true;"
                "refs.dispatchEvent(new Event('change',{bubbles:true}));"
                "return true;})()",
            )
            assert evaluate(
                call,
                "[...document.querySelectorAll('.session-material-item input[type=checkbox]')]"
                ".filter(node=>node.checked).length===1",
            )
            before = bridge.base()
            evaluate(
                call,
                "window.__materialStream={source,handler:source.onmessage,queued:[]};"
                "source.onmessage=event=>window.__materialStream.queued.push(event);true",
            )
            snapshot = _http_json(server, f"/api/authoring?task={bridge.task_id}")[1]
            status, external = _http_json(
                server,
                "/api/authoring/followup",
                method="POST",
                body={
                    "task": bridge.task_id,
                    "expected": snapshot["base"],
                    "request_id": uuid.uuid4().hex,
                    "body": "Concurrent review note: retain export audit metadata.",
                },
            )
            assert status == 200, external
            evaluate(
                call,
                "window.__materialPosts=[];const original=window.fetch;"
                "window.fetch=async(...args)=>{"
                "const response=await original(...args);"
                "if(new URL(String(args[0]),location.href).pathname==='/api/authoring/reconcile')"
                "window.__materialPosts.push({status:response.status,request:args[1]?.body,"
                "body:await response.clone().text()});return response;};true",
            )
            click_text(
                call,
                "Adopt this revision and request review",
                'document.querySelector(".reconcile-preview")',
            )
            wait(call, "window.__materialPosts.length===1")
            failed = evaluate(call, "window.__materialPosts[0]")
            assert failed["status"] == 409, failed
            wait(call, 'Boolean(document.querySelector(".authoring-conflict-recovery"))')
            assert evaluate(
                call, "document.body.innerText.includes('Your draft and rejected request are kept')"
            )
            click_text(call, "Refresh latest state and keep my text")
            wait(call, "document.body.innerText.includes('Latest server state loaded')")
            # The preview and confirmations described the replaced state; adoption needs an
            # explicit new preview against the refreshed state (#139).
            assert evaluate(
                call,
                "!document.querySelector('.reconcile-preview button') && "
                "[...document.querySelectorAll("
                "'.authoring-reconcile > label input[type=checkbox]')].every(box=>!box.checked)",
            )
            _select_candidate(call, candidate["id"])
            _confirm_adoption(call)
            _preview_adoption(call)
            wait(call, 'Boolean(document.querySelector(".session-material-review"))')
            storage_key = (
                f"myharness-authoring:{receipt['project']}:{bridge.task_id}:"
                f"session-material-handling:{candidate['id']}"
            )
            recovered_fields = evaluate(
                call,
                "(()=>({fields:[...document.querySelectorAll('.session-material-item textarea')]"
                ".map(field=>({label:field.getAttribute('aria-label'),value:field.value})),"
                "storage:localStorage.getItem(" + json.dumps(storage_key) + "),"
                "review:document.querySelector('.session-material-review')?.innerText}))()",
            )
            assert any(
                row["label"] == "Reason for handling event " + first_id
                and "represented in the selected specification" in row["value"]
                for row in recovered_fields["fields"]
            ), recovered_fields
            assert evaluate(
                call,
                "(()=>{const id=" + json.dumps(first_id) + ";const box=[..."
                "document.querySelectorAll('.session-material-item input[type=checkbox]')]"
                ".find(node=>node.getAttribute('aria-label')"
                ".includes(id+' in this reconciliation'));"
                "return Boolean(box?.checked);})()",
            ), recovered_fields
            assert evaluate(
                call,
                "[...document.querySelectorAll('.session-material-item input[type=checkbox]')]"
                ".filter(node=>node.checked).length===1",
            )
            pending_before_reconcile = set(bridge.status()["material"]["pending_material_ids"])
            assert first_id in pending_before_reconcile
            click_text(
                call,
                "Adopt this revision and request review",
                'document.querySelector(".reconcile-preview")',
            )
            wait(call, "window.__materialPosts.length===2")
            succeeded = evaluate(call, "window.__materialPosts[1]")
            assert succeeded["status"] == 200, succeeded
            submitted = json.loads(succeeded["request"] or "null")
            assert submitted and submitted.get("handling") == [
                {
                    "input_id": first_id,
                    "disposition": "incorporated",
                    "rationale": rationale_text,
                    "references": ["revision:" + candidate["id"]],
                }
            ], {"submitted": submitted, "response": succeeded["body"]}
            pending = set(bridge.status()["material"]["pending_material_ids"])
            assert pending == pending_before_reconcile - {first_id}
            assert external["contribution"]
            assert bridge.base() != before
    finally:
        _shutdown(server, thread)


def test_chrome_attached_spec_and_diagram_edits_keep_buffers_and_use_real_http(
    db_path, tmp_path, monkeypatch, planning_session
):
    bridge, receipt, caller, capability = planning_session
    bridge.publish(attached_payload(bridge, receipt, "attached-edit-source"), caller, capability)
    monkeypatch.setattr(AuthoringWorker, "start", lambda self: False)
    server, thread = _server(db_path, bridge.task_id, bridge.root)
    try:
        with chrome(
            tmp_path / "chrome-attached-edit",
            server.url + f"?task={bridge.task_id}&tab=spec",
            1280,
            900,
        ) as call:
            wait(call, 'Boolean(document.querySelector(".spec-editor"))')
            set_theme(call, "dark")
            textarea = ".spec-editor"
            draft = "## Retained attached specification draft\n\nPreserve selected account order.\n"
            enter(call, textarea, draft)
            click_text(call, "Diagram")
            wait(call, 'Boolean(document.querySelector(".workspace-diagram-panel"))')
            click_text(call, "Spec")
            wait(
                call,
                "document.querySelector('.spec-editor')?.value.includes("
                "'Retained attached specification draft')",
            )
            call("Page.reload")
            wait(
                call,
                "document.querySelector('.spec-editor')?.value.includes("
                "'Retained attached specification draft')",
            )
            saved = "## Saved attached specification\n\nExport order is preserved.\n"
            evaluate(
                call,
                "window.__specPosts=[];const original=window.fetch;window.fetch=async(...args)=>{"
                "const response=await original(...args);"
                "if(String(args[0]).includes('/api/authoring/save-spec'))"
                "window.__specPosts.push({status:response.status,"
                "request:args[1]?.body});return response;};true",
            )
            enter(call, textarea, saved)
            click_text(call, "Save working specification")
            wait(call, "window.__specPosts.length===1")
            spec_post = evaluate(call, "window.__specPosts[0]")
            assert spec_post["status"] == 200
            assert json.loads(spec_post["request"])["body"] == saved
            wait(call, "document.body.innerText.includes('Working specification saved')")
            snapshot = _http_json(server, f"/api/authoring?task={bridge.task_id}")[1]
            assert snapshot["working"]["body"] == saved
            click_text(call, "Diagram")
            wait(
                call, 'Boolean(document.querySelector(".workspace-diagram-panel .workspace-node"))'
            )
            assert evaluate(
                call, "document.body.innerText.includes('Proposed logical system design')"
            )
            hit = evaluate(
                call,
                "(()=>{const node=document.querySelector("
                "'.workspace-diagram-panel .workspace-node');"
                "const rect=node.getBoundingClientRect();"
                "const x=rect.left+rect.width/2,y=rect.top+rect.height/2;"
                "const top=document.elementFromPoint(x,y);"
                "return {id:node.dataset.nodeId,role:node.getAttribute('role'),"
                "tabindex:node.getAttribute('tabindex'),rect:{left:rect.left,top:rect.top,"
                "right:rect.right,bottom:rect.bottom,width:rect.width,height:rect.height},"
                "viewport:{width:innerWidth,height:innerHeight},top:top?.outerHTML.slice(0,180),"
                "target:top?.closest('.workspace-node')?.dataset.nodeId||null};})()",
            )
            assert hit["id"] and hit["role"] == "button" and hit["tabindex"] == "0", hit
            assert hit["rect"]["width"] > 0 and hit["rect"]["height"] > 0, hit
            assert 0 <= hit["rect"]["left"] < hit["viewport"]["width"], hit
            assert 0 <= hit["rect"]["top"] < hit["viewport"]["height"], hit
            assert hit["target"] == hit["id"], hit
            x = (hit["rect"]["left"] + hit["rect"]["right"]) / 2
            y = (hit["rect"]["top"] + hit["rect"]["bottom"]) / 2
            call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
            call(
                "Input.dispatchMouseEvent",
                {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
            )
            call(
                "Input.dispatchMouseEvent",
                {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
            )
            wait(
                call,
                "document.querySelector('.workspace-diagram-panel select')?.value==="
                + json.dumps(hit["id"]),
            )
            selected = evaluate(
                call,
                "(()=>{const field=[...document.querySelectorAll("
                "'.workspace-inspector label')].find(label=>"
                "label.textContent.includes('Component label'))?.querySelector('input');"
                "return {label:field?.value||null,"
                "selection:document.querySelector('.workspace-diagram-panel select')?.value};})()",
            )
            assert selected["selection"] == hit["id"] and selected["label"], {
                "hit": hit,
                "selected": selected,
            }
            field_hit = evaluate(
                call,
                "(()=>{const field=[...document.querySelectorAll('.workspace-inspector label')]"
                ".find(label=>label.textContent.includes('Component label'))?"
                ".querySelector('input');"
                "const rect=field.getBoundingClientRect();const x=rect.left+rect.width/2;"
                "const y=rect.top+rect.height/2;const top=document.elementFromPoint(x,y);"
                "return {rect:{left:rect.left,top:rect.top,right:rect.right,bottom:rect.bottom},"
                "viewport:{width:innerWidth,height:innerHeight},target:top===field};})()",
            )
            assert (
                field_hit["target"]
                and 0 <= field_hit["rect"]["left"] < field_hit["viewport"]["width"]
            ), field_hit
            field_x = (field_hit["rect"]["left"] + field_hit["rect"]["right"]) / 2
            field_y = (field_hit["rect"]["top"] + field_hit["rect"]["bottom"]) / 2
            call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": field_x, "y": field_y})
            call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mousePressed",
                    "x": field_x,
                    "y": field_y,
                    "button": "left",
                    "clickCount": 1,
                },
            )
            call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "x": field_x,
                    "y": field_y,
                    "button": "left",
                    "clickCount": 1,
                },
            )
            assert evaluate(
                call,
                "(()=>{const field=[...document.querySelectorAll('.workspace-inspector label')]"
                ".find(label=>label.textContent.includes('Component label'))?"
                ".querySelector('input');"
                "return document.activeElement===field;})()",
            )
            evaluate(
                call,
                "(()=>{const field=[...document.querySelectorAll("
                "'.workspace-inspector label')].find(label=>"
                "label.textContent.includes('Component label'))?.querySelector('input');"
                "field.value=field.value+' reviewed';"
                "field.dispatchEvent(new Event('input',{bubbles:true}));return true;})()",
            )
            proposed_label = selected["label"] + " reviewed"
            call(
                "Input.dispatchKeyEvent",
                {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
            )
            call(
                "Input.dispatchKeyEvent",
                {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
            )
            click_text(call, "Save proposal")
            wait(call, "document.body.innerText.includes('Saved proposal')")
            dashboard = _http_json(server, f"/api/dashboard?task={bridge.task_id}")[1]
            proposals = dashboard["task"]["workspace"]["proposals"]
            assert proposals and proposals[-1]["operations"][-1]["label"] == proposed_label
            assert proposals[-1]["task_id"] == bridge.task_id
            assert dashboard["task"]["session"]["snapshot"]["mode"] == "attached"
            assert evaluate(call, "document.documentElement.scrollWidth<=innerWidth+1")
    finally:
        _shutdown(server, thread)


def test_chrome_source_replacement_keeps_corrected_question_history_readable(
    db_path, tmp_path, monkeypatch, planning_session
):
    from harness.authoring import AuthoringService
    from harness.interview import InterviewEngine
    from harness.investigation_store import InvestigationStore
    from tests.test_authoring import rework1b_apply
    from tests.test_investigation_review_regressions import source_publication

    bridge, receipt, caller, capability = planning_session
    bridge._test_caller, bridge._test_cap = caller, capability
    source_publication(bridge, receipt, "lineage-source-one")
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    store = InvestigationStore(bridge.controller, bridge.project, bridge.task_id)
    original_id = store.decisions()[0]["question_id"]
    original_answer = "Keep the original CSV behavior in the historical record."
    service.answer(
        expected=service.store.base(),
        request_id="lineage-answer-original",
        question=original_id,
        mode="answer",
        answer=original_answer,
    )
    rework1b_apply(
        service,
        f"question-response:{original_id}",
        {"mode": "unknown", "answer": "", "source": None, "ref": None},
        suffix="lineage-correction",
    )
    corrected_id = (
        InterviewEngine(bridge.controller, bridge.task_id)
        .effective()
        .current("question", original_id)
    )
    assert corrected_id != original_id
    source_publication(
        bridge,
        receipt,
        "lineage-source-two",
        premise="The replacement CSV premise changes account export behavior.",
        predecessor=corrected_id,
    )

    snapshot = service.snapshot()
    current_id = store.decisions()[0]["question_id"]
    assert current_id not in {original_id, corrected_id}
    assert {question["id"] for question in snapshot["questions"]} == {current_id}
    historical = {question["id"]: question for question in snapshot["historical"]["questions"]}
    assert historical[original_id]["answer"] == original_answer
    assert historical[corrected_id]["answer"] is None
    replacements = snapshot["source_lineage"]["replacements"]
    assert replacements[str(original_id)] == current_id
    assert replacements[str(corrected_id)] == current_id
    retired = {row["target"]: row for row in snapshot["revision_targets"] if not row["editable"]}
    old_target = retired[f"question-response:{original_id}"]
    assert old_target["current_target"] == f"question-response:{current_id}"

    monkeypatch.setattr(AuthoringWorker, "start", lambda self: False)
    server, thread = _server(db_path, bridge.task_id, bridge.root)
    try:
        code, served = _http_json(server, f"/api/authoring?task={bridge.task_id}")
        assert code == 200
        assert served["historical"]["questions"] == snapshot["historical"]["questions"]
        with chrome(
            tmp_path / "chrome-source-lineage-history",
            server.url + f"?task={bridge.task_id}&tab=history",
            390,
            844,
        ) as call:
            wait(
                call,
                'document.querySelector("#workspace-tab-history")?.getAttribute("aria-selected")==="true"',
            )
            wait(call, 'Boolean(document.querySelector(".authoring-source-lineage-history"))')
            set_theme(call, "dark")
            history_text = evaluate(
                call,
                "document.querySelector('.authoring-source-lineage-history').innerText",
            )
            assert original_answer in history_text, history_text
            assert f"Q{current_id}:" in history_text, history_text
            assert evaluate(call, "document.documentElement.scrollWidth<=innerWidth+1")
            assert evaluate(
                call,
                "document.querySelectorAll('[data-retired-question-id]').length===2",
            )
            evaluate(
                call,
                "document.querySelector('.authoring-source-lineage-history')"
                ".scrollIntoView({block:'start'});true",
            )
            wait(
                call,
                "(()=>{const rect=document.querySelector('.authoring-source-lineage-history')"
                ".getBoundingClientRect();return rect.top>=0&&rect.top<innerHeight-80;})()",
            )
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            capture = ARTIFACTS / f"source-lineage-history-390-dark-{uuid.uuid4().hex[:10]}.png"
            _capture(call, capture)
            click_text(call, f"Open current successor Q{current_id}")
            wait(call, 'Boolean(document.querySelector("dialog.authoring-drawer[open]"))')
            assert evaluate(
                call,
                "document.querySelector('dialog[open] .authoring-question')?.dataset.questionId==="
                + json.dumps(str(current_id)),
            )
            assert not evaluate(
                call,
                "Boolean(document.querySelector('[data-retired-question-id="
                + json.dumps(str(original_id))
                + "] button[data-authoring-form-key]'))",
            )
            click_text(call, "Close interview")
            click_text(call, "Interview")
            wait(call, 'Boolean(document.querySelector(".investigation-controls"))')
            click_text(call, "Load exact investigation review")
            wait(call, 'Boolean(document.querySelector(".investigation-source-history"))')
            evaluate(call, "document.querySelector('.investigation-source-history').open=true;true")
            package_history = evaluate(
                call,
                "document.querySelector('.investigation-source-history').innerText",
            )
            assert original_answer in package_history, package_history
            package = _http_json(server, f"/api/authoring?task={bridge.task_id}")[1]
            assert any(row["id"] == original_id for row in package["historical"]["questions"])
    finally:
        _shutdown(server, thread)


def test_chrome_preview_survives_a_live_repaint_while_its_request_is_in_flight(
    controller, db_path, tmp_path, monkeypatch
):
    """#139: a stream repaint during the preview request must not drop the preview or the
    operator's confirmations; adoption still needs both checkboxes and the preview."""
    root = tmp_path / "repaint-spec"
    root.mkdir()
    task = controller.create_task("Review a manually drafted export specification")
    monkeypatch.setattr(AuthoringWorker, "start", lambda self: False)
    server, thread = _server(db_path, task.id, root)
    try:
        with chrome(
            tmp_path / "chrome-repaint", server.url + f"?task={task.id}&tab=spec", 1280, 900
        ) as call:
            wait(call, 'Boolean(document.querySelector(".spec-editor"))')
            body = "## Export\n\nExport the selected accounts as CSV.\n"
            enter(call, ".spec-editor", body)
            click_text(call, "Save working specification")
            wait(call, "document.body.innerText.includes('Working specification saved')")
            candidate_id = _http_json(server, f"/api/authoring?task={task.id}")[1]["working"]["id"]
            click_text(call, "Review")
            _select_candidate(call, candidate_id)
            _confirm_adoption(call)
            evaluate(
                call,
                "(()=>{window.__held=[];let release;window.__gate=new Promise(r=>release=r);"
                "window.__release=release;const original=window.fetch;window.fetch=async(...a)=>{"
                "const response=await original(...a);"
                "if(String(a[0]).includes('/api/authoring/reconcile-preview')){"
                "window.__held.push(response.status);await window.__gate;}"
                "return response;};return true;})()",
            )
            click_text(call, "Preview explicit adoption")
            wait(call, "window.__held.length===1")
            # A live frame repaints the panel while the preview response is still pending.
            evaluate(call, "workspaceRerender();true")
            assert evaluate(call, "!document.querySelector('.reconcile-preview button')")
            evaluate(call, "window.__release();true")
            wait(call, 'Boolean(document.querySelector(".reconcile-preview button"))')
            assert evaluate(
                call,
                "[...document.querySelectorAll("
                "'.authoring-reconcile > label input[type=checkbox]')].every(box=>box.checked)",
            )
            click_text(
                call,
                "Adopt this revision and request review",
                'document.querySelector(".reconcile-preview")',
            )
            wait(call, "document.body.innerText.includes('Revision adopted')")
            # The used preview is gone: adopting again needs a new explicit preview.
            assert not evaluate(
                call,
                "[...document.querySelectorAll('.reconcile-preview button')].some(b=>"
                "b.textContent==='Adopt this revision and request review')",
            )
            assert (
                _http_json(server, f"/api/authoring?task={task.id}")[1]["adopted"]["body"] == body
            )
            assert evaluate(call, "document.documentElement.scrollWidth<=innerWidth+1")
    finally:
        _shutdown(server, thread)


def test_chrome_preview_is_dropped_when_a_live_frame_brings_a_newer_state(
    controller, db_path, tmp_path, monkeypatch
):
    """#139: a preview kept across repaints is only for its own authoring state; a live frame
    with a newer state (another client changed it) drops it until it is previewed again."""
    root = tmp_path / "superseded-spec"
    root.mkdir()
    task = controller.create_task("Review a manually drafted export specification")
    monkeypatch.setattr(AuthoringWorker, "start", lambda self: False)
    server, thread = _server(db_path, task.id, root)
    try:
        with chrome(
            tmp_path / "chrome-superseded", server.url + f"?task={task.id}&tab=spec", 1280, 900
        ) as call:
            wait(call, 'Boolean(document.querySelector(".spec-editor"))')
            enter(call, ".spec-editor", "## Export\n\nExport the selected accounts as CSV.\n")
            click_text(call, "Save working specification")
            wait(call, "document.body.innerText.includes('Working specification saved')")
            candidate_id = _http_json(server, f"/api/authoring?task={task.id}")[1]["working"]["id"]
            click_text(call, "Review")
            _select_candidate(call, candidate_id)
            _confirm_adoption(call)
            _preview_adoption(call)
            adopt = (
                "[...document.querySelectorAll('.reconcile-preview button')].some(b=>"
                "b.textContent==='Adopt this revision and request review')"
            )
            assert evaluate(call, adopt)
            before = _http_json(server, f"/api/authoring?task={task.id}")[1]
            status, changed = _http_json(
                server,
                "/api/authoring/followup",
                method="POST",
                body={
                    "task": task.id,
                    "expected": before["base"],
                    "request_id": str(uuid.uuid4()),
                    "body": "Another client added context after the preview.",
                },
            )
            assert status == 200, changed
            newer = _http_json(server, f"/api/authoring?task={task.id}")[1]["base"]["generation"]
            assert newer > before["base"]["generation"]
            # The live frame carrying the newer state re-renders the panel.
            wait(
                call,
                "workspaceStates.get(" + json.dumps(task.id) + ").authoring.base.generation"
                f"==={newer}",
            )
            assert not evaluate(call, adopt)
            assert evaluate(
                call,
                "[...document.querySelectorAll("
                "'.authoring-reconcile > label input[type=checkbox]')].every(box=>!box.checked)",
            )
    finally:
        _shutdown(server, thread)
