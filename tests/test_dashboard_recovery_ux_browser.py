"""Real Chrome coverage for truthful recovery, decision, and responsive dashboard UI."""

import json
import os
import shutil
import subprocess
from importlib.resources import as_file
from pathlib import Path

import pytest

from harness.dashboard import server as server_module
from harness.dashboard.server import DashboardServer
from harness.design import ArchitectureDesigner
from harness.design_models import DesignSection, EntryStatus
from harness.diagrams import DiagramService
from harness.execution_decisions import record_execution_decision
from harness.execution_projection import ingest_event
from tests.execution_helpers import approved_execution, event, started
from tests.test_dashboard import api, serve
from tests.test_dashboard_browser import CHROME
from tests.test_dashboard_execution_browser import _cdp_capture
from tests.test_dashboard_live_view_state_browser import chrome, evaluate, wait

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium not installed"),
]

ACTIVE_FIXTURE = r"""
function activeRecoveryFixture(execution) {
  const fixture = structuredClone(execution);
  fixture.status = "Running";
  fixture.error = null;
  fixture.status_explanation = null;
  fixture.readiness = { actions: { resume: true, reconnect: true }, message: "Cached readiness." };
  const blocker = fixture.blockers[0];
  const decision = fixture.decisions[0];
  fixture.blockers.push({ ...blocker, id: "retired-case", state: "Resolved",
    decision_state: "Resolved", delivery_id: "verified-request" });
  fixture.decisions.push({ ...decision, id: "retired-request", blocker_id: "retired-case",
    state: "Rejected", delivery_status: "not_delivered", retryable: false,
    error: "invalid decision criteria",
    outcome_notice: "Invalid request retired before delivery; no AL acknowledgment." },
    { ...decision, id: "verified-request", blocker_id: "retired-case", state: "Resolved",
      delivery_status: "acknowledged", retryable: false, error: null,
      outcome_notice: "Recorded AL decision outcome." });
  return fixture;
}
"""

