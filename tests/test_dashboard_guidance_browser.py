"""Actual entrypoint Chrome smoke, explicit launch and responsive guidance."""

# ruff: noqa: F811

import base64
import os
from pathlib import Path

import pytest

from tests.test_dashboard import serve
from tests.test_dashboard_browser import CHROME
from tests.test_dashboard_guidance import approved  # noqa: F401
from tests.test_dashboard_live_view_state_browser import chrome, evaluate, wait

pytestmark = [pytest.mark.browser, pytest.mark.skipif(CHROME is None, reason="Chrome unavailable")]


@pytest.mark.parametrize("width", [1280, 390])
def test_guidance_start_real_browser(controller, approved, db_path, monkeypatch, tmp_path, width):  # noqa: F811
    launches = []
    monkeypatch.setattr(
        "harness.execution_worker.launch_worker",
        lambda conn, eid: launches.append(eid) or {"status": "Reserved"},
    )
    with (
        serve(db_path, approved) as server,
        chrome(tmp_path / "chrome", server.url, width, 900) as call,
    ):
        wait(call, 'Boolean(document.querySelector("#implementation-start-button"))')
        assert evaluate(
            call, 'document.getElementById("current-status").innerText.includes("external-session")'
        )
        assert evaluate(call, 'document.getElementById("implementation-start-button").disabled')
        assert evaluate(
            call, 'document.querySelector("#current-status").getBoundingClientRect().top < 650'
        )
        assert evaluate(call, "document.documentElement.scrollWidth <= innerWidth")
        assert evaluate(
            call,
            'typeof renderExecution === "function" && '
            'typeof renderImplementationStart === "function" && '
            'typeof renderKnowledgePanel === "function"',
        )
        assert not launches
        evaluate(
            call,
            """(() => {
          const host = document.getElementById("implementation-host");
          host.value = "codex"; host.dispatchEvent(new Event("change")); host.focus();
          return true;
        })()""",
        )
        # Genuine same-identity refresh keeps the selected host and keyboard focus.
        evaluate(call, "refreshDashboard()")
        assert evaluate(
            call,
            'document.activeElement.id === "implementation-host" && '
            'document.activeElement.value === "codex"',
        )
        # Design instructions stay secondary and retain their disclosure state on refresh.
        assert evaluate(
            call, '!document.querySelector("[data-view-disclosure=design-diagnostics]").open'
        )
        assert evaluate(call, '!document.querySelector(".diagram-guidance").checkVisibility()')
        evaluate(
            call, 'document.querySelector("[data-view-disclosure=design-diagnostics]").open = true'
        )
        evaluate(call, "refreshDashboard()")
        assert evaluate(
            call, 'document.querySelector("[data-view-disclosure=design-diagnostics]").open'
        )
        assert evaluate(call, 'document.querySelector(".diagram-guidance").checkVisibility()')
        evaluate(
            call, 'document.querySelector("[data-view-disclosure=design-diagnostics]").open = false'
        )
        evaluate(call, "refreshDashboard()")
        assert evaluate(
            call, '!document.querySelector("[data-view-disclosure=design-diagnostics]").open'
        )
        path = Path(
            os.environ.get(
                "HARNESS_DASHBOARD_SCREENSHOT_DIR",
                str(tmp_path),
            )
        )
        path.mkdir(parents=True, exist_ok=True)
        screenshot = call("Page.captureScreenshot", {"format": "png"})
        (path / f"guidance-{width}.png").write_bytes(base64.b64decode(screenshot["data"]))
        evaluate(call, 'document.getElementById("implementation-start-button").click()')
        wait(call, 'Boolean(document.querySelector("#execution"))')
        assert len(launches) == 1
        assert evaluate(
            call,
            'document.getElementById("current-status").innerText'
            '.includes("Starting implementation")',
        )
        assert evaluate(
            call, 'document.querySelectorAll("#implementation-start-button").length === 0'
        )
        assert controller.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1


