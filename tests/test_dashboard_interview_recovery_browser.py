"""Actual Chrome/HTTP interview recovery without a real provider."""

import json
import tempfile
import threading
import uuid
from pathlib import Path

import pytest

from harness.authoring_worker import AuthoringWorker
from harness.dashboard.server import DashboardServer
from harness.db import transaction
from tests.test_dashboard_authoring_browser import (
    CHROME,
    _capture,
    _capture_authoring_posts,
    _http_json,
    _set_theme,
)
from tests.test_dashboard_interview_recovery import interview_fixture
from tests.test_dashboard_live_view_state_browser import chrome, evaluate, wait

pytestmark = [pytest.mark.browser, pytest.mark.skipif(CHROME is None, reason="Chrome unavailable")]
ARTIFACTS = Path(tempfile.gettempdir()) / "harness-interview-recovery-artifacts" / "outputs"


def click_text(call, text, scope="document"):
    expression = (
        f"[...{scope}.querySelectorAll('button')].find(b=>b.textContent==={json.dumps(text)})"
    )
    wait(
        call,
        f"(()=>{{const button={expression};if(!button || button.disabled)return false;"
        "button.click();return true;})()",
    )


def query(selector):
    return f"document.querySelector({json.dumps(selector)})"


def enter(call, selector, value):
    wait(
        call,
        "(()=>{const input=document.querySelector("
        + json.dumps(selector)
        + ");if(!input)return false;input.value="
        + json.dumps(value)
        + ";input.dispatchEvent(new Event('input',{bubbles:true}));return true;})()",
    )


def set_theme(call, theme):
    _set_theme(call, theme)


