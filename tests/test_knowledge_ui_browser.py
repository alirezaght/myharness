"""Real Chrome coverage for graph interactions, refresh truth and visual artifacts."""

import base64
import json
import re
from pathlib import Path

import pytest

from tests.test_dashboard_browser import CHROME, _headless

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not installed"),
]

ARTIFACTS = Path("/private/tmp/al-knowledge-platform-20261116/browser-artifacts")


def _json_for_script(value):
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def _visible_dom(output):
    return re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", output, flags=re.IGNORECASE)


def _page(static_dir, script):
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<link rel='stylesheet' href='file://{static_dir / 'app.css'}'>"
        "<style>body>main#out{display:block;grid-template-columns:none;width:100%;"
        "max-width:72rem;box-sizing:border-box;margin:0 auto;padding:1rem}</style>"
        f"<script src='file://{static_dir / 'knowledge.js'}'></script>"
        "</head><body><main id='out' class='knowledge-panel'></main><script>"
        + script
        + "</script></body></html>"
    )


def test_graph_is_bounded_keyboard_linked_safe_and_screenshot_ready(tmp_path):
    from harness.dashboard.server import STATIC_DIR

    hostile = "<img src=x onerror=window.__executed=true> " + "component-" * 12
    bundle = {
        "bundle_hash": "fixture-bundle",
        "snapshot_id": "fixture-snapshot",
        "budget": {"serialized_bytes": 512, "max_bytes": 32768, "max_assertions": 40},
        "notice": "Context grants no execution or approval authority.",
        "coverage": [],
        "conflicts": [],
        "staleness": [],
        "paths": [["a-one", "a-two"]],
        "assertions": [
            {
                "assertion_id": "a-one",
                "subject": hostile,
                "predicate": "imports",
                "object": "module:service",
                "text": "source span one",
                "origin": "static-analysis",
                "verification": "source-backed",
                "historical": False,
            },
            {
                "assertion_id": "a-two",
                "subject": "module:service",
                "predicate": "references",
                "object": "module:database",
                "text": "source span two",
                "origin": "attributed-inference",
                "verification": "unverified",
                "historical": True,
            },
        ],
    }
    script = (
        "window.__executed=false;"
        f"renderKnowledgeResults(document.getElementById('out'),'t-one',{_json_for_script(bundle)});"
        "const nodes=[...document.querySelectorAll('[data-knowledge-node]')];"
        "nodes[0].focus();nodes[0].dispatchEvent(new KeyboardEvent('keydown',"
        "{key:'Enter',bubbles:true}));"
        "const focused=document.activeElement.classList.contains('knowledge-assertion');"
        "const safe=document.querySelectorAll('img').length===0&&!window.__executed;"
        "const trust=document.getElementById('knowledge-evidence-a-two')"
        ".textContent.includes('historical')"
        "&&document.getElementById('knowledge-evidence-a-two').textContent.includes('unverified');"
        "const bounded=nodes.length<=12&&document.querySelectorAll('.knowledge-arrow').length<=16;"
        "const serviceTrust=document.querySelector("
        "'[data-knowledge-node=\"module:service\"] .knowledge-node-state').textContent;"
        "const trustDeduplicated=serviceTrust==='source-backed / unverified · historical';"
        "const noPageOverflow=document.documentElement.scrollWidth<=window.innerWidth;"
        "const metrics={innerWidth:window.innerWidth,visualWidth:window.visualViewport.width,"
        "graphRight:document.querySelector('.knowledge-graph').getBoundingClientRect().right,"
        "panelRight:document.querySelector('.knowledge-panel').getBoundingClientRect().right,"
        "paragraphsFit:[...document.querySelectorAll('.knowledge-panel p')].every(p=>"
        "p.scrollWidth<=p.clientWidth+1)};"
        "const viewportFits=Math.abs(metrics.innerWidth-metrics.visualWidth)<1"
        "&&metrics.panelRight<=metrics.innerWidth+1&&metrics.paragraphsFit;"
        "document.body.append(document.createTextNode((safe&&focused&&trust&&bounded&&trustDeduplicated&&noPageOverflow"
        "&&viewportFits?'GRAPH_UI_PASS ':'GRAPH_UI_FAIL ')+JSON.stringify(metrics)));"
    )
    html = tmp_path / "knowledge-graph.html"
    html.write_text(_page(STATIC_DIR, script), encoding="utf-8")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    captures = []
    from tests.test_dashboard_live_view_state_browser import chrome, evaluate, wait

    for width, height, name in (
        (1280, 800, "knowledge-graph-desktop.png"),
        (390, 844, "knowledge-graph-narrow.png"),
    ):
        artifact = ARTIFACTS / name
        with chrome(tmp_path / f"graph-profile-{width}", html.as_uri(), width, height) as call:
            wait(call, "document.readyState === 'complete'")
            wait(call, "document.body.textContent.includes('GRAPH_UI_')")
            result = evaluate(
                call, "document.body.textContent.match(/GRAPH_UI_(?:PASS|FAIL) (\\{[^}]+\\})/)?.[1]"
            )
            assert result, evaluate(call, "document.body.innerText")
            metrics = json.loads(result)
            visible = evaluate(call, "document.body.innerText")
            assert "GRAPH_UI_PASS" in visible, metrics
            assert abs(metrics["innerWidth"] - width) < 1, metrics
            assert abs(metrics["visualWidth"] - width) < 1, metrics
            shot = call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
            artifact.write_bytes(base64.b64decode(shot["data"]))
        assert artifact.is_file() and artifact.stat().st_size > 0
        captures.append(str(artifact))
    print("knowledge graph screenshots: " + ", ".join(captures))