def test_lost_start_response_retries_same_reservation(
    controller, approved, db_path, monkeypatch, tmp_path
):  # noqa: F811
    launches = []
    monkeypatch.setattr(
        "harness.execution_worker.launch_worker",
        lambda conn, eid: launches.append(eid) or {"status": "Reserved"},
    )
    with (
        serve(db_path, approved) as server,
        chrome(tmp_path / "chrome", server.url, 1280, 900) as call,
    ):
        wait(call, 'Boolean(document.querySelector("#implementation-start-button"))')
        evaluate(
            call,
            """(() => {
          window.originalStartFetch = window.fetch;
          window.startPayloads = [];
          let losses = 3;
          window.fetch = async (input, options) => {
            if (String(input) === "/api/execution/start") {
              startPayloads.push(options.body);
              const response = await originalStartFetch(input, options);
              if (losses-- > 0) throw new Error("lost response after reservation");
              return response;
            }
            return originalStartFetch(input, options);
          };
          const host = document.getElementById("implementation-host");
          host.value = "claude"; host.dispatchEvent(new Event("change"));
          document.getElementById("implementation-start-button").click();
        })()""",
        )
        wait(
            call,
            'document.getElementById("implementation-start-button")?.textContent '
            '=== "Retry same start request"',
        )
        evaluate(call, "refreshDashboard()")
        assert evaluate(call, 'document.getElementById("implementation-host").disabled')
        evaluate(call, 'document.getElementById("implementation-start-button").click()')
        wait(
            call,
            'startPayloads.length === 4 && !document.getElementById("implementation-start-button")',
        )
        assert evaluate(call, "startPayloads.every(payload => payload === startPayloads[0])")
        assert len(set(launches)) == 1
        assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1
        assert controller.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1


def test_stale_approval_requires_reload_and_explicit_new_click(
    controller, approved, sample_repo, db_path, monkeypatch, tmp_path
):  # noqa: F811
    from harness.approval import ApprovalGate
    from harness.spec_store import SpecStore

    launches = []
    monkeypatch.setattr("harness.execution_worker.launch_worker", lambda *a: launches.append(a))
    with (
        serve(db_path, approved) as server,
        chrome(tmp_path / "chrome", server.url, 390, 900) as call,
    ):
        wait(call, 'Boolean(document.querySelector("#implementation-host"))')
        evaluate(call, "source.close()")
        ApprovalGate(
            controller, approved, SpecStore(sample_repo / "docs/specs" / approved), sample_repo
        ).approve()
        evaluate(
            call,
            """(() => {
          const host = document.getElementById("implementation-host");
          host.value = "codex"; host.dispatchEvent(new Event("change"));
          document.getElementById("implementation-start-button").click();
        })()""",
        )
        wait(call, 'Boolean(document.getElementById("implementation-start-reload"))')
        assert evaluate(call, 'document.getElementById("implementation-start-button").disabled')
        assert not launches
        evaluate(call, 'document.getElementById("implementation-start-reload").click()')
        wait(call, '!document.getElementById("implementation-start-reload")')
        assert evaluate(call, 'document.getElementById("implementation-host").value === ""')
        assert not launches
        assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0


def test_inflight_start_cannot_repaint_other_task(
    controller, approved, db_path, monkeypatch, tmp_path
):  # noqa: F811
    other = controller.create_task("Another task awaiting requirements", "trivial").id
    monkeypatch.setattr(
        "harness.execution_worker.launch_worker", lambda conn, eid: {"status": "Reserved"}
    )
    with (
        serve(db_path, approved) as server,
        chrome(tmp_path / "chrome", server.url, 1280, 900) as call,
    ):
        wait(call, 'Boolean(document.querySelector("#implementation-host"))')
        evaluate(
            call,
            """(() => {
          source.close();
          const original = window.fetch;
          window.fetch = async (input, options) => {
            const response = await original(input, options);
            if (String(input) === "/api/execution/start") {
              return await new Promise(resolve => {
                window.releaseStart = () => resolve(response);
              });
            }
            return response;
          };
          const host = document.getElementById("implementation-host");
          host.value = "codex"; host.dispatchEvent(new Event("change"));
          document.getElementById("implementation-start-button").click();
        })()""",
        )
        wait(call, 'typeof window.releaseStart === "function"')
        evaluate(call, f'history.replaceState(null, "", "/?task={other}"); refreshDashboard()')
        wait(
            call,
            'document.querySelector("#current-status").innerText'
            '.includes("Specification in progress")',
        )
        evaluate(call, "window.releaseStart()")
        wait(call, "implementationStarts.size === 0")
        assert evaluate(call, f'document.querySelector("#task").innerText.includes("{other}")')
        assert evaluate(call, '!document.getElementById("implementation-start")')
        assert (
            controller.conn.execute(
                "SELECT COUNT(*) FROM executions WHERE task_id=?", (other,)
            ).fetchone()[0]
            == 0
        )


