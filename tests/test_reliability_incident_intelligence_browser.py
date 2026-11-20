# ruff: noqa: F811
"""Persisted Incident Intelligence journeys in real desktop and narrow Chrome."""

import base64
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
from harness.execution_store import get_execution
from harness.knowledge.models import RefreshRequest
from harness.knowledge.policy import operator_scope
from harness.knowledge.refresh import refresh
from harness.reliability import store
from tests.test_agent_contracts import output_contract
from tests.test_dashboard import serve
from tests.test_dashboard_browser import CHROME
from tests.test_dashboard_execution_browser import _cdp_call
from tests.test_execution_operations import generated_handoff_fixture, operations  # noqa: F401

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium unavailable"),
]

DRIVER = r"""
function waitFor(name, predicate) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const poll = () => {
      const value = predicate();
      if (value) resolve(value);
      else if (Date.now() - start > 12000) reject(new Error(`timed out: ${name}`));
      else setTimeout(poll, 35);
    };
    poll();
  });
}
function finish(value) {
  const node = document.createElement("pre");
  node.id = "incident-intelligence-browser-result";
  node.hidden = true;
  node.textContent = JSON.stringify(value);
  document.body.append(node);
  window.__iiDone = true;
}
function selectValues(node, values) {
  const wanted = new Set(values);
  for (const option of node.options) option.selected = wanted.has(option.value);
  node.dispatchEvent(new Event("change", { bubbles: true }));
}
function setValue(node, value, eventName = "change") {
  node.value = value;
  node.dispatchEvent(new Event(eventName, { bubbles: true }));
}
async function painted() {
  await new Promise(requestAnimationFrame);
  await new Promise(requestAnimationFrame);
}
document.addEventListener("DOMContentLoaded", async () => {
  const result = {};
  let verdictResponse = null;
  try {
    await waitFor("persisted incidents", () =>
      document.querySelectorAll("#incident-intelligence .ii-item").length >= 3
      && !document.querySelector("#incident-intelligence .ii-results")?.textContent
        .includes("Loading"));
    const section = () => document.querySelector("#incident-intelligence");
    const first = () => section()?.querySelector(".ii-item");
    const incidentId = first()?.dataset.incidentId;
    result.initialCount = section().querySelectorAll(".ii-item").length;
    result.initialScope = section().querySelector("#ii-scope").value;
    const viewportWidth = window.visualViewport?.width || window.innerWidth;
    result.initialOverflow = document.documentElement.scrollWidth > viewportWidth;
    result.initialViewport = {
      innerWidth: window.innerWidth,
      visualWidth: window.visualViewport?.width,
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      offenders: [...document.querySelectorAll("body *")]
        .map((node) => ({
          tag: node.tagName,
          id: node.id || null,
          className: typeof node.className === "string" ? node.className.slice(0, 80) : null,
          left: Math.round(node.getBoundingClientRect().left),
          right: Math.round(node.getBoundingClientRect().right),
          width: Math.round(node.getBoundingClientRect().width),
        }))
        .filter((node) => node.right > viewportWidth + 1)
        .slice(0, 20),
    };
    result.filterControlsFit = ["#ii-scope", "#ii-query", "#ii-category", "#ii-source-kind"]
      .every((selector) =>
        section().querySelector(selector).getBoundingClientRect().right <= viewportWidth + 1);
    first().querySelector("button").click();
    await waitFor("selected immutable incident", () =>
      section().querySelector(".ii-detail .ii-facts")
      && section().querySelector(".ii-detail").textContent.includes(incidentId));
    const detailText = section().querySelector(".ii-detail").textContent;
    result.originalFactVisible = detailText.includes("Immutable observed fact");
    result.provenanceVisible = detailText.includes("Source provenance");
    const cause = section().querySelector("#ii-cause");
    const advanced = section().querySelector("#ii-advanced-controls");
    result.primaryEditorVisible = [
      "#ii-cause", "#ii-confidence", "#ii-supporting", "#ii-status",
      "#ii-rationale", "#ii-evidence", "#ii-save",
    ]
        .every((selector) => {
          const node = section().querySelector(selector);
          const rect = node.getBoundingClientRect();
          return node.getClientRects().length > 0 && rect.width > 0 && rect.height > 0;
        });
    result.primaryOutsideOptionalDisclosure = !advanced.contains(
      section().querySelector(".ii-editor > form"));
    cause.focus();
    result.primaryEditorFocusable = document.activeElement === cause;
    setValue(cause, "failed_verification");
    setValue(section().querySelector("#ii-confidence"), "medium");
    selectValues(section().querySelector("#ii-supporting"), [incidentId]);
    result.supportEvidenceOptionCount = section().querySelector("#ii-supporting").options.length;
    result.supportEvidenceSelectionVisible = [
      ...section().querySelector("#ii-supporting").selectedOptions,
    ]
      .some((option) => option.value === incidentId);
    section().querySelector("#ii-include-hypothesis").checked = true;
    section().querySelector("#ii-include-hypothesis").dispatchEvent(
      new Event("change", { bubbles: true }));
    setValue(section().querySelector("#ii-status"), "investigating");
    const explanation = section().querySelector("#ii-explanation");
    explanation.value = "A persisted verification refusal supports this bounded hypothesis.";
    explanation.dispatchEvent(new Event("input", { bubbles: true }));
    section().querySelector("#ii-save").click();
    await waitFor("appended investigation", () =>
      [...section().querySelectorAll(".ii-status")].some((node) =>
        node.textContent.includes("investigating"))
      && section().querySelector(".ii-detail").textContent.includes(
        "A persisted verification refusal"));
    result.hypothesisAndHistoryPersisted = true;

    // A second actual operator append races the editor's expected revision. The UI must expose
    // its normal 409 conflict, then a status-only edit must not repeat the previous hypothesis.
    const scope = {
      task: executionSnapshot.task_id || executionTaskId,
      execution: executionSnapshot.id,
      epoch: executionSnapshot.epoch_id,
    };
    const concurrent = await fetch("/api/reliability/investigate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ schema_version: 1, scope, incident: incidentId, expected_revision: 1,
        request_id: "browser-concurrent-revision", changes: { status: "investigating",
          rationale: { code: "evidence_updated", evidence: [incidentId] } } }),
    });
    result.concurrentAppendStatus = concurrent.status;
    setValue(section().querySelector("#ii-status"), "resolved");
    section().querySelector("#ii-save").click();
    await waitFor("honest revision conflict", () =>
      section().querySelector(".ii-detail [role=alert]")?.textContent.includes("revision changed"));
    result.conflictVisible = true;
    section().querySelector(`#ii-select-${incidentId}`).click();
    await waitFor("fresh history after conflict", () => section().querySelector(".ii-detail")
      ?.textContent.includes("Investigation revision 2"));
    setValue(section().querySelector("#ii-status"), "resolved");
    section().querySelector("#ii-save").click();
    await waitFor("status-only revision", () =>
      [...section().querySelectorAll(".ii-status")].some((node) =>
        node.textContent.includes("resolved")));
    const currentDoc = section().querySelector(".ii-detail .ii-subsection");
    result.statusOnlyKeepsSingleHypothesis = currentDoc.querySelectorAll(".ii-record").length === 1
      && currentDoc.textContent.includes("failed_verification");
    section().querySelector("#ii-advanced-controls").open = true;
    const sourceVersion = __SOURCE_VERSION__;
    const snapshot = __SNAPSHOT__;
    setValue(section().querySelector("#ii-component"), __COMPONENT_REFERENCE__, "input");
    setValue(section().querySelector("#ii-component-verification"), "verified");
    setValue(section().querySelector("#ii-component-version"), sourceVersion, "input");
    setValue(section().querySelector("#ii-component-snapshot"), snapshot, "input");
    section().querySelector("#ii-save").click();
    await waitFor("historical Knowledge membership verification", () =>
      section().querySelector(".ii-detail").textContent.includes('"verification":"verified"'));
    result.verifiedSourceBasisVisible = section().querySelector(".ii-detail").textContent
      .includes(snapshot);

    section().querySelector("#ii-advanced-controls").open = true;
    section().querySelector("#ii-suggest").click();
    await waitFor("bounded related symptom suggestion", () =>
      section().querySelectorAll(".ii-group").length > 0);
    const groupArticle = section().querySelector(".ii-group");
    const groupButton = groupArticle.querySelector("button");
    groupButton.click();
    await waitFor("group decision form", () => section().querySelector("#ii-group-save"));
    result.groupEvidenceOptionCount =
      section().querySelector("#ii-group-evidence")?.options.length || 0;
    result.groupEvidenceOptionTexts = [
      ...(section().querySelector("#ii-group-evidence")?.options || []),
    ]
      .map((option) => option.textContent);
    result.groupEvidenceStyle = (() => {
      const node = section().querySelector("#ii-group-evidence");
      return node ? {
        color: getComputedStyle(node).color,
        background: getComputedStyle(node).backgroundColor,
      } : null;
    })();
    setValue(section().querySelector("#ii-group-action"), "reject");
    section().querySelector("#ii-group-save").click();
    await waitFor("append-only group decision", () =>
      section().querySelector(".ii-groups").textContent.includes('"kind":"reject"'));
    result.groupDecisionPersisted = true;

    await waitFor("published output descriptor", () =>
      section().querySelector(".ii-output-item input"));
    result.verdictEvidenceOptionCount =
      section().querySelector("#ii-verdict-evidence")?.options.length || 0;
    result.verdictEvidenceOptionTexts = [
      ...(section().querySelector("#ii-verdict-evidence")?.options || []),
    ]
      .map((option) => option.textContent);
    result.verdictEvidenceStyle = (() => {
      const node = section().querySelector("#ii-verdict-evidence");
      return node ? {
        color: getComputedStyle(node).color,
        background: getComputedStyle(node).backgroundColor,
      } : null;
    })();
    const verdictRationale = section().querySelector("#ii-verdict-rationale");
    result.verdictRationaleSelected = verdictRationale.selectedIndex >= 0
      && verdictRationale.value === "evidence_updated";
    const fetchBeforeVerdict = window.fetch.bind(window);
    window.fetch = async (input, options) => {
      const response = await fetchBeforeVerdict(input, options);
      const url = new URL(String(input), location.href);
      if (url.pathname === "/api/reliability/verdict") {
        verdictResponse = {
          status: response.status,
          request: JSON.parse(options.body),
          body: await response.clone().json().catch(() => null),
        };
      }
      return response;
    };
    const outputRadio = section().querySelector(".ii-output-item input");
    outputRadio.checked = true;
    outputRadio.dispatchEvent(new Event("change", { bubbles: true }));
    section().querySelector("#ii-verdict-explanation").value =
      "The actual published result needs a follow-up.";
    section().querySelector("#ii-verdict-explanation").dispatchEvent(
      new Event("input", { bubbles: true }));
    section().querySelector("#ii-verdict").click();
    await waitFor("historical output verdict", () =>
      section().querySelector(".ii-detail [role=status]")?.textContent.includes(
        "Historical operator verdict recorded"));
    result.verdictResponse = verdictResponse;
    result.verdictReasonCode = verdictResponse?.request?.reason?.code;
    result.realOutputVerdictPersisted = true;
    result.verdictDoesNotGrantAuthority = section().querySelector(".ii-outputs-card")
      .textContent.includes("does not approve, verify, deploy, or change execution status");
    result.outputPickerReadable = section().querySelector(".ii-outputs-card")
      .textContent.includes("published agent report or result")
      && section().querySelector(".ii-output-item").textContent.includes("result.md")
      && section().querySelector(".ii-output-item").textContent.includes(
        "Output provenance and integrity");

    await waitFor("reloaded incident list", () =>
      section().querySelectorAll(".ii-item").length >= 4);
    const originalButton = section().querySelector(`#ii-select-${incidentId}`);
    originalButton.click();
    await waitFor("reselected incident after output verdict", () =>
      section().querySelector(".ii-editor"));
    const currentCount = section().querySelectorAll(".ii-item").length;

    const originalFetch = window.fetch.bind(window);
    let holdNextList = false;
    let heldList = null;
    let taskScopeList = 0;
    let taskScopeParameters = null;
    window.fetch = async (input, options) => {
      const url = new URL(String(input), location.href);
      if (url.pathname === "/api/reliability/incidents"
          && url.searchParams.get("task") === scope.task
          && !url.searchParams.has("execution")) {
        taskScopeList += 1;
        taskScopeParameters = [...url.searchParams.keys()];
      }
      const response = await originalFetch(input, options);
      if (url.pathname === "/api/reliability/incidents" && holdNextList) {
        holdNextList = false;
        return new Promise((resolve) => { heldList = () => resolve(response); });
      }
      return response;
    };
    const search = section().querySelector("#ii-query");
    search.value = "not-a-real-source";
    holdNextList = true;
    section().querySelector("#ii-filters")?.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }));
    await waitFor("held old-scope response", () => heldList);
    setValue(section().querySelector("#ii-scope"), "task");
    await waitFor("task-wide response", () => taskScopeList > 0);
    const taskScopeSection = section();
    const taskScopeHasNoEpoch = !taskScopeParameters.includes("execution")
      && !taskScopeParameters.includes("epoch")
      && taskScopeSection.querySelector("#ii-scope").value === "task";
    heldList();
    await painted();
    result.staleScopeResponseFenced = taskScopeSection === section()
      && section().querySelector("#ii-scope").value === "task";
    result.taskScopeWasRequestedWithoutEpoch = taskScopeHasNoEpoch;
    setValue(section().querySelector("#ii-scope"), "execution");
    await waitFor("execution scope restored", () =>
      section().querySelector("#ii-scope")?.value === "execution");
    setValue(section().querySelector("#ii-query"), "", "input");
    section().querySelector("#ii-filters")?.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }));
    await waitFor("execution list restored", () =>
      section().querySelectorAll(".ii-item").length >= currentCount);
    section().querySelector(`#ii-select-${incidentId}`).click();
    await waitFor("final detail for screenshots", () => section().querySelector(".ii-editor"));
    result.supportSelectionRestored = [...section().querySelector("#ii-supporting").selectedOptions]
      .some((option) => option.value === incidentId);
    section().querySelector("#ii-advanced-controls").open = false;
    await painted();
    section().querySelector("#ii-advanced-controls").open = true;
    await painted();
    result.finalOverflow = document.documentElement.scrollWidth > viewportWidth;
    result.viewportWidth = window.visualViewport?.width || window.innerWidth;
    result.layoutViewportWidth = window.innerWidth;
    result.finalViewport = {
      innerWidth: window.innerWidth,
      visualWidth: window.visualViewport?.width,
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      offenders: [...document.querySelectorAll("body *")]
        .map((node) => ({
          tag: node.tagName,
          id: node.id || null,
          className: typeof node.className === "string" ? node.className.slice(0, 80) : null,
          left: Math.round(node.getBoundingClientRect().left),
          right: Math.round(node.getBoundingClientRect().right),
          width: Math.round(node.getBoundingClientRect().width),
        }))
        .filter((node) => node.right > viewportWidth + 1)
        .slice(0, 20),
    };
    result.controlsInViewport = ["#ii-query", "#ii-source-kind", "#ii-save", "#ii-verdict"]
      .map((selector) => {
        const node = section().querySelector(selector);
        const rect = node.getBoundingClientRect();
        return { selector, left: rect.left, right: rect.right, width: rect.width };
      });
    finish(result);
  } catch (error) {
    result.error = error.message;
    result.stack = error.stack;
    result.verdictResponse = verdictResponse;
    result.failureState = {
      outputPanel: document.querySelector(
        "#incident-intelligence .ii-outputs-card")?.innerText || "",
      detailAlerts: [...document.querySelectorAll(
        "#incident-intelligence .ii-detail [role=alert]")].map((node) => node.textContent),
      detailStatuses: [...document.querySelectorAll(
        "#incident-intelligence .ii-detail [role=status]")].map((node) => node.textContent),
      selectedOutput: document.querySelector(
        "#incident-intelligence .ii-output-item input:checked")?.value || null,
    };
    finish(result);
  }
});
"""