def test_refresh_status_retains_incomplete_diagnostics_and_network_recovery(tmp_path):
    from harness.dashboard.server import STATIC_DIR

    locator = "src/" + "very-long-component-path/" * 14 + "large.json"
    attempts = [
        {
            "status": "incomplete",
            "coverage": [
                {
                    "status": "capped",
                    "reason": "source-size-capped",
                    "locator": locator,
                    "limit": 1048576,
                }
            ],
        },
        {
            "status": "incomplete",
            "coverage": [
                {
                    "status": "unreadable",
                    "reason": "permission denied",
                    "locator": locator,
                    "limit": 1048576,
                }
            ],
        },
        {"status": "published", "coverage": []},
        {
            "status": "incomplete",
            "coverage": [
                {
                    "status": "capped",
                    "reason": "refresh-bytes-capped",
                    "locator": locator,
                    "limit": 67108864,
                }
            ],
        },
    ]
    statuses = [
        {
            "status": "not-indexed",
            "active_snapshot_id": None,
            "source_count": 0,
            "entity_count": 0,
            "relationship_count": 0,
            "latest_refresh": {"status": "incomplete", "coverage": attempts[0]["coverage"]},
            "snapshot_coverage": [],
        },
        {
            "status": "indexed",
            "active_snapshot_id": "snapshot-last-complete",
            "source_count": 19,
            "entity_count": 20,
            "relationship_count": 22,
            "latest_refresh": {"status": "incomplete", "coverage": attempts[1]["coverage"]},
            "snapshot_coverage": [
                {"status": "limited", "reason": "text-only-no-dependency-analysis"}
            ],
        },
        {
            "status": "indexed",
            "active_snapshot_id": "snapshot-new-complete",
            "source_count": 21,
            "entity_count": 22,
            "relationship_count": 24,
            "latest_refresh": {"status": "published", "coverage": []},
            "snapshot_coverage": [
                {"status": "limited", "reason": "text-only-no-dependency-analysis"}
            ],
        },
    ]
    script = (
        "(async()=>{try{"
        f"const statusFixtures={_json_for_script(statuses)};"
        f"const attemptFixtures={_json_for_script(attempts)};"
        "let statusIndex=0,attemptIndex=0,failInspect=false;"
        "window.fetch=async()=>{if(failInspect){failInspect=false;"
        "throw new Error('status offline');}"
        "const data=statusFixtures[Math.min(statusIndex++,statusFixtures.length-1)];"
        "return {ok:true,json:async()=>data};};"
        "window.postJson=async()=>{const attempt=attemptFixtures[attemptIndex++];"
        "if(attemptIndex===3)return {res:null,data:{},netErr:'offline'};"
        "if(attemptIndex===4)failInspect=true;return {res:{ok:true},data:attempt};};"
        "const panel=renderKnowledgePanel('t-refresh');"
        "document.getElementById('out').append(panel);"
        "const pause=()=>new Promise(resolve=>setTimeout(resolve,80));"
        "const button=panel.querySelector('.knowledge-refresh');await pause();"
        "const initial=panel.querySelector('.knowledge-status').textContent;"
        "button.click();await pause();"
        "const retained=panel.querySelector('.knowledge-status').textContent;"
        "button.click();await pause();"
        "const recovered=panel.querySelector('.knowledge-status').textContent;"
        "button.click();await pause();"
        "const network=panel.querySelector('.knowledge-status').textContent;"
        "button.click();await pause();"
        "const reloadFailure=panel.querySelector('.knowledge-status').textContent;"
        "panel.querySelectorAll('.knowledge-coverage').forEach(details=>details.open=true);"
        "const available=!button.disabled;"
        "const noPageOverflow=document.documentElement.scrollWidth<=window.innerWidth;"
        "const ok=initial.includes('No knowledge snapshot yet. Refresh incomplete.')"
        "&&initial.includes('source-size-capped')"
        "&&retained.includes('Active knowledge snapshot retained. Latest refresh incomplete.')"
        "&&retained.includes('snapshot-last-complete')&&retained.includes('text-only-no-dependency-analysis')"
        "&&recovered.includes('Index available')&&recovered.includes('snapshot-new-complete')"
        "&&network.includes('offline')"
        "&&reloadFailure.toLowerCase().includes('refresh was incomplete')"
        "&&reloadFailure.includes('refresh-bytes-capped')&&available&&noPageOverflow;"
        "document.body.append(document.createTextNode((ok?'REFRESH_UI_PASS ':'REFRESH_UI_FAIL ')+"
        "JSON.stringify({initial,retained,recovered,network,reloadFailure,available,noPageOverflow})));"
        "}catch(error){document.body.append(document.createTextNode(`REFRESH_UI_ERROR:${error.message}`));}})();"
    )
    html = tmp_path / "knowledge-refresh.html"
    html.write_text(_page(STATIC_DIR, script), encoding="utf-8")
    for width, height in ((1280, 800), (390, 844)):
        result = _headless(
            tmp_path / f"refresh-profile-{width}",
            f"--window-size={width},{height}",
            "--dump-dom",
            html.as_uri(),
        )
        assert result.returncode == 0, result.stderr
        visible = _visible_dom(result.stdout)
        marker = re.search(r"REFRESH_UI_(?:PASS|FAIL) (\{[^}]+\})", visible)
        assert marker, visible[-1000:]
        diagnostic = json.loads(marker.group(1))
        assert "REFRESH_UI_PASS" in visible, json.dumps(diagnostic)