@pytest.mark.parametrize(
    "width,theme", [(1280, "light"), (1280, "dark"), (390, "light"), (390, "dark")]
)
def test_chrome_interview_unknown_save_and_explicit_host_recovery(
    controller, db_path, tmp_path, monkeypatch, width, theme
):
    service, first, second = interview_fixture(controller, tmp_path)
    ws = controller.workspace(service.task_id)
    with transaction(controller.conn):
        duplicate = ws.ask(
            "scope",
            "Several packages found (`one/`, `two/`); which one does the request target?",
            source="investigation",
        )
    monkeypatch.setattr(
        "harness.authoring_policy.availability", lambda host: {"host": host, "available": True}
    )
    failed = service.request_regeneration(
        expected=service.store.base(), request_id="legacy-claude-1234", host="claude"
    )["job"]
    with transaction(controller.conn):
        controller.conn.execute(
            "UPDATE authoring_jobs SET state='failed',error='Native profile unavailable',"
            "settlement='settled' WHERE id=?",
            (failed,),
        )
        controller.conn.execute(
            "UPDATE authoring_attempts SET state='failed',settlement='settled' WHERE job_id=?",
            (failed,),
        )
    monkeypatch.setattr(
        "harness.authoring_policy.availability",
        lambda host: {
            "host": host,
            "available": host == "codex",
            "version": "fixture",
            "error": "Unsupported profile" if host == "claude" else None,
        },
    )
    from harness.authoring_policy import availability

    monkeypatch.setattr("harness.dashboard.authoring_view.availability", availability)
    monkeypatch.setattr(AuthoringWorker, "start", lambda self: False)
    server = DashboardServer(db_path, service.task_id, project_root=tmp_path)
    server.poll_seconds = 0.1
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with chrome(
            tmp_path / "chrome", server.url + f"?task={service.task_id}&tab=interview", width, 900
        ) as call:
            wait(call, 'Boolean(document.querySelector("#workspace-tab-interview"))')
            click_text(call, "Interview")
            _set_theme(call, theme)
            wait(call, "document.body.innerText.includes('Interview model needs attention')")
            wait(call, f"Boolean({query('[aria-label="Recovery interview model"]')})")
            assert (
                evaluate(call, query('[aria-label="Recovery interview model"]') + ".value")
                == "claude"
            )
            click_text(call, "Request a new response on selected host")
            wait(call, "document.body.innerText.includes('is unavailable')")
            assert len(_http_json(server, f"/api/authoring?task={service.task_id}")[1]["jobs"]) == 1
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            capture_id = uuid.uuid4().hex[:10]
            _capture(call, ARTIFACTS / f"recovery-{width}-{theme}-{capture_id}.png")
            evaluate(
                call,
                query('[aria-label="Recovery interview model"]')
                + ".closest('section').scrollIntoView({block:'start'});true",
            )
            _capture(call, ARTIFACTS / f"recovery-controls-{width}-{theme}-{capture_id}.png")
            click_text(call, f"Answer Q{first.id}")
            wait(call, 'Boolean(document.querySelector("dialog.authoring-drawer[open]"))')
            wait(call, 'document.querySelector("dialog[open]")?.innerText.includes("optional")')
            click_text(call, "I don’t know yet", 'document.querySelector("dialog[open]")')
            wait(
                call,
                f"Boolean({query(f'dialog[open] [data-question-id="{second.id}"]')})",
            )
            wait(
                call,
                "document.body.innerText.includes("
                "'remains unresolved; the next question is shown')",
            )
            assert (
                evaluate(
                    call,
                    query(f'button[data-workspace-focus="interview-drawer:{service.task_id}"]')
                    + ".textContent",
                )
                == f"Answer Q{second.id}"
            )
            wait(
                call, "document.getAnimations().every(animation=>animation.playState !== 'running')"
            )
            _capture(call, ARTIFACTS / f"drawer-{width}-{theme}-{capture_id}.png")
            assert evaluate(call, "document.documentElement.scrollWidth<=innerWidth+1")
            before_reload = evaluate(call, "performance.timeOrigin")
            call("Page.reload")
            wait(call, f"performance.timeOrigin !== {before_reload}")
            wait(
                call,
                f"Boolean({query(f'button[data-workspace-focus="interview-drawer:{service.task_id}"]')})",
            )
            click_text(call, f"Answer Q{second.id}")
            wait(
                call,
                f"Boolean({query('dialog[open] textarea')})",
            )
            enter(
                call,
                f'dialog[open] textarea[aria-label="Answer question {second.id}"]',
                "Users can export CSV",
            )
            click_text(call, "Save answer", 'document.querySelector("dialog[open]")')
            wait(call, f'document.body.innerText.includes("Answer for Q{second.id} saved")')
            snapshot = _http_json(server, f"/api/authoring?task={service.task_id}")[1]
            assert (
                next(q for q in snapshot["questions"] if q["id"] == second.id)["answer"]
                == "Users can export CSV"
            )
            assert snapshot["interview"]["current_question_id"] not in {
                first.id,
                second.id,
                duplicate.id,
            }
            click_text(call, "Close interview")
            click_text(call, f"Open Q{first.id} in drawer")
            wait(
                call,
                f"Boolean({query(f'dialog[open] [data-question-id="{first.id}"]')})",
            )
            wait(
                call,
                'document.querySelector("dialog[open]")?.innerText'
                '.includes("requirement remains unresolved")',
            )
            click_text(call, "Close interview")
            current = snapshot["interview"]["current_question_id"]
            enter(
                call,
                f'.authoring-question[data-question-id="{current}"] '
                f'textarea[aria-label="Answer question {current}"]',
                "I don't know",
            )
            click_text(
                call,
                "Save answer",
                query(f'.authoring-question[data-question-id="{current}"]'),
            )
            wait(call, "document.body.innerText.includes('save a non-answer as unresolved')")
            assert not evaluate(
                call, "Boolean(document.querySelector('.authoring-conflict-recovery'))"
            )
            assert (
                evaluate(
                    call,
                    query(f'textarea[aria-label="Answer question {current}"]') + ".value",
                )
                == "I don't know"
            )
            card = f'.authoring-question[data-question-id="{current}"]'
            evaluate(call, query(card + " details") + ".open=true;true")
            evaluate(
                call,
                "(()=>{const field="
                + query(card + ' [aria-label="Reason type"]')
                + ";field.value='codebase';"
                "field.dispatchEvent(new Event('change',{bubbles:true}));return true;})()",
            )
            enter(
                call,
                card + ' [aria-label="Deferral reason"]',
                "Existing behavior is documented in README",
            )
            click_text(call, "Save deferral", query(card))
            wait(call, f'document.body.innerText.includes("Deferral for Q{current} saved")')
            deferred = _http_json(server, f"/api/authoring?task={service.task_id}")[1]
            assert (
                next(q for q in deferred["questions"] if q["id"] == current)["skip_source"]
                == "codebase"
            )
            assert deferred["interview"]["current_question_id"] != current
            enter(call, '[aria-label="Follow-up message"]', "Additional context only")
            click_text(call, "Save follow-up only")
            wait(call, "document.body.innerText.includes('Follow-up saved as context')")
            after_followup = _http_json(server, f"/api/authoring?task={service.task_id}")[1]
            assert after_followup["interview"] == deferred["interview"]
            enter(call, '[aria-label="Follow-up message"]', "Saved even when Claude is unavailable")
            click_text(call, "Send to the interview model")
            wait(call, "document.body.innerText.includes('Your follow-up was saved as context')")
            after_send = _http_json(server, f"/api/authoring?task={service.task_id}")[1]
            assert after_send["interview"] == deferred["interview"]
            assert len(after_send["jobs"]) == 1
            evaluate(
                call,
                "(()=>{const host="
                + query('[aria-label="Recovery interview model"]')
                + ";host.value='codex';"
                "host.dispatchEvent(new Event('change',{bubbles:true}));return true;})()",
            )
            click_text(call, "Request a new response on selected host")
            wait(call, "document.body.innerText.includes('Request queued')")
            jobs = _http_json(server, f"/api/authoring?task={service.task_id}")[1]["jobs"]
            assert jobs[0]["id"] == failed and jobs[0]["state"] == "failed"
            assert jobs[-1]["host"] == "codex" and jobs[-1]["state"] == "queued"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        assert not thread.is_alive()