def test_rapid_duplicate_start_clicks_while_response_is_pending(
    controller, approved, db_path, monkeypatch, tmp_path
):
    launches = []
    monkeypatch.setattr(
        "harness.execution_worker.launch_worker",
        lambda conn, eid: launches.append(eid) or {"status": "Reserved"},
    )
    with (
        serve(db_path, approved) as server,
        chrome(tmp_path / "chrome", server.url, 1280, 900) as call,
    ):
        wait(call, 'Boolean(document.querySelector("#implementation-host"))')
        evaluate(
            call,
            """(() => {
          source.close();
          const original = window.fetch;
          window.startPosts = 0;
          window.fetch = async (input, options) => {
            if (String(input) !== "/api/execution/start") return original(input, options);
            startPosts += 1;
            const response = await original(input, options);
            return new Promise(resolve => { window.releaseStart = () => resolve(response); });
          };
          const host = document.getElementById("implementation-host");
          host.value = "codex"; host.dispatchEvent(new Event("change"));
          const originalButton = document.getElementById("implementation-start-button");
          originalButton.click();
          // Exercise retained listeners as well as the replacement disabled button.
          for (let i = 0; i < 5; i++) {
            originalButton.click();
            document.getElementById("implementation-start-button").click();
          }
        })()""",
        )
        wait(call, 'typeof window.releaseStart === "function"')
        assert evaluate(call, "startPosts === 1")
        assert evaluate(call, 'document.getElementById("implementation-start-button").disabled')
        assert len(launches) == 1
        assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1
        assert controller.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1
        evaluate(call, 'document.getElementById("implementation-start-button").click()')
        assert evaluate(call, "startPosts === 1")
        evaluate(call, "window.releaseStart()")
        wait(call, '!document.getElementById("implementation-start-button")')
        assert evaluate(call, "startPosts === 1")
        assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1
        assert controller.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1


def test_effective_design_keeps_diagrams_visible_with_secondary_instructions(
    controller, sample_repo, db_path, monkeypatch, tmp_path
):
    from harness.approval import ApprovalGate
    from harness.spec_store import SpecStore
    from tests.test_dashboard_approval import approval_task

    monkeypatch.chdir(sample_repo)
    tid = approval_task(db_path, sample_repo)
    ApprovalGate(
        controller, tid, SpecStore(sample_repo / "docs/specs" / tid), sample_repo
    ).approve()
    monkeypatch.setattr(
        "harness.execution_worker.launch_worker", lambda *a: pytest.fail("read launched work")
    )
    with (
        serve(db_path, tid) as server,
        chrome(tmp_path / "chrome", server.url, 1280, 900) as call,
    ):
        wait(call, 'Boolean(document.querySelector("#diagrams svg"))')
        assert evaluate(call, 'document.querySelector("#implementation-start-button") !== null')
        assert evaluate(
            call, 'document.querySelector(".diagram-provenance").textContent.includes("effective")'
        )
        assert evaluate(call, '!document.querySelector(".diagram-guidance").checkVisibility()')
        assert evaluate(call, 'document.querySelector("#diagrams svg").getClientRects().length > 0')
        evaluate(
            call, 'document.querySelector("[data-view-disclosure=design-diagnostics]").open = true'
        )
        evaluate(call, "refreshDashboard()")
        assert evaluate(
            call, 'document.querySelector("[data-view-disclosure=design-diagnostics]").open'
        )
        assert evaluate(call, 'document.querySelector("#diagrams svg").getClientRects().length > 0')