def test_empty_result_integrity_and_panel_state_survive_status_poll(tmp_path):
    from harness.dashboard.server import STATIC_DIR

    script = (
        "(async()=>{try{"
        "let poll;window.setInterval=(callback)=>{poll=callback;return 1;};"
        "window.clearInterval=()=>{};"
        "window.fetch=async()=>({ok:true,json:async()=>({status:'indexed',"
        "active_snapshot_id:'snapshot-1',source_count:1,entity_count:1,relationship_count:0,"
        "latest_refresh:{status:'unchanged',coverage:[]},snapshot_coverage:["
        "{status:'limited',reason:'text-only'}]})});"
        "window.postJson=async()=>({res:{ok:true},data:{}});"
        "document.body.style.minHeight='1400px';"
        "const panel=renderKnowledgePanel('task-1');document.getElementById('out').append(panel);"
        "await new Promise(resolve=>setTimeout(resolve,50));"
        "const bundle={bundle_hash:'b1',snapshot_id:'s1',"
        "budget:{serialized_bytes:0,max_bytes:32768,"
        "max_assertions:40},assertions:[],coverage:[{status:'source-unknown',reason:'unreadable'}],"
        "ambiguities:[{status:'ambiguous-identity',reason:'two candidates'}],conflicts:[],"
        "staleness:[{status:'withheld-stale-evidence'}],exclusions:{'source-read-cap':2}};"
        "renderKnowledgeResults(panel.querySelector('.knowledge-results'),'task-1',bundle);"
        "const input=panel.querySelector('input');input.value='typed search';"
        "const details=[...panel.querySelectorAll('.knowledge-results details')];"
        "details.forEach(item=>item.open=true);"
        "const before=panel.querySelector('.knowledge-results').textContent;"
        "const statusSummary=panel.querySelector('.knowledge-status details summary');"
        "statusSummary.focus();window.scrollTo(0,160);"
        "await new Promise(resolve=>setTimeout(resolve,25));const scrollBefore=window.scrollY;"
        "poll();await new Promise(resolve=>setTimeout(resolve,50));"
        "const after=panel.querySelector('.knowledge-results').textContent;"
        "const visible=['No matching observations','unreadable','ambiguous-identity',"
        "'withheld-stale-evidence','source-read-cap'].every(value=>before.includes(value));"
        "const preserved=input.value==='typed search'&&after===before"
        "&&[...panel.querySelectorAll('.knowledge-results details')].every(item=>item.open);"
        "const focusPreserved=document.activeElement==="
        "panel.querySelector('.knowledge-status details summary')"
        "&&window.scrollY===scrollBefore;"
        "document.body.append(document.createTextNode(visible&&preserved&&focusPreserved"
        "?'EMPTY_UI_PASS':'EMPTY_UI_FAIL'));"
        "}catch(error){document.body.append(document.createTextNode(`EMPTY_UI_ERROR:${error.message}`));}})();"
    )
    html = tmp_path / "knowledge-empty.html"
    html.write_text(_page(STATIC_DIR, script), encoding="utf-8")
    result = _headless(tmp_path / "knowledge-empty-profile", "--dump-dom", html.as_uri())
    assert result.returncode == 0, result.stderr
    assert "EMPTY_UI_PASS" in _visible_dom(result.stdout)