@pytest.mark.parametrize("width,theme", [(1280, "light"), (390, "dark")])
def test_chrome_attached_session_is_visible_and_pause_is_explicit(
    controller, db_path, tmp_path, monkeypatch, width, theme
):
    from tests.planning_session_fixture import ordinary_owner, request

    bridge, receipt, caller, capability = ordinary_owner(controller, tmp_path)
    delivery = bridge.wait(
        request(bridge, receipt, "attached-ui-initial-wait"), caller, capability, timeout_seconds=0
    )
    event_ids = [event["id"] for event in delivery["events"]]
    bridge.ack_received(
        request(bridge, receipt, "attached-ui-initial-ack")
        | {"delivery_id": delivery["delivery_id"], "event_ids": event_ids},
        caller,
        capability,
    )
    bridge.publish(
        request(bridge, receipt, "attached-ui-agent-message")
        | {
            "consumes": event_ids,
            "content": {
                "task_message": {"text": "I checked the export request and repository layout."}
            },
        },
        caller,
        capability,
    )
    monkeypatch.setattr(AuthoringWorker, "start", lambda self: False)
    server = DashboardServer(db_path, bridge.task_id, project_root=bridge.root)
    server.poll_seconds = 0.1
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with chrome(
            tmp_path / "chrome-session",
            server.url + f"?task={bridge.task_id}&tab=interview",
            width,
            900,
        ) as call:
            wait(call, "Boolean(document.querySelector('#workspace-tab-interview'))")
            _set_theme(call, theme)
            wait(call, "document.body.innerText.includes('Conversation with your invoking agent')")
            wait(call, "document.body.innerText.includes('Shared session started')")
            wait(
                call,
                "document.body.innerText.includes('I checked the export request a"
                "nd repository layout.')",
            )
            initial_session_revision = evaluate(
                call, "workspaceData.task.session.base.input_revision"
            )
            _capture_authoring_posts(call)
            assert (
                evaluate(call, "document.body.innerText.includes('Start / resume interview')")
                is False
            )
            assert (
                evaluate(call, "document.body.innerText.includes('Investigate this task')") is False
            )
            assert (
                evaluate(call, "document.body.innerText.includes('No authoring job recorded')")
                is False
            )
            assert (
                evaluate(call, "document.body.innerText.includes('Selected response host: codex')")
                is False
            )
            assert (
                evaluate(call, "document.body.innerText.includes('Model for the next response')")
                is False
            )
            assert (
                evaluate(call, "document.body.innerText.includes('Request model response')")
                is False
            )
            assert evaluate(
                call,
                "document.body.innerText.includes('Request a response from the co"
                "nnected Codex agent')",
            )
            assert evaluate(call, "document.body.innerText.includes('Open interview')") is False
            assert evaluate(call, "document.body.innerText.includes('ordinary')")
            assert (
                evaluate(
                    call,
                    "document.body.innerText.includes('test_chrome_attached_session_i0/ordinary')",
                )
                is False
            )
            assert evaluate(call, "document.body.innerText.includes('session.started')") is False
            assert (
                evaluate(call, "document.body.innerText.includes('Pause shared session')") is False
            )
            opened = evaluate(
                call,
                "(()=>{const details=document.querySelector("
                + json.dumps(f'[data-workspace-disclosure="session-details:{bridge.task_id}"]')
                + ");if(!details)return false;details.open=true;return true;})()",
            )
            assert opened
            assert evaluate(call, "document.body.innerText.includes('Pause shared session')")
            assert evaluate(call, "document.body.innerText.includes('Choose standalone mode')")
            evaluate(
                call,
                "(()=>{window.__sessionActions=[];const prior=window.fetch;window"
                ".fetch=async(...args)=>{"
                "const response=await prior(...args);if(String(args[0]).includes("
                "'/api/session/action'))"
                "window.__sessionActions.push({request:JSON.parse(args[1].body),s"
                "tatus:response.status,body:await response.clone().text()});"
                "return response;};return true;})()",
            )
            stored = evaluate(
                call,
                "localStorage.getItem("
                + json.dumps(f"myharness-workspace:{bridge.project}:{bridge.task_id}")
                + ")",
            )
            assert "capability" not in (stored or "").lower()
            assert evaluate(call, "document.documentElement.scrollWidth<=innerWidth+1")

            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            capture_id = uuid.uuid4().hex[:10]
            _capture(call, ARTIFACTS / f"shared-session-{width}-{theme}-{capture_id}.png")
            enter(
                call,
                '[aria-label="Follow-up message"]',
                "Please compare the current export flow with this request.",
            )
            click_text(call, "Send to your connected Codex conversation")
            wait(call, "window.__authoringPosts.length>=2")
            posts = evaluate(call, "window.__authoringPosts")
            followup_post = next(post for post in posts if post["url"].endswith("/followup"))
            response_post = next(post for post in posts if post["url"].endswith("/regenerate"))
            assert followup_post["status"] == 200
            assert (
                followup_post["request"]["body"]
                == "Please compare the current export flow with this request."
            )
            assert response_post["status"] == 200 and response_post["request"]["host"] == "codex"
            saved = _http_json(server, f"/api/authoring?task={bridge.task_id}")[1]
            assert any(
                record["kind"] == "followup"
                and record["payload"].get("body")
                == "Please compare the current export flow with this request."
                for record in saved["contributions"]
            )
            assert saved["jobs"] == []
            wait(
                call,
                "(()=>{const input=document.querySelector('[aria-label=\"Follow-up message\"]');"
                "return input?.value==='' && workspaceData.task.session.base.input_revision >= "
                + str(initial_session_revision + 2)
                + " && document.body.innerText.includes('Saved.');})()",
            )
            click_text(call, "Pause shared session")
            wait(call, "window.__sessionActions.length>=1")
            session_actions = evaluate(call, "window.__sessionActions")
            failed_action = session_actions[-1]
            if failed_action["status"] != 200:
                current = json.loads(failed_action["body"]).get("current")
                visible_base = evaluate(call, "workspaceData.task.session.base")
                pytest.fail(
                    "pause returned HTTP "
                    f"{failed_action['status']}; "
                    f"expected={json.dumps(failed_action['request']['expected'], sort_keys=True)}; "
                    f"current={json.dumps(current, sort_keys=True)}; "
                    "visible_base="
                    f"visible_base={json.dumps(visible_base, sort_keys=True)}"
                )
            wait(call, "document.body.innerText.includes('Shared session paused')")
            from harness.planning_session_store import PlanningSessionStore

            with bridge._transaction():
                assert (
                    PlanningSessionStore(bridge.conn, bridge.task_id).contact()["state"]
                    == "disconnected"
                )
            assert evaluate(call, "document.documentElement.scrollWidth<=innerWidth+1")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        assert not thread.is_alive()