def _install_driver(monkeypatch, tmp_path, source_version, snapshot, component_reference):
    copied = tmp_path / "incident-intelligence-static"
    with as_file(server_module.STATIC_DIR) as source:
        shutil.copytree(source, copied)
    driver = (
        DRIVER.replace("__SOURCE_VERSION__", json.dumps(source_version))
        .replace("__SNAPSHOT__", json.dumps(snapshot))
        .replace("__COMPONENT_REFERENCE__", json.dumps(component_reference))
    )
    (copied / "incident-intelligence-driver.js").write_text(driver, encoding="utf-8")
    page = copied / "index.html"
    html = page.read_text(encoding="utf-8")
    anchor = '<script src="/static/app.js" defer></script>'
    assert html.count(anchor) == 1
    page.write_text(
        html.replace(
            anchor,
            anchor + '\n    <script src="/static/incident-intelligence-driver.js" defer></script>',
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "STATIC_DIR", copied)
    monkeypatch.setattr(
        server_module,
        "STATIC",
        server_module.STATIC
        | {
            "/static/incident-intelligence-driver.js": (
                "incident-intelligence-driver.js",
                "text/javascript; charset=utf-8",
            )
        },
    )


def _capture(server, profile: Path, output_dir: Path, width: int, height: int):
    profile.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            "--incognito",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
            "--disable-extensions",
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
                raise RuntimeError("Chrome exited before opening its DevTools port")
            time.sleep(0.05)
        if not port_file.exists():
            raise RuntimeError("Chrome DevTools port did not become ready")
        port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
        with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as response:
            target = next(row for row in json.load(response) if row.get("type") == "page")
        parsed = urlparse(target["webSocketDebuggerUrl"])
        sock = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
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
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": 1,
                "mobile": width < 600,
            },
        )
        call("Page.navigate", {"url": server.url})
        deadline = time.monotonic() + 90
        result = None
        while time.monotonic() < deadline:
            value = (
                call(
                    "Runtime.evaluate",
                    {
                        "expression": (
                            "window.__iiDone ? JSON.parse(document.getElementById("
                            "'incident-intelligence-browser-result').textContent) : null"
                        ),
                        "returnByValue": True,
                    },
                )
                .get("result", {})
                .get("value")
            )
            if value is not None:
                result = value
                break
            time.sleep(0.1)
        if result is None:
            raise RuntimeError("Incident Intelligence Chrome flow did not settle")
        if result.get("error"):
            call(
                "Runtime.evaluate",
                {
                    "expression": (
                        "document.querySelector('#incident-intelligence .ii-outputs-card')"
                        "?.scrollIntoView({block:'center'})"
                    ),
                },
            )
            call(
                "Runtime.evaluate",
                {"expression": "new Promise(requestAnimationFrame)", "awaitPromise": True},
            )
            shot = call(
                "Page.captureScreenshot",
                {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
            )
            failure_path = output_dir / f"incident-intelligence-failure-{width}x{height}.png"
            failure_path.write_bytes(base64.b64decode(shot["data"]))
            result["failureScreenshot"] = str(failure_path)
            (output_dir / "failure-result.json").write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8"
            )
            raise RuntimeError(
                f"Chrome interaction failed: {result['error']}\n{result.get('stack', '')}\n"
                f"Diagnostics: {json.dumps(result.get('failureState'), sort_keys=True)}; "
                f"verdict={json.dumps(result.get('verdictResponse'), sort_keys=True)}; "
                f"screenshot={failure_path}"
            )
        for name, selector in [
            ("history", "#incident-intelligence"),
            ("editor", ".ii-editor"),
            ("advanced-controls", "#ii-advanced-controls"),
            ("groups", ".ii-groups-card"),
            ("outputs-verdict", ".ii-outputs-card"),
        ]:
            call(
                "Runtime.evaluate",
                {
                    "expression": (
                        "document.querySelector('#ii-advanced-controls').open = "
                        f"{str(name == 'advanced-controls').lower()}"
                    ),
                },
            )
            call(
                "Runtime.evaluate",
                {
                    "expression": (
                        f"document.querySelector({json.dumps(selector)})"
                        "?.scrollIntoView({block:'start'})"
                    ),
                },
            )
            call(
                "Runtime.evaluate",
                {"expression": "new Promise(requestAnimationFrame)", "awaitPromise": True},
            )
            evidence_state = (
                call(
                    "Runtime.evaluate",
                    {
                        "expression": """
                            (() => {
                              const root = document.querySelector('#incident-intelligence');
                              const ids = [
                                'ii-supporting', 'ii-contradicting', 'ii-evidence',
                                'ii-control-evidence', 'ii-correction-evidence',
                                'ii-group-evidence', 'ii-verdict-evidence'
                              ];
                              return Object.fromEntries(ids.map((id) => {
                                const node = root.querySelector(`#${id}`);
                                const style = node && getComputedStyle(node);
                                return [id, node ? {
                                  visible: node.getClientRects().length > 0
                                    && style.display !== 'none'
                                    && style.visibility !== 'hidden',
                                  display: style.display,
                                  visibility: style.visibility,
                                  color: style.color,
                                  background: style.backgroundColor,
                                  scrollTop: node.scrollTop,
                                  optionCount: node.options.length,
                                  optionTexts: [...node.options].map(
                                    (option) => option.textContent
                                  ),
                                  selected: [...node.selectedOptions].map((option) => option.value)
                                } : null];
                              }));
                            })()
                        """,
                        "returnByValue": True,
                    },
                )
                .get("result", {})
                .get("value", {})
            )
            result.setdefault("screenshotEvidenceState", {})[name] = evidence_state
            if name == "editor":
                result["editorScreenshotState"] = (
                    call(
                        "Runtime.evaluate",
                        {
                            "expression": """
                                (() => {
                                  const root = document.querySelector('#incident-intelligence');
                                  const details = root.querySelector('#ii-advanced-controls');
                                  const ids = [
                                    'ii-cause', 'ii-confidence', 'ii-supporting', 'ii-status',
                                    'ii-rationale', 'ii-evidence', 'ii-save'
                                  ];
                                  const component = root.querySelector('#ii-component');
                                  const prior = document.activeElement;
                                  component.focus({preventScroll: true});
                                  const optionalFocusable = document.activeElement === component;
                                  if (prior?.isConnected) prior.focus({preventScroll: true});
                                  const checkbox = root.querySelector('#ii-include-hypothesis');
                                  const checkboxLabel = checkbox.closest('label');
                                  const primary = Object.fromEntries(ids.map((id) => {
                                    const node = root.querySelector(`#${id}`);
                                    const rect = node?.getBoundingClientRect();
                                    return [id, Boolean(
                                      node && node.getClientRects().length
                                      && rect.width && rect.height
                                    )];
                                  }));
                                  return {
                                    advancedOpen: details.open,
                                    primary,
                                    optionalCheckVisibility: component.checkVisibility(),
                                    optionalFocusable,
                                    optionalDisplay: getComputedStyle(component).display,
                                    checkboxLabelDisplay: getComputedStyle(checkboxLabel).display,
                                    checkboxWidth: checkbox.getBoundingClientRect().width
                                  };
                                })()
                            """,
                            "returnByValue": True,
                        },
                    )
                    .get("result", {})
                    .get("value", {})
                )
            shot = call(
                "Page.captureScreenshot",
                {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
            )
            path = output_dir / f"incident-intelligence-{name}-{width}x{height}.png"
            path.write_bytes(base64.b64decode(shot["data"]))
            result.setdefault("screenshots", []).append(str(path))
        (output_dir / "functional-result.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
        sock.close()
        return result
    finally:
        process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=4)


@pytest.mark.parametrize("name,width,height", [("desktop", 1280, 800), ("narrow", 390, 844)])
def test_persisted_incident_intelligence_in_chrome(
    operations, monkeypatch, tmp_path, name, width, height
):
    from harness.dashboard import incident_actions

    operation, principal, controller, initialization, _, execution, _ = operations
    for _ in range(3):
        observation = store.begin(operation, principal, "read_file")
        store.finish(operation, observation, "AUTHORITY_REJECTED")

    child, _ = generated_handoff_fixture(operations)
    assignment = operation.dispatch(child, "await_assignment", {"timeout": 1})["handoff"]
    state = json.loads(Path(initialization["source"]).read_bytes())
    actual_execution = get_execution(controller.conn, execution["id"])
    content = "Persisted actual output selected for historical operator review."
    contract = output_contract(operation, child, actual_execution, state, assignment, content)
    operation.dispatch(
        child,
        "submit_output",
        {
            "name": "result.md",
            "content": content,
            "expected_graph_revision": 1,
            "contract": contract,
        },
    )

    repository = Path(operation.envelope["repository"])
    scope = operator_scope(repository, task_id=actual_execution["task_id"])
    approved_spec = f"docs/specs/{actual_execution['task_id']}/v1.md"
    snapshot = refresh(
        controller.conn, scope, RefreshRequest(included_paths=(approved_spec,))
    ).snapshot_id
    source = controller.conn.execute(
        "SELECT s.locator,m.version_id FROM knowledge_snapshot_sources m "
        "JOIN knowledge_sources s USING(source_id) WHERE m.snapshot_id=? "
        "AND s.locator=? AND m.membership_state='present'",
        (snapshot, approved_spec),
    ).fetchone()
    assert source is not None, (
        "fixture needs one real present Knowledge source to verify historical membership"
    )
    _install_driver(monkeypatch, tmp_path, source["version_id"], snapshot, source["locator"])
    monkeypatch.setattr(DashboardServer, "stream_lifetime", 0.5)

    verdict_errors = []
    original_verdict = incident_actions.operator_verdicts.reject

    def trace_verdict(*args, **kwargs):
        try:
            return original_verdict(*args, **kwargs)
        except Exception as error:
            verdict_errors.append(f"{type(error).__name__}: {error}")
            raise

    monkeypatch.setattr(incident_actions.operator_verdicts, "reject", trace_verdict)

    screenshot_dir = tmp_path / f"screenshots-{name}"
    with serve(operation.database, actual_execution["task_id"]) as server:
        try:
            result = _capture(
                server, tmp_path / f"chrome-profile-{name}", screenshot_dir, width, height
            )
        except RuntimeError as error:
            raise AssertionError(f"{error}; operator verdict cause={verdict_errors}") from error

    assert result["initialScope"] == "execution", result
    assert result["initialCount"] >= 3 and result["originalFactVisible"], result
    assert result["provenanceVisible"] and result["hypothesisAndHistoryPersisted"], result
    assert result["concurrentAppendStatus"] == 200 and result["conflictVisible"], result
    assert result["statusOnlyKeepsSingleHypothesis"], result
    assert result["verifiedSourceBasisVisible"] and result["groupDecisionPersisted"], result
    assert result["realOutputVerdictPersisted"] and result["verdictDoesNotGrantAuthority"], result
    assert (
        result["verdictRationaleSelected"] and result["verdictReasonCode"] == "evidence_updated"
    ), result
    assert result["staleScopeResponseFenced"] and result["taskScopeWasRequestedWithoutEpoch"], (
        result
    )
    assert (
        result["supportEvidenceOptionCount"] >= 3 and result["supportEvidenceSelectionVisible"]
    ), result
    assert result["supportSelectionRestored"] and result["outputPickerReadable"], result
    assert (
        result["primaryEditorVisible"]
        and result["primaryEditorFocusable"]
        and result["primaryOutsideOptionalDisclosure"]
    ), result
    assert result["groupEvidenceOptionCount"] >= 3 and result["verdictEvidenceOptionCount"] >= 3, (
        result
    )
    editor_state = result["editorScreenshotState"]
    assert (
        not editor_state["advancedOpen"]
        and not editor_state["optionalFocusable"]
        and all(editor_state["primary"].values())
    ), editor_state
    assert editor_state["checkboxLabelDisplay"] == "flex" and editor_state["checkboxWidth"] < 30, (
        editor_state
    )
    for name, picker_id in (
        ("groups", "ii-group-evidence"),
        ("outputs-verdict", "ii-verdict-evidence"),
    ):
        state = result["screenshotEvidenceState"][name][picker_id]
        assert (
            state["visible"]
            and state["optionCount"] >= 3
            and state["optionTexts"][0].startswith("#")
        ), state
    assert (
        result["filterControlsFit"]
        and not result["initialOverflow"]
        and not result["finalOverflow"]
    ), result
    assert result["viewportWidth"] == width, result
    assert len(result["screenshots"]) == 5, result
    assert all(
        Path(path).is_file() and Path(path).stat().st_size > 1024 for path in result["screenshots"]
    )
    (screenshot_dir / "functional-result.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