def test_partial_snapshot_long_observation_accessibility_and_refresh_poll(tmp_path):
    from harness.dashboard.server import STATIC_DIR
    from tests.test_dashboard_live_view_state_browser import chrome, evaluate, wait

    observation = "<img src=x onerror=window.__xss=true>" + "x" * (
        8275 - len("<img src=x onerror=window.__xss=true>")
    )
    initial_status = {
        "status": "indexed",
        "active_snapshot_id": "partial-snapshot-1",
        "source_count": 1310,
        "entity_count": 9407,
        "relationship_count": 18963,
        "latest_refresh": {
            "status": "published-partial",
            "coverage": [{"status": "excluded", "reason": "sensitive-source"}],
        },
        "snapshot_coverage": [{"status": "excluded", "reason": "sensitive-source"}],
    }
    retained_status = {
        **initial_status,
        "latest_refresh": {
            "status": "incomplete",
            "coverage": [{"status": "excluded", "reason": "sensitive-source"}],
        },
    }
    script = (
        "(async()=>{"
        "let statusIndex=0,pollStatus=null;window.__statusTimerMs=null;window.__xss=false;"
        "window.setInterval=(callback,ms)=>{pollStatus=callback;"
        "window.__statusTimerMs=ms;return 7;};"
        "window.clearInterval=()=>{};window.__pollStatus=()=>pollStatus();"
        f"const statuses={_json_for_script([initial_status, retained_status])};"
        "window.fetch=async(url)=>{if(String(url).startsWith('/api/knowledge/evidence?'))"
        "return {ok:true,json:async()=>({citations:[{locator:'src/ApprovalGate.py',"
        "text:'exact cited bytes',truncated:false}]})};"
        "const data=statuses[Math.min(statusIndex++,statuses.length-1)];"
        "return {ok:true,json:async()=>data};};"
        "window.postJson=async(url)=>String(url).endsWith('/refresh')"
        "?{res:{ok:true},data:{status:'incomplete',coverage:[{status:'excluded',reason:'sensitive-source'}]}}"
        ":{res:{ok:true},data:{authority:'Source-backed explanation.',citations:[{"
        "locator:'src/ApprovalGate.py',"
        "start_byte:0,end_byte:16,span_sha256:'abc123'}]}};"
        "document.body.style.minHeight='1800px';"
        f"window.__observation={_json_for_script(observation)};"
        "const panel=renderKnowledgePanel('task-partial');"
        "document.getElementById('out').append(panel);"
        "await new Promise(resolve=>setTimeout(resolve,40));"
        "window.__initialStatus=panel.querySelector('.knowledge-status').textContent;"
        "const bundle={bundle_hash:'bundle-partial',snapshot_id:'partial-snapshot-1',"
        "budget:{serialized_bytes:9000,max_bytes:32768,max_assertions:40},assertions:[{"
        "assertion_id:'long-described-by',subject:'module:ApprovalGate',predicate:'described-by',"
        "object:null,text:"
        f"{_json_for_script(observation)},"
        "origin:'static-analysis',verification:'source-backed',historical:false}],"
        "coverage:[],conflicts:[],staleness:[],paths:[]};"
        "renderKnowledgeResults(panel.querySelector('.knowledge-results'),'task-partial',bundle);"
        "window.__bundleRendered=true;})();"
    )
    html = tmp_path / "knowledge-long-observation.html"
    html.write_text(_page(STATIC_DIR, script), encoding="utf-8")
    artifacts = Path("/private/tmp/knowledge-completion-inputs-20261116/ui-polish-artifacts")
    artifacts.mkdir(parents=True, exist_ok=True)

    for width, height, name in (
        (1280, 800, "knowledge-ui-polish-desktop.png"),
        (390, 844, "knowledge-ui-polish-narrow.png"),
    ):
        with chrome(tmp_path / f"polish-profile-{width}", html.as_uri(), width, height) as call:
            wait(call, "window.__bundleRendered === true")
            before = evaluate(
                call,
                "(()=>{const article=document.querySelector('.knowledge-assertion');"
                "const details=article.querySelector('.knowledge-observation-full');"
                "return {status:document.querySelector('.knowledge-status').textContent,"
                "label:document.querySelector('.knowledge-coverage summary')?.textContent,"
                "excerpt:article.querySelector('p').textContent,"
                "full:article.querySelector('.knowledge-observation-full p')?.textContent,"
                "closed:!details?.open,hasExplain:[...article.querySelectorAll('button')]"
                ".some(b=>b.textContent==='Explain evidence'),"
                "safe:article.querySelectorAll('img').length===0&&!window.__xss,"
                "viewport:window.innerWidth,visual:window.visualViewport.width,"
                "paragraphsFit:[...document.querySelectorAll('.knowledge-panel p')]"
                ".every(p=>p.scrollWidth<=p.clientWidth+1)}})()",
            )
            assert (
                "Index available — latest refresh published partial coverage." in before["status"]
            )
            assert before["label"] == "Coverage of active snapshot (1)"
            assert len(before["excerpt"]) <= 340 and before["excerpt"].endswith("…")
            assert before["full"] == observation and before["closed"]
            assert before["hasExplain"] and before["safe"]
            assert before["viewport"] == before["visual"] == width
            assert before["paragraphsFit"]

            # Native summary keyboard behavior expands and collapses the literal observation.
            evaluate(call, "document.querySelector('.knowledge-observation-full summary').focus()")
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
                {
                    "type": "char",
                    "key": "Enter",
                    "code": "Enter",
                    "text": "\r",
                },
            )
            call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter"})
            wait(call, "document.querySelector('.knowledge-observation-full').open")
            assert evaluate(
                call,
                "document.querySelector('.knowledge-observation-full p').textContent==="
                "window.__observation",
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
                {
                    "type": "char",
                    "key": "Enter",
                    "code": "Enter",
                    "text": "\r",
                },
            )
            call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter"})
            wait(call, "!document.querySelector('.knowledge-observation-full').open")

            evaluate(call, "document.querySelector('.knowledge-assertion button').click()")
            wait(
                call,
                "[...document.querySelectorAll('.knowledge-assertion button')]"
                ".some(b=>b.textContent==='View cited source text')",
            )
            evaluate(
                call,
                "[...document.querySelectorAll('.knowledge-assertion button')]"
                ".find(b=>b.textContent==='View cited source text').click()",
            )
            wait(
                call,
                "document.querySelector('.knowledge-source-text').textContent"
                ".includes('exact cited bytes')",
            )

            evaluate(call, "document.querySelector('.knowledge-refresh').click()")
            wait(
                call,
                "document.querySelector('.knowledge-status').textContent"
                ".includes('Latest refresh incomplete')",
            )
            after_refresh = evaluate(
                call,
                "({status:document.querySelector('.knowledge-status').textContent,"
                "label:document.querySelector('.knowledge-coverage summary')?.textContent})",
            )
            assert (
                "Active knowledge snapshot retained. Latest refresh incomplete."
                in after_refresh["status"]
            )
            assert "partial-snapshot-1" in after_refresh["status"]
            assert after_refresh["label"] == "Coverage of active snapshot (1)"
            assert "last complete" not in after_refresh["status"].lower()

            # Keep the observation and status disclosures open through the 15 second poll.
            evaluate(
                call,
                "document.querySelector('.knowledge-observation-full').open=true;"
                "document.querySelector('.knowledge-results details')?.setAttribute('open','');"
                "document.querySelector('.knowledge-status details').open=true;"
                "document.querySelector('.knowledge-query-form input').value='ApprovalGate';"
                "document.querySelector('.knowledge-status details summary')"
                ".focus({preventScroll:true});"
                "window.scrollTo(0,800);",
            )
            before_poll = evaluate(
                call,
                "({results:document.querySelector('.knowledge-results').textContent,"
                "y:window.scrollY,"
                "query:document.querySelector('.knowledge-query-form input').value})",
            )
            assert evaluate(call, "window.__statusTimerMs") == 15000
            evaluate(call, "window.__pollStatus()")
            wait(
                call,
                "document.querySelector('.knowledge-status').textContent"
                ".includes('Latest refresh incomplete')",
            )
            after_poll = evaluate(
                call,
                "({results:document.querySelector('.knowledge-results').textContent,"
                "y:window.scrollY,"
                "query:document.querySelector('.knowledge-query-form input').value,"
                "observationOpen:document.querySelector('.knowledge-observation-full').open,"
                "focus:document.activeElement.textContent.trim(),"
                "statusOpen:document.querySelector('.knowledge-status details').open})",
            )
            assert after_poll["results"] == before_poll["results"]
            assert (
                after_poll["y"] == before_poll["y"] and after_poll["query"] == before_poll["query"]
            )
            assert after_poll["observationOpen"] and after_poll["statusOpen"]
            assert after_poll["focus"] == "Coverage of active snapshot (1)"

            evaluate(
                call,
                "document.querySelector('.knowledge-observation-full').open=false;"
                "const summary=document.querySelector('.knowledge-observation-full summary');"
                "window.scrollTo(0,summary.getBoundingClientRect().top+window.scrollY-380);",
            )
            shot = call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
            (artifacts / name).write_bytes(base64.b64decode(shot["data"]))