DRIVER = (
    ACTIVE_FIXTURE
    + r"""
const RESULT = {};
const SCRIPT_ERRORS = [];
window.addEventListener("error", (event) => SCRIPT_ERRORS.push(event.message));
function finish(value) {
  Object.assign(RESULT, value);
  const out = document.createElement("pre");
  out.id = "recovery-ux-result";
  out.textContent = JSON.stringify(RESULT);
  document.body.append(out);
}
function waitFor(predicate) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const poll = () => predicate() ? resolve() : Date.now() - start > 8000
      ? reject(new Error(`execution panel did not render: ${JSON.stringify({
        text: document.body.innerText,
        hasExecution: Boolean(document.querySelector("#execution")),
        blockerCount: document.querySelectorAll("#execution .execution-blocker").length,
        decisionsText: document.querySelector("#execution .execution-decisions")?.innerText,
        errors: SCRIPT_ERRORS,
      })}`))
      : setTimeout(poll, 40);
    poll();
  });
}
document.addEventListener("DOMContentLoaded", async () => {
  try {
    await waitFor(() => document.querySelector("#execution"));
    if (!document.querySelector("#execution .execution-blocker")) {
      throw new Error(`blocker markup missing: ${document.querySelector("#execution")?.innerText}`);
    }
    const approvalPreview = document.getElementById("approve-preview");
    if (approvalPreview) approvalPreview.open = true;
    const originalFetch = window.fetch;
    let refreshBody = null;
    let retryBody = null;
    let resumeBody = null;
    let resumeRequests = 0;
    window.fetch = (input, options) => {
      const url = String(input);
      if (url === "/api/execution/reconnect") {
        refreshBody = JSON.parse(options.body);
        return Promise.resolve(new Response(JSON.stringify({ error: "operator permission denied" }),
          { status: 400, headers: { "Content-Type": "application/json" } }));
      }
      if (url === "/api/execution/decision-retry") {
        retryBody = JSON.parse(options.body);
        return Promise.resolve(new Response(JSON.stringify({ state: "Delivered" }),
          { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      if (url === "/api/execution/recover") {
        resumeRequests += 1;
        resumeBody = JSON.parse(options.body);
        return Promise.resolve(new Response(JSON.stringify({ status: "Accepted" }),
          { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      return originalFetch(input, options);
    };
    const buttons = [...document.querySelectorAll(".execution-actions button")]
      .map((button) => button.textContent);
    const actionHints = [...document.querySelectorAll(".execution-action small")]
      .map((node) => node.textContent);
    const proseData = await fetch(`/api/dashboard?task=${encodeURIComponent(DRIVE.proseTask)}`)
      .then((response) => response.json());
    const diagramData = await fetch(`/api/dashboard?task=${encodeURIComponent(DRIVE.diagramTask)}`)
      .then((response) => response.json());
    const diagramFixtures = document.createElement("div");
    diagramFixtures.id = "diagram-ui-fixtures";
    diagramFixtures.append(
      renderDiagrams(DRIVE.proseTask, proseData.task.diagrams),
      renderDiagrams(DRIVE.diagramTask, diagramData.task.diagrams),
    );
    document.querySelector("main").append(diagramFixtures);
    const [prosePanel, effectivePanel] = diagramFixtures.querySelectorAll("#diagrams");
    const diagramUi = {
      proseState: Boolean(prosePanel.querySelector('.change-illustration svg')),
      proseGuidance: prosePanel.textContent.includes("What this task changes"),
      independentProvenance: !prosePanel.textContent.includes("no structured notation"),
      effectiveState: effectivePanel.textContent.includes("Design state: effective"),
      currentDiagramCount: effectivePanel.querySelectorAll(".diagram-canvas svg").length,
      pendingProposal: effectivePanel.textContent.includes("Pending diagram proposals (1)"),
      proposalControls: effectivePanel.querySelector(".diagram-proposals")
        ?.querySelectorAll("button, [data-element-id]").length,
      unchangedPhase: diagramData.task.phase,
    };
    const decision = document.querySelector(".saved-decision-details summary")?.textContent;
    const outcome = document.querySelector(".saved-decision-details")?.textContent;
    const duplicateFormBefore = document.querySelectorAll(".execution-decision-form").length;
    const retry = [...document.querySelectorAll("#execution button")]
      .find((button) => button.textContent === "Retry saved decision delivery");
    retry.click();
    await waitFor(() => document.querySelector(".execution-blocker [role=status]")
      ?.textContent.includes("original text"));
    const retryResult = document.querySelector(".execution-blocker [role=status]")?.textContent;
    const noFormForPending = document.querySelectorAll(".execution-decision-form").length;
    const refresh = [...document.querySelectorAll(".execution-actions button")]
      .find((button) => button.textContent === "Refresh recorded status");
    const keyboardReady = new Promise((resolve) => {
      const observe = (event) => {
        if (!event.isTrusted || event.key !== "Tab") return;
        document.removeEventListener("keydown", observe);
        resolve();
      };
      document.addEventListener("keydown", observe);
    });
    window.recoveryKeyboardReady = true;
    await keyboardReady;
    refresh.focus();
    const focusStyle = getComputedStyle(refresh).outlineStyle;
    refresh.click();
    const actionAlert = () => document.querySelector(".execution-action-results [role=alert]");
    await waitFor(() => actionAlert()?.textContent.includes("operator permission denied"));
    const refreshError = actionAlert()?.textContent;
    await refreshDashboard();
    const refreshErrorAfterRender = actionAlert()?.textContent;
    const resume = [...document.querySelectorAll(".execution-actions button")]
      .find((button) => button.textContent === "Continue task");
    resume.click();
    await waitFor(() => document.querySelector(".execution-action-results [role=status]")
      ?.textContent.includes("request was accepted"));
    const resumeMessage = document.querySelector(
      ".execution-action-results [role=status]")?.textContent;
    const incomplete = document.querySelector(".execution-summary")?.textContent;
    document.querySelector('[data-view-disclosure="host-reconciliation"]').open = true;
    const hostEvidence = document.getElementById("execution-reconcile-evidence");
    hostEvidence.value = "Inspected recorded host exit; preserve this draft";
    hostEvidence.dispatchEvent(new Event("input"));
    hostEvidence.focus({preventScroll:true});
    hostEvidence.setSelectionRange(2, 7);
    document.getElementById("execution-reconcile-outcome").value = "not-started";
    document.getElementById("execution-reconcile-outcome").dispatchEvent(new Event("change"));
    await refreshDashboard();
    const reconciliationDraft = {
      text: document.getElementById("execution-reconcile-evidence").value,
      outcome: document.getElementById("execution-reconcile-outcome").value,
      focus: document.activeElement.id,
      selection: [document.activeElement.selectionStart, document.activeElement.selectionEnd],
    };
    const resumeAfterRender = document.querySelector(
      ".execution-action-results [role=status]")?.textContent;
    const boxes = [...document.querySelectorAll("#current-status article")].map((node) => {
      const rect = node.getBoundingClientRect();
      return { left: rect.left, right: rect.right, width: rect.width };
    });
    const result = { buttons, actionHints, decision, outcome, duplicateFormBefore,
      retryResult, noFormForPending, refreshError, refreshErrorAfterRender,
      refreshBody, retryBody, resumeBody, resumeMessage, resumeAfterRender, incomplete,
      diagramUi, reconciliationDraft, focusStyle, boxes, viewportWidth: innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      headings: [...document.querySelectorAll("#current-status h3")]
        .map((node) => node.textContent),
      currentStatus: document.querySelector("#current-status article:first-child")?.textContent,
      whyBlocked: document.querySelector("#current-status article:nth-child(2)")?.textContent,
      nextStep: document.querySelector("#current-status article:last-child")?.textContent,
      detailsCollapsed: !document.querySelector(".task-metadata").open,
      statusBeforeMetadata: !!(document.querySelector("#current-status")
        .compareDocumentPosition(document.querySelector(".task-metadata"))
        & Node.DOCUMENT_POSITION_FOLLOWING),
    };
    const active = activeRecoveryFixture(executionSnapshot);
    document.getElementById("execution").replaceWith(renderExecution(DRIVE.task, active));
    const activeResume = [...document.querySelectorAll(".execution-actions button")]
      .find((button) => button.textContent === "Continue task");
    const beforeResume = resumeRequests;
    activeResume.click();
    await sendExecutionAction("recover", active);
    const retired = [...document.querySelectorAll(".saved-decision-details")]
      .find((node) => node.textContent.includes("retired-request"));
    const verified = [...document.querySelectorAll(".saved-decision-details")]
      .find((node) => node.textContent.includes("verified-request"));
    const alerts = [...document.querySelectorAll("#execution [role=alert]")];
    result.activeUi = {
      resumeDisabled: activeResume.disabled,
      resumeRequestUnchanged: resumeRequests === beforeResume,
      activeGuidance: activeResume.closest(".execution-action").textContent,
      historicalError: retired.textContent.includes(
        "Historical delivery issue: invalid decision criteria"),
      retiredAlertCount: retired.querySelectorAll("[role=alert]").length,
      noCurrentRetiredAlert: !alerts.some((node) =>
        node.textContent.includes("invalid decision criteria")),
      noRetiredRetry: retired.closest("article").querySelectorAll("button").length === 0,
      verifiedAcknowledged: verified.textContent.includes("Acknowledged by AL"),
      activeFailureAlert: alerts.some((node) =>
        node.textContent.includes("Decision delivery issue:")),
      activeFailureRetry: [...document.querySelectorAll("#execution button")]
        .some((node) => node.textContent === "Retry saved decision delivery"),
      overflow: document.documentElement.scrollWidth > innerWidth,
    };
    window.fetch = originalFetch;
    finish(result);
  } catch (error) { finish({ error: error.message }); }
});
"""
)


