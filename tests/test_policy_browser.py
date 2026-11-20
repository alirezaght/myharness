# ruff: noqa: F811
"""Actual policy dashboard in desktop/narrow Chrome, with independently owned async requests."""

import json
import os
import shutil
import socket
import subprocess
import time
from importlib.resources import as_file
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import pytest

from harness.dashboard import server as server_module
from harness.dashboard.server import DashboardServer
from tests.test_dashboard import serve
from tests.test_dashboard_browser import CHROME
from tests.test_dashboard_execution_browser import _cdp_call

# Pytest discovers these re-exported fixture dependencies by name.
from tests.test_policy_admission import (  # noqa: F401
    attempt,
    configure,
    document,
    observed,
    patch,
    policy_runtime,
    processes,
)

pytestmark = [pytest.mark.browser, pytest.mark.skipif(CHROME is None, reason="Chrome unavailable")]


def capture_policy_view(
    server, task_id, profile: Path, output: Path, width: int, height: int, fixture_action
):
    profile.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            "--incognito",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            f"--user-data-dir={profile}",
            "--remote-debugging-port=0",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        port_file = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 10
        while not port_file.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Chrome exited before opening the DevTools port")
            time.sleep(0.05)
        if not port_file.exists():
            raise RuntimeError("Chrome DevTools port did not become ready")
        port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
        with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as response:
            target = next(row for row in json.load(response) if row.get("type") == "page")
        parsed = urlparse(target["webSocketDebuggerUrl"])
        sock = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        key = __import__("base64").b64encode(os.urandom(16)).decode()
        sock.sendall(
            (
                f"GET {parsed.path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        handshake = bytearray()
        while not handshake.endswith(b"\r\n\r\n"):
            handshake.extend(sock.recv(1))
        if b" 101 " not in handshake:
            raise RuntimeError("Chrome DevTools websocket handshake failed")
        sock.settimeout(10)
        call_id = 0

        def call(method, params=None):
            nonlocal call_id
            call_id += 1
            return _cdp_call(sock, call_id, method, params)

        call("Page.enable")
        call("Runtime.enable")
        call(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": "window.__policyErrors=[];window.addEventListener('"
                "error',e=>window.__policyErrors.push(String(e.mess"
                "age)));"
            },
        )
        call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": width < 600},
        )
        call("Page.navigate", {"url": f"{server.url}?task={task_id}"})
        deadline = time.monotonic() + 60
        handled_keyboard = set()
        handled_fixture = set()
        while time.monotonic() < deadline:
            state = (
                call(
                    "Runtime.evaluate",
                    {
                        "expression": "({done:Boolean(window.__reliabilityDriverDone), "
                        "keyboard:window.__reliabilityKeyboardRequest, "
                        "fixture:window.__reliabilityFixtureRequest})",
                        "returnByValue": True,
                    },
                )
                .get("result", {})
                .get("value", {})
            )
            keyboard = state.get("keyboard")
            if keyboard and keyboard["token"] not in handled_keyboard:
                call(
                    "Runtime.evaluate",
                    {
                        "expression": (
                            f"document.querySelector({json.dumps(keyboard['selector'])})?.focus()"
                        )
                    },
                )
                call(
                    "Input.dispatchKeyEvent",
                    {
                        "type": "rawKeyDown",
                        "key": "Enter",
                        "code": "Enter",
                        "windowsVirtualKeyCode": 13,
                        "nativeVirtualKeyCode": 36,
                    },
                )
                call(
                    "Input.dispatchKeyEvent",
                    {"type": "char", "key": "Enter", "code": "Enter", "text": "\r"},
                )
                call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter"})
                handled_keyboard.add(keyboard["token"])
                call(
                    "Runtime.evaluate",
                    {"expression": f"window.__reliabilityKeyboardDone={keyboard['token']}"},
                )
            fixture = state.get("fixture")
            if fixture and fixture["token"] not in handled_fixture:
                facts = fixture_action(fixture["action"])
                facts["token"] = fixture["token"]
                call(
                    "Runtime.evaluate",
                    {"expression": "window.__reliabilityFixtureResult=" + json.dumps(facts)},
                )
                handled_fixture.add(fixture["token"])
            if state.get("done"):
                break
            time.sleep(0.1)
        else:
            diagnostic = call(
                "Runtime.evaluate",
                {
                    "expression": "({ready:document.readyState,errors:window.__policy"
                    "Errors,body:document.body.innerText.slice(-4000)})",
                    "returnByValue": True,
                },
            )
            output.with_suffix(".json").write_text(json.dumps(diagnostic, indent=2))
            raise RuntimeError("policy browser driver did not finish; inspect saved diagnostic")
        driver_result = (
            call(
                "Runtime.evaluate",
                {
                    "expression": (
                        "JSON.parse(document.getElementById('reliability-br"
                        "owser-result').textContent)"
                    ),
                    "returnByValue": True,
                },
            )
            .get("result", {})
            .get("value", {})
        )
        output.with_suffix(".json").write_text(json.dumps(driver_result, indent=2) + "\n")
        if driver_result.get("error"):
            raise RuntimeError(f"incident screenshot browser flow failed: {driver_result['error']}")
        call(
            "Runtime.evaluate",
            {
                "expression": (
                    "document.querySelector('#execution-policy').scrollIntoView({block:'start'})"
                )
            },
        )
        capture = call(
            "Page.captureScreenshot",
            {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
        )
        output.write_bytes(__import__("base64").b64decode(capture["data"]))
        bounds = call(
            "Runtime.evaluate",
            {
                "expression": "(()=>{const r=document.querySelector('.policy-capabilities')"
                ".getBoundingClientRect();return {x:r.x+scrollX,y:r.y+scrollY,"
                "width:r.width,height:r.height,scale:1};})()",
                "returnByValue": True,
            },
        )["result"]["value"]
        capture = call(
            "Page.captureScreenshot",
            {"format": "png", "fromSurface": True, "captureBeyondViewport": True, "clip": bounds},
        )
        cap_output = output.with_name(output.stem + "-capabilities.png")
        cap_output.write_bytes(__import__("base64").b64decode(capture["data"]))
        assert cap_output.stat().st_size > 1024
        sock.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    assert output.is_file() and output.stat().st_size > 1024
    return driver_result


DRIVER = r"""
function waitFor(name,
predicate) {
 const start=Date.now();
 return new Promise((resolve,
reject)=>{

  const poll=()=>{
 const value=predicate();
 if(value)resolve(value);
 else if(Date.now()-start>10000)reject(new Error(name));
else setTimeout(poll,
30);
 }
;
poll();
 }
);
 }

function painted(){
return new Promise((resolve)=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
}

let token=0;

async function keyboard(node){
 node.focus();
const next=++token;

  window.__reliabilityKeyboardRequest={
token:next,
selector:'#'+node.id}
;

  await waitFor('keyboard',
()=>window.__reliabilityKeyboardDone===next);
 }

async function fixture(action){
const next=++token;
window.__reliabilityFixtureRequest={
token:next,
action}
;

 return waitFor('fixture',
()=>window.__reliabilityFixtureResult?.token===next&&window.__reliabilityFixtureResult);
}

function finish(value){
const node=document.createElement('pre');
node.id='reliability-browser-result';
node.hidden=true;
node.textContent=JSON.stringify(value);
document.body.append(node);
window.__reliabilityDriverDone=true;
}

document.addEventListener('DOMContentLoaded',
async()=>{
try{

 await waitFor('policy decisions',
()=>document.querySelectorAll('#execution-policy .policy-decision-list li').length===25);

 source?.close();
source=null;

 const section=()=>document.querySelector('#execution-policy');

 const state=()=>[...POLICY_VIEWS.values()].find(v=>v.identity===section().dataset.viewIdentity);

 const selected=[...section().querySelectorAll('.policy-decision-list li button')]
   .find(n=>!n.disabled);
await keyboard(selected);

 await waitFor('detail',
()=>section().querySelector('details p')?.textContent.includes('started'));

 const original=window.fetch.bind(window);
let hold=true;
const waiting=[];

 window.fetch=async(input,
options)=>{
const response=await original(input,
options);

  if(String(input).includes('/api/execution/policy?')
  &&!String(input).includes('attempt=')
  &&!String(input).includes('cap_after=')&&hold)
   return {
...response,
ok:response.ok,
json:()=>new Promise(resolve=>waiting.push(async()=>resolve(await response.json())))}
;

  return response;
}
;

 document.querySelector('#policy-decision').value='draft-authorization';
document.querySelector('#policy-decision').dispatchEvent(new Event('input'));

 document.querySelector('#policy-decision').focus();
document.querySelector('#policy-decision').setSelectionRange(3,
9);

 repaintExecution();
await waitFor('held refresh',
()=>waiting.length);

 const detailNode=section().querySelector('details');
detailNode.open=false;
detailNode.dispatchEvent(new Event('toggle'));

 await fixture('complete');

 // Parent repaint refreshes mutable selected details while the list request remains blocked.
 repaintExecution();
await waitFor('live completion',
()=>state().detail?.attempt.state==='settled');

 const independent=state().loading&&state().detail.attempt.state==='settled';

 hold=false;
for(const release of waiting.splice(0))await release();
await waitFor('list released',
()=>!state().loading);
await painted();

 const drafts=document.querySelector('#policy-decision').value==='draft-authorization';

 const focus=document.activeElement.id==='policy-decision'
 &&document.activeElement.selectionStart===3
 &&document.activeElement.selectionEnd===9;

 const collapsed=!section().querySelector('details').open;

 // Traverse bounded pages, then explicitly return to latest.
 const capSeen=new Set(state().capItems.map(item=>item.id));
 hold=true;
 repaintExecution();
 await waitFor('capability test held list',()=>waiting.length);
 const capCursor=state().capCursor;
 await keyboard(document.querySelector('#policy-caps-more'));
 await waitFor('independent capability page',
 ()=>!state().capLoading&&state().capPageAfter===capCursor);
 const capIndependent=state().loading&&state().capItems.length===25;
 for(const item of state().capItems)capSeen.add(item.id);
 hold=false;
 for(const release of waiting.splice(0))await release();
 await waitFor('capability held list released',()=>!state().loading);
 const capWindowRetained=state().capPageAfter===capCursor;
 while(state().capCursor){
   await keyboard(document.querySelector('#policy-caps-more'));
   await waitFor('next capability page',()=>!state().capLoading);
   for(const item of state().capItems)capSeen.add(item.id);
 }
 const capHistory=capSeen.size===105&&state().capItems.length<=25&&capWindowRetained;
 const capEndFocus=document.activeElement.id==='policy-caps-first';
 const labels=[...section().querySelectorAll('.policy-decision-list li button')]
   .map(node=>node.textContent);
 const distinctLabels=new Set(labels).size===labels.length;
 const seen=new Set(state().items.map(i=>i.id));
let pages=0;

 while(state().cursor){
document.querySelector('#policy-older').click();
await waitFor('older',
()=>!state().loading);
for(const item of state().items)seen.add(item.id);
if(++pages>20)throw new Error('history loop');
}

 const history=seen.size>=106&&state().items.length<=25;

 document.querySelector('#policy-latest').click();
await waitFor('latest',
()=>!state().loading);
await painted();

 const rect=section().getBoundingClientRect();

 const controls=[...section().querySelectorAll('input,select,button')].every(n=>{
const r=n.getBoundingClientRect();
return r.left>=rect.left-1&&r.right<=rect.right+1;
}
);

 const overflow=document.documentElement.scrollWidth>innerWidth+1;

 const safe=!section().textContent.includes('Authorization: Bearer policy-private')
 &&!section().querySelector('script,img');

 finish({
independent,
distinctLabels,
capIndependent,
capHistory,
capEndFocus,
capSeen:capSeen.size,
liveCompletion:state().detail.attempt.state==='settled',
drafts,
focus,
collapsed,
history,
seen:seen.size,
controls,
overflow,
safe}
);

}
catch(error){
finish({
error:String(error.stack||error)}
);
}
}
);

"""


@pytest.mark.parametrize("width,height", [(1280, 800), (390, 844)])
def test_policy_flow_in_real_chrome(policy_runtime, monkeypatch, tmp_path, width, height):
    r = policy_runtime
    configure(r, document(r, maximum=200))
    for index in range(105):
        r.operation.dispatch(r.principal, "apply_patch", patch(r, f"svc/file-{index}.txt"))
    from tests.test_policy_capabilities import grant

    for index in range(105):
        grant(r, identifier=f"cap-{index:03d}")
    pending = attempt(r)
    pending.start()
    copied = tmp_path / "policy-static"
    with as_file(server_module.STATIC_DIR) as directory:
        shutil.copytree(directory, copied)
    (copied / "policy-driver.js").write_text(DRIVER)
    page = copied / "index.html"
    body = page.read_text()
    anchor = '<script src="/static/app.js" defer></script>'
    assert body.count(anchor) == 1
    page.write_text(
        body.replace(anchor, anchor + '\n<script src="/static/policy-driver.js" defer></script>')
    )
    monkeypatch.setattr(server_module, "STATIC_DIR", copied)
    monkeypatch.setattr(
        server_module,
        "STATIC",
        server_module.STATIC
        | {"/static/policy-driver.js": ("policy-driver.js", "text/javascript; charset=utf-8")},
    )
    monkeypatch.setattr(DashboardServer, "stream_lifetime", 0.5)
    output = tmp_path / "policy-shots" / f"policy-{width}x{height}.png"
    output.parent.mkdir()

    def action(name):
        assert name == "complete"
        pending.finish()
        return {"complete": True}

    with serve(r.operation.database, r.operation.envelope["task_id"]) as server:
        result = capture_policy_view(
            server,
            r.operation.envelope["task_id"],
            tmp_path / "chrome",
            output,
            width,
            height,
            action,
        )
    for key in (
        "independent",
        "distinctLabels",
        "capIndependent",
        "capHistory",
        "capEndFocus",
        "liveCompletion",
        "drafts",
        "focus",
        "collapsed",
        "history",
        "controls",
        "safe",
    ):
        assert result[key], result
    assert not result["overflow"], result