def test_execution_script_loads_and_seal_recovery_ui_retries_same_preview(tmp_path):
    from harness.dashboard.server import STATIC_DIR

    script = r"""
      window.el = (tag, text) => {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        return node;
      };
      window.alertText = text => {
        const node = window.el("p", text);
        node.setAttribute("role", "alert");
        return node;
      };
      window.viewIdentity = (task, execution) => JSON.stringify([task, execution.id]);
      window.repaintExecution = () => {};
      window.refreshDashboard = async () => {};
      const execution = {
        task_id: "t-one", id: "e-one", epoch_id: "epoch-1", authority_generation: 5,
      };
      const executionElement = document.getElementById("execution");
      executionElement.dataset.viewIdentity = JSON.stringify(["t-one", "e-one"]);
      executionTaskId = "t-one";
      window.fetch = async () => ({
        ok: true,
        json: async () => ({
          preview_sha256: "sha256:preview",
          preview: {
            readiness: "ready", state_condition: "missing", byte_count: 321,
            target: "specs/approved.md", sealed_sha256: "sha256:sealed",
            expected_epoch_id: "epoch-1", expected_authority_generation: 5,
            provenance_obligations: [],
          },
        }),
      });
      const payloads = [];
      let restoreCount = 0;
      window.postJson = async (_url, payload) => {
        payloads.push(payload);
        restoreCount += 1;
        if (restoreCount === 1) return { res: null, data: {}, netErr: "offline" };
        return {
          res: { ok: true },
          data: {
            restored: true,
            state: "restored",
            authority_revalidation: { status: "unavailable", diagnostic: "provenance pending" },
            implementation_authority_granted: false,
          },
          netErr: null,
        };
      };
      const root = document.getElementById("recovery-ui");
      const wait = () => new Promise(resolve => setTimeout(resolve, 40));
      const render = () => root.replaceChildren(renderSpecRestoration(execution));
      const result = () => document.body.append(document.createTextNode("SEAL_UI_PASS"));
      const fail = error => document.body.append(
        document.createTextNode(`SEAL_UI_FAIL:${error.message}`),
      );
      (async () => {
        try {
          render();
          let panel = root.querySelector(".spec-restoration");
          panel.querySelector("button").click();
          await wait();
          render();
          panel = root.querySelector(".spec-restoration");
          const preview = panel.textContent;
          const confirmation = panel.querySelector("input");
          const restore = [...panel.querySelectorAll("button")].find(button =>
            button.textContent === "Restore approved specification");
          const gated = restore.disabled;
          confirmation.value = "Restore approved specification";
          confirmation.dispatchEvent(new Event("input", { bubbles: true }));
          const enabled = !restore.disabled;
          restore.click();
          await wait();
          render();
          panel = root.querySelector(".spec-restoration");
          const retryVisible = panel.textContent.includes("offline")
            && panel.textContent.includes(payloads[0].request_id);
          const retry = [...panel.querySelectorAll("button")].find(button =>
            button.textContent === "Restore approved specification");
          retry.click();
          await wait();
          render();
          panel = root.querySelector(".spec-restoration");
          const exactRetry = payloads.length === 2
            && payloads[0].request_id === payloads[1].request_id
            && payloads[0].preview_sha256 === "sha256:preview"
            && payloads[1].preview_sha256 === "sha256:preview"
            && payloads[1].epoch_id === "epoch-1"
            && payloads[1].authority_generation === 5
            && payloads[1].confirmation === "Restore approved specification";
          const outcome = panel.textContent.includes("restored at the original path")
            && panel.textContent.includes("Authority revalidation: unavailable")
            && panel.textContent.includes("provenance pending")
            && panel.textContent.includes("Implementation authority granted: no")
            && panel.textContent.includes("No native session was started.");
          if (preview.includes("missing") && preview.includes("321 retained bytes")
              && preview.includes("specs/approved.md") && preview.includes("sha256:sealed")
              && gated && enabled && retryVisible && exactRetry && outcome) result();
          else document.body.append(document.createTextNode("SEAL_UI_FAIL:assertion"));
        } catch (error) { fail(error); }
      })();
    """
    html = tmp_path / "spec-restoration.html"
    html.write_text(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<link rel='stylesheet' href='file://{STATIC_DIR / 'app.css'}'>"
        "</head><body><main id='execution'><div id='recovery-ui'></div></main>"
        f"<script src='file://{STATIC_DIR / 'execution.js'}'></script><script>{script}</script>"
        "</body></html>",
        encoding="utf-8",
    )
    result = _headless(
        tmp_path / "spec-restoration-profile", "--window-size=390,844", "--dump-dom", html.as_uri()
    )
    assert result.returncode == 0, result.stderr
    assert "SEAL_UI_PASS" in _visible_dom(result.stdout), result.stdout