def _install_driver(
    tmp_path: Path,
    monkeypatch,
    task: str,
    execution: str,
    decision: str,
    prose_task: str,
    diagram_task: str,
) -> None:
    copied = tmp_path / "static"
    with as_file(server_module.STATIC_DIR) as real:
        shutil.copytree(real, copied)
    config = json.dumps(
        {
            "task": task,
            "execution": execution,
            "decision": decision,
            "proseTask": prose_task,
            "diagramTask": diagram_task,
        }
    )
    (copied / "driver.js").write_text(
        f'"use strict";\nconst DRIVE = {config};\n{DRIVER}', encoding="utf-8"
    )
    page = copied / "index.html"
    source = page.read_text(encoding="utf-8")
    tag = '<script src="/static/app.js" defer></script>'
    assert source.count(tag) == 1
    page.write_text(
        source.replace(tag, f'{tag}\n    <script src="/static/driver.js" defer></script>'),
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "STATIC_DIR", copied)
    monkeypatch.setattr(
        server_module,
        "STATIC",
        server_module.STATIC
        | {"/static/driver.js": ("driver.js", "text/javascript; charset=utf-8")},
    )


def _install_capture_driver(tmp_path: Path, monkeypatch, *, active=False) -> None:
    copied = tmp_path / ("active-capture-static" if active else "capture-static")
    with as_file(server_module.STATIC_DIR) as real:
        shutil.copytree(real, copied)
    driver = r"""
document.addEventListener("DOMContentLoaded", () => {
  const reveal = () => {
    const preview = document.getElementById("approve-preview");
    if (!preview) return setTimeout(reveal, 25);
    preview.open = true;
    preview.scrollIntoView({ block: "center" });
  };
  reveal();
});
"""
    if active:
        driver = (
            ACTIVE_FIXTURE
            + r"""
document.addEventListener("DOMContentLoaded", () => {
  const reveal = () => {
    if (!executionSnapshot || !source || source.readyState !== EventSource.OPEN)
      return setTimeout(reveal, 30);
    const fixture = activeRecoveryFixture(executionSnapshot);
    document.getElementById("execution").replaceWith(renderExecution(executionTaskId, fixture));
    const history = [...document.querySelectorAll(".saved-decision-details")]
      .find((node) => node.textContent.includes("retired-request"));
    history.open = true;
    history.scrollIntoView({ block: "center" });
  };
  reveal();
});
"""
        )
    (copied / "driver.js").write_text(driver, encoding="utf-8")
    monkeypatch.setattr(server_module, "STATIC_DIR", copied)