def test_chrome_clock_unavailable_explains_same_command_recovery_without_reattach(
    controller, db_path, tmp_path, monkeypatch
):
    from harness.planning_session_clock import BootObservation
    from tests.planning_session_fixture import ordinary_owner

    bridge, _, _, _ = ordinary_owner(controller, tmp_path)
    monkeypatch.setattr(
        "harness.planning_session_store.boot_observation",
        lambda: BootObservation(None, "permission_denied"),
    )
    monkeypatch.setattr(AuthoringWorker, "start", lambda self: False)
    server = DashboardServer(db_path, bridge.task_id, project_root=bridge.root)
    server.poll_seconds = 0.1
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with chrome(
            tmp_path / "chrome-clock-unavailable",
            server.url + f"?task={bridge.task_id}&tab=interview",
            390,
            844,
        ) as call:
            wait(call, "Boolean(document.querySelector('#workspace-tab-interview'))")
            _set_theme(call, "light")
            wait(call, "document.body.innerText.includes('cannot verify its lease')")
            assert evaluate(call, "document.body.innerText.includes('permission_denied')")
            assert evaluate(
                call,
                "document.body.innerText.includes('Ask the invoking provider to r"
                "un the same foreground Harness command with host permission')",
            )
            assert (
                evaluate(call, "document.body.innerText.includes('Save a reconnect request')")
                is False
            )
            assert (
                evaluate(call, "document.body.innerText.includes('Choose standalone mode')")
                is False
            )
            assert (
                evaluate(
                    call,
                    "document.body.innerText.includes('Request a response from the co"
                    "nnected Codex agent')",
                )
                is False
            )
            recovery_controls = evaluate(
                call,
                "(()=>{const buttons=[...document.querySelectorAll('button')];"
                "const send=buttons.find(button=>button.textContent.startsWith("
                "'Send after Codex session recovery'));"
                "const save=buttons.find(button=>button.textContent==='Save follow-up only');"
                "return {sendDisabled:send?.disabled,"
                "saveEnabled:Boolean(save&&!save.disabled)};})()",
            )
            assert recovery_controls == {"sendDisabled": True, "saveEnabled": True}
            assert evaluate(call, "document.documentElement.scrollWidth<=innerWidth+1")
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            _capture(
                call, ARTIFACTS / f"shared-session-clock-unavailable-{uuid.uuid4().hex[:10]}.png"
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        assert not thread.is_alive()