def test_mounted_history_knowledge_survives_real_sse_and_stays_task_scoped(
    db_path, sample_repo, tmp_path
):
    from harness.controller import HarnessController
    from harness.dashboard.knowledge_view import knowledge_action
    from harness.db import connect
    from tests.test_dashboard import serve
    from tests.test_dashboard_live_view_state_browser import chrome, evaluate, wait
    from tests.test_knowledge_dashboard import _task

    marker = "mounted_history_knowledge_fixture"
    (sample_repo / "demo/export.py").write_text(f"def {marker}():\n    return 1\n")
    task_id = _task(db_path, sample_repo)
    other = _task(db_path, sample_repo)
    status, result = knowledge_action(db_path, json.dumps({"task": task_id}).encode(), "refresh")
    assert status == 200 and result["status"] in {"published", "published-partial"}
    with (
        serve(db_path, task_id) as server,
        chrome(
            tmp_path / "mounted-knowledge-profile",
            f"{server.url}?task={task_id}&workspace=run&tab=history&details=records",
            1280,
            800,
        ) as call,
    ):
        wait(call, "Boolean(document.querySelector('.knowledge-query-form'))")
        evaluate(
            call,
            "window.keptKnowledge=document.querySelector('details[data-view-disclosure=knowledge]');"
            "keptKnowledge.open=true;"
            "window.keptKnowledgeInput=keptKnowledge.querySelector('.knowledge-query-form input');"
            f"keptKnowledgeInput.value={json.dumps(marker)};"
            "keptKnowledge.querySelector('form').requestSubmit();",
        )
        wait(call, "Boolean(document.querySelector('.knowledge-assertion'))")
        before = evaluate(
            call,
            "(() => {window.keptResults=keptKnowledge"
            ".querySelector('.knowledge-results').textContent;"
            "window.keptTimer=keptKnowledge.querySelector('.knowledge-panel')._statusTimer;"
            "keptKnowledge.closest('details[data-view-disclosure=workspace-task-records]').open=true;"
            "keptKnowledgeInput.focus();keptKnowledgeInput.setSelectionRange(3,12);"
            "return {results:keptResults,insideHistory:!!keptKnowledge.closest("
            "'details[data-view-disclosure=workspace-task-records]')};})()",
        )
        assert marker in before["results"] and before["insideHistory"]
        conn = connect(db_path)
        try:
            HarnessController(conn).workspace(task_id).record_event("test.knowledge.sse", {})
        finally:
            conn.close()
        wait(call, "workspaceData.task.latest_activity?.kind==='test.knowledge.sse'")
        retained = evaluate(
            call,
            "({same:keptKnowledge===document.querySelector('details[data-view-disclosure=knowledge]'),"
            "sameInput:keptKnowledgeInput===document.querySelector('.knowledge-query-form input'),"
            "focused:document.activeElement===keptKnowledgeInput,start:keptKnowledgeInput.selectionStart,"
            "end:keptKnowledgeInput.selectionEnd,query:keptKnowledgeInput.value,"
            "results:keptKnowledge.querySelector('.knowledge-results').textContent,"
            "open:keptKnowledge.open&&keptKnowledge.closest('details[data-view-disclosure=workspace-task-records]').open,"
            "timer:keptTimer===keptKnowledge.querySelector('.knowledge-panel')._statusTimer,"
            "count:document.querySelectorAll('.knowledge-panel').length})",
        )
        assert retained == {
            "same": True,
            "sameInput": True,
            "focused": True,
            "start": 3,
            "end": 12,
            "query": marker,
            "results": before["results"],
            "open": True,
            "timer": True,
            "count": 1,
        }
        evaluate(
            call,
            "source.close();window.clearedKnowledgeTimers=[];"
            "window.realClearInterval=window.clearInterval;"
            "window.clearInterval=id=>{clearedKnowledgeTimers.push(id);realClearInterval(id);};",
        )
        evaluate(
            call,
            "(async()=>{const response=await fetch('/api/dashboard?task="
            + other
            + "');render(await response.json());})()",
        )
        assert evaluate(
            call,
            "document.querySelector('.knowledge-panel').dataset.task==="
            + json.dumps(other)
            + "&&!keptKnowledge.isConnected&&clearedKnowledgeTimers.includes(keptTimer)"
            "&&document.querySelector('.knowledge-query-form input').value===''",
        )