def test_recovery_and_decision_controls_are_truthful_and_responsive(
    controller, sample_repo, monkeypatch, db_path, tmp_path
):
    import harness.execution_decisions as decision_service

    task, execution = approved_execution(controller, sample_repo, monkeypatch)
    started(controller.conn, execution)
    controller.conn.execute(
        "UPDATE executions SET native_id=?, status='Interrupted', error=? WHERE id=?",
        (
            "native-fixture-session",
            "native host exited 0; no guarded AL implementation result",
            execution,
        ),
    )
    ingest_event(
        controller.conn,
        execution,
        event(
            controller.conn,
            execution,
            2,
            "task.registered",
            {
                "id": "A",
                "title": "Recovery UI fixture",
                "status": "Idle",
                "dependencies": [],
                "files": ["fixture"],
                "criteria": ["AC-01"],
                "revision": 1,
            },
        ),
    )
    blocker = {
        "id": "design1",
        "revision": 1,
        "tasks": ["A"],
        "kind": "developer_engineering_decision",
        "question": "Which storage design?",
        "context": "The approved plan leaves this choice open.",
        "alternatives": ["Keep the current format", "Add an indexed format"],
        "recommendation": "Keep the current format.",
    }
    ingest_event(
        controller.conn,
        execution,
        event(controller.conn, execution, 3, "blocker.raised", blocker),
    )
    original_durable_file = decision_service.durable_file

    def denied(*args, **kwargs):
        raise PermissionError("permission denied for synthetic Registry")

    monkeypatch.setattr(decision_service, "durable_file", denied)
    with pytest.raises(PermissionError):
        record_execution_decision(
            controller,
            execution,
            "design1",
            blocker_revision=1,
            request_id="saved-request",
            verdict="accept",
            decision_text="Keep the current format and preserve existing behavior.",
            tasks=["A"],
            criteria=["AC-01"],
        )
    monkeypatch.setattr(decision_service, "durable_file", original_durable_file)
    prose_task = controller.create_task("Prose-only diagram fixture").id
    controller.advance(prose_task)
    controller.advance(prose_task)
    ArchitectureDesigner(controller, prose_task).add(
        DesignSection.COMPONENTS, "The dashboard reads from a local service."
    )
    diagram_task = controller.create_task("Diagram proposal fixture").id
    controller.advance(diagram_task)
    controller.advance(diagram_task)
    designer = ArchitectureDesigner(controller, diagram_task)
    accepted = designer.propose(
        DesignSection.COMPONENTS,
        "component Dashboard\ncomponent Service\nconnect Dashboard -> Service",
        "Record the existing dashboard boundary",
    )
    designer.decide(accepted.id, EntryStatus.ACCEPTED, None)
    DiagramService(controller, diagram_task).sync()
    designer.propose(
        DesignSection.COMPONENTS,
        "component ProposedLater",
        "Review another possible component",
    )
    delivery = controller.conn.execute(
        "SELECT id FROM execution_decisions WHERE execution_id=?", (execution,)
    ).fetchone()[0]
    _install_driver(tmp_path, monkeypatch, task, execution, delivery, prose_task, diagram_task)
    monkeypatch.setattr(DashboardServer, "stream_lifetime", 0.5)
    artifact_dir = Path(
        os.environ.get("HARNESS_DASHBOARD_SCREENSHOT_DIR", tmp_path / "screenshots")
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HARNESS_DASHBOARD_SCREENSHOT_DIR", str(artifact_dir))
    with serve(db_path, task) as server:
        snapshot = api(server, f"?task={task}")
        assert snapshot["task"]["execution"]["id"] == execution
        assert snapshot["task"]["execution"]["blockers"], snapshot["task"]["execution"]
        epoch = {
            "epoch_id": snapshot["task"]["execution"]["epoch_id"],
            "authority_generation": snapshot["task"]["execution"]["authority_generation"],
        }
        initial_phases = (
            controller.get_task(prose_task).phase,
            controller.get_task(diagram_task).phase,
        )
        captures = {}
        screenshots = {}
        for name, width, height in (("desktop", 1280, 800), ("mobile", 390, 844)):
            url = server.url + f"?task={task}"
            # Headless Chrome's native window has a platform-specific minimum
            # width. CDP sets the actual document viewport for narrow layouts.
            with chrome(tmp_path / f"{name}-driver-profile", url, width, height) as call:
                wait(call, "window.recoveryKeyboardReady === true")
                for kind in ("keyDown", "keyUp"):
                    call(
                        "Input.dispatchKeyEvent",
                        {"type": kind, "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9},
                    )
                wait(call, "Boolean(document.querySelector('#recovery-ux-result'))")
                captures[name] = evaluate(
                    call, "JSON.parse(document.querySelector('#recovery-ux-result').textContent)"
                )
        _install_capture_driver(tmp_path, monkeypatch)
        monkeypatch.setattr(DashboardServer, "stream_lifetime", 30.0)
        screenshots["desktop"] = _cdp_capture(
            server,
            tmp_path / "capture-desktop-profile",
            "dashboard-recovery-1280x800.png",
            1280,
            800,
            task,
        )
        screenshots["mobile"] = _cdp_capture(
            server,
            tmp_path / "capture-mobile-profile",
            "dashboard-recovery-390x844.png",
            390,
            844,
            task,
        )
        _install_capture_driver(tmp_path, monkeypatch, active=True)
        active_screenshots = {}
        for name, width, height in (("desktop", 1280, 800), ("mobile", 390, 844)):
            active_screenshots[name] = _cdp_capture(
                server,
                tmp_path / f"active-{name}-profile",
                f"active-worker-history-{width}x{height}.png",
                width,
                height,
                task,
            )
        assert (
            controller.get_task(prose_task).phase,
            controller.get_task(diagram_task).phase,
        ) == initial_phases
    for name, observed in captures.items():
        assert "error" not in observed, {"viewport": name, **observed}
        assert "Refresh recorded status" in observed["buttons"]
        assert "Continue task" in observed["buttons"]
        assert not any(
            label in observed["buttons"] for label in ["Reconnect", "Resume native session"]
        )
        assert any("never continues the native agent" in hint for hint in observed["actionHints"])
        assert observed["decision"] == "Saved · delivery failed"
        assert "does not verify environment repair" in observed["outcome"]
        assert observed["duplicateFormBefore"] == 0
        assert "original text and identity" in observed["retryResult"]
        assert observed["retryBody"] == {
            "task": task,
            "execution": execution,
            "delivery": delivery,
            **epoch,
        }
        assert observed["noFormForPending"] == 0
        assert observed["refreshError"].startswith("Action failed:")
        assert observed["refreshErrorAfterRender"] == observed["refreshError"]
        assert observed["refreshBody"] == {"task": task, "execution": execution, **epoch}
        assert observed["resumeBody"] == {"task": task, "execution": execution, **epoch}
        assert "request was accepted" in observed["resumeMessage"]
        assert observed["resumeAfterRender"] == observed["resumeMessage"]
        assert observed["reconciliationDraft"] == {
            "text": "Inspected recorded host exit; preserve this draft",
            "outcome": "not-started",
            "focus": "execution-reconcile-evidence",
            "selection": [2, 7],
        }
        assert "without a verified implementation result" in observed["incomplete"]
        assert "complete" not in observed["resumeMessage"].lower()
        assert observed["diagramUi"]["proseState"] is True
        assert observed["diagramUi"]["proseGuidance"] is True
        assert observed["diagramUi"]["independentProvenance"] is True
        assert observed["diagramUi"]["effectiveState"] is True
        assert observed["diagramUi"]["currentDiagramCount"] > 0
        assert observed["diagramUi"]["pendingProposal"] is True
        assert observed["diagramUi"]["proposalControls"] == 0
        assert observed["diagramUi"]["unchangedPhase"] == "design"
        assert observed["headings"] == ["Current status", "Developer decision", "Next step"]
        assert "Specification approved" in observed["currentStatus"]
        assert "Interrupted" in observed["currentStatus"]
        assert "Keep the current format" in observed["whyBlocked"]
        assert "Continuing a native session does not resolve this blocker" in observed["nextStep"]
        assert observed["detailsCollapsed"] and observed["statusBeforeMetadata"]
        assert observed["focusStyle"] != "none"
        assert observed["viewportWidth"] == (1280 if name == "desktop" else 390)
        assert observed["documentWidth"] <= observed["viewportWidth"]
        active = observed["activeUi"]
        assert active["resumeDisabled"] and active["resumeRequestUnchanged"]
        assert "already active" in active["activeGuidance"]
        assert "Restart" not in active["activeGuidance"]
        assert active["historicalError"] and active["noCurrentRetiredAlert"]
        assert active["retiredAlertCount"] == 0 and active["noRetiredRetry"]
        assert active["verifiedAcknowledged"]
        assert active["activeFailureAlert"] and active["activeFailureRetry"]
        assert active["overflow"] is False
        assert all(
            box["left"] >= 0 and box["right"] <= observed["viewportWidth"]
            for box in observed["boxes"]
        )
    for name, viewport in (("desktop", 1280), ("mobile", 390)):
        image = screenshots[name]
        assert image["viewport_width"] == viewport
        assert image["global_horizontal_overflow"] is False
        assert image["stream_open"] is True
        assert all(Path(path).stat().st_size > 1024 for path in image["screenshots"])
    browser_version = subprocess.run(
        [CHROME, "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    (artifact_dir / "dashboard-recovery-capture-metrics.json").write_text(
        json.dumps(
            {
                "browser": browser_version,
                "viewports": {
                    name: {
                        "functional": capture,
                        "screenshots": screenshots[name],
                        "active_screenshots": active_screenshots[name],
                    }
                    for name, capture in captures.items()
                },
                "visual_observations": [
                    "desktop and narrow viewports have no document-wide horizontal overflow",
                    "status summary precedes collapsed technical metadata",
                    "current status, blocker recommendation, and next step are separately visible",
                    "keyboard focus has a visible outline",
                    "the approved specification preview is expanded in the CDP captures",
                    "active-worker captures show disabled continuation and retired error history",
                    "active failure remains alerted while the retired request is historical",
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
