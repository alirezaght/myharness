"""Real Chrome coverage for graph interactions, refresh truth and visual artifacts."""

import json
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
        "const noPageOverflow=document.documentElement.scrollWidth<=window.innerWidth;"
        "document.body.append(document.createTextNode(safe&&focused&&trust&&bounded&&noPageOverflow"
        "?'GRAPH_UI_PASS':'GRAPH_UI_FAIL'));"
    )
    html = tmp_path / "knowledge-graph.html"
    html.write_text(_page(STATIC_DIR, script), encoding="utf-8")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    captures = []
    for width, height, name in (
        (1280, 800, "knowledge-graph-desktop.png"),
        (390, 844, "knowledge-graph-narrow.png"),
    ):
        artifact = ARTIFACTS / name
        result = _headless(
            tmp_path / f"graph-profile-{width}",
            f"--window-size={width},{height}",
            "--dump-dom",
            f"--screenshot={artifact}",
            html.as_uri(),
        )
        assert result.returncode == 0, result.stderr
        assert "GRAPH_UI_PASS" in result.stdout
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
        "&&retained.includes('Showing the last complete snapshot. Latest refresh incomplete.')"
        "&&retained.includes('snapshot-last-complete')&&retained.includes('text-only-no-dependency-analysis')"
        "&&recovered.includes('Index ready')&&recovered.includes('snapshot-new-complete')"
        "&&network.includes('offline')&&reloadFailure.includes('Refresh incomplete')"
        "&&reloadFailure.includes('refresh-bytes-capped')&&available&&noPageOverflow;"
        "document.body.append(document.createTextNode(ok?'REFRESH_UI_PASS':'REFRESH_UI_FAIL'));"
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
        assert "REFRESH_UI_PASS" in result.stdout


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
    assert "SEAL_UI_PASS" in result.stdout, result.stdout
