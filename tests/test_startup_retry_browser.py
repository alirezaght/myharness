"""Real Chrome startup recovery with genuine approved initialized-run authority."""

# ruff: noqa: F811

import base64
import os
from pathlib import Path

import pytest

from tests.test_dashboard import serve
from tests.test_dashboard_browser import CHROME
from tests.test_dashboard_live_view_state_browser import chrome, evaluate, wait
from tests.test_startup_retry import initialized_failure, operations  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.skipif(CHROME is None, reason="Chrome unavailable")]


@pytest.mark.parametrize("width", [1280, 390])
def test_startup_failure_ready_retry_and_pending_result(
    initialized_failure, monkeypatch, tmp_path, width
):
    controller, execution, _, envelope = initialized_failure
    calls = []
    monkeypatch.setattr("harness.execution_worker._spawn_worker", lambda *a: calls.append(a))
    with (
        serve(Path(envelope["database"]), execution["task_id"]) as server,
        chrome(tmp_path / "chrome", server.url + "?tab=implementation", width, 1000) as call,
    ):
        wait(call, 'Boolean(document.getElementById("execution"))')
        assert evaluate(
            call, 'document.body.innerText.includes("Original startup diagnostic unavailable")'
        )
        assert evaluate(
            call,
            '!Array.from(document.querySelectorAll("button"))'
            '.some(b => b.textContent === "Continue task")',
        )
        assert evaluate(call, "document.documentElement.scrollWidth <= innerWidth")
        # Current readiness is independent of the unavailable historical startup cause.
        package = __import__(
            "harness.launch_readiness", fromlist=["installed_package"]
        ).installed_package(None, None)
        executable = Path(package["executable"])
        assert executable.resolve().is_relative_to(tmp_path.resolve())
        original = executable.read_bytes()
        executable.write_text("#!/bin/sh\necho 'codex-cli 9.9.9'\n")
        evaluate(call, "refreshDashboard()")
        assert evaluate(
            call, 'document.body.innerText.includes("unsupported native executable version")'
        )
        assert evaluate(
            call,
            'Array.from(document.querySelectorAll("button"))'
            '.find(b => b.textContent === "Retry startup").disabled',
        )
        assert not calls
        executable.write_bytes(original)
        evaluate(call, "refreshDashboard()")
        assert evaluate(
            call,
            '!Array.from(document.querySelectorAll("button"))'
            '.find(b => b.textContent === "Retry startup").disabled',
        )
        path = Path(os.environ.get("HARNESS_DASHBOARD_SCREENSHOT_DIR", str(tmp_path)))
        path.mkdir(parents=True, exist_ok=True)
        (path / f"startup-retry-{width}.png").write_bytes(
            base64.b64decode(call("Page.captureScreenshot", {"format": "png"})["data"])
        )
        evaluate(
            call,
            """(() => {
          const original = window.fetch;
          window.fetch = async (input, options) => {
            const response = await original(input, options);
            if (String(input) === "/api/execution/retry-start") {
              window.retryResponse = {status: response.status, body: await response.clone().json()};
            }
            return response;
          };
          Array.from(document.querySelectorAll("button"))
            .find(b => b.textContent === "Retry startup").click();
        })()""",
        )
        wait(call, "Boolean(window.retryResponse)")
        response = evaluate(call, "window.retryResponse")
        assert response["status"] == 200, response
        assert response["body"]["status"] == "WorkerOwned", response
        assert len(calls) == 1
        assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1
        assert controller.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1
        assert (
            controller.conn.execute(
                "SELECT COUNT(*) FROM execution_attempts WHERE mode='retry-start'"
            ).fetchone()[0]
            == 1
        )


def test_definitive_preflight_failure_allows_host_switch(
    controller, sample_repo, db_path, monkeypatch, tmp_path
):
    from tests.test_dashboard_guidance import approved

    tid = approved.__wrapped__(controller, sample_repo, monkeypatch)
    selected_bin = tmp_path / "selected-host-bin"
    selected_bin.mkdir()
    codex = selected_bin / "codex"
    codex.write_text("#!/bin/sh\necho 'codex-cli 9.9.9'\n")
    codex.chmod(0o700)
    monkeypatch.setenv("PATH", str(selected_bin) + os.pathsep + os.environ["PATH"])
    calls = []
    monkeypatch.setattr(
        "harness.execution_worker.launch_worker",
        lambda conn, eid: calls.append(eid) or {"status": "Reserved"},
    )
    with (
        serve(db_path, tid) as server,
        chrome(tmp_path / "chrome", server.url + "?tab=implementation", 390, 1000) as call,
    ):
        wait(call, 'Boolean(document.querySelector("#implementation-host"))')
        evaluate(
            call,
            """(() => {
          const host = document.getElementById("implementation-host");
          host.value = "codex"; host.dispatchEvent(new Event("change"));
          document.getElementById("implementation-start-button").click();
        })()""",
        )
        wait(
            call,
            'document.getElementById("implementation-start")?.innerText'
            '.includes("No new run was reserved")',
        )
        assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0
        evaluate(call, "refreshDashboard()")
        assert evaluate(call, '!document.getElementById("implementation-host").disabled')
        evaluate(
            call,
            """(() => {
          const host = document.getElementById("implementation-host");
          host.value = "claude"; host.dispatchEvent(new Event("change"));
          document.getElementById("implementation-start-button").click();
        })()""",
        )
        wait(call, 'Boolean(document.getElementById("execution"))')
        assert len(calls) == 1
        assert controller.conn.execute("SELECT host FROM executions").fetchone()[0] == "claude"
        assert controller.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1


def test_lost_success_response_recovers_same_run_after_host_drift(
    controller, sample_repo, db_path, monkeypatch, tmp_path
):
    import harness.launch_readiness as readiness
    from tests.test_dashboard_guidance import approved

    tid = approved.__wrapped__(controller, sample_repo, monkeypatch)
    dispatches = []

    def dispatch(conn, eid):
        dispatches.append(eid)
        conn.execute("UPDATE executions SET status='WorkerOwned' WHERE id=?", (eid,))
        monkeypatch.setattr(
            readiness,
            "readiness",
            lambda *a, **k: {
                "checks": [{"status": "BLOCKED", "reason": "host drift after dispatch"}]
            },
        )
        return {"status": "WorkerOwned"}

    monkeypatch.setattr("harness.execution_worker.launch_worker", dispatch)
    with (
        serve(db_path, tid) as server,
        chrome(tmp_path / "chrome", server.url + "?tab=implementation", 390, 1000) as call,
    ):
        wait(call, 'Boolean(document.getElementById("implementation-host"))')
        evaluate(
            call,
            """(() => {
          const original = window.fetch;
          window.replies = [];
          window.fetch = async (input, options) => {
            const response = await original(input, options);
            if (String(input) === "/api/execution/start") {
              replies.push({status: response.status, data: await response.clone().json()});
              if (replies.length === 1) throw new Error("lost successful start response");
            }
            return response;
          };
          const host = document.getElementById("implementation-host");
          host.value = "claude"; host.dispatchEvent(new Event("change"));
          document.getElementById("implementation-start-button").click();
        })()""",
        )
        wait(
            call, 'window.replies?.length === 2 && !document.getElementById("implementation-start")'
        )
        replies = evaluate(call, "window.replies")
        assert [reply["status"] for reply in replies] == [200, 200]
        assert replies[0]["data"]["execution"]["id"] == replies[1]["data"]["execution"]["id"]
        assert len(dispatches) == 1
        assert controller.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1
        assert controller.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1


def test_reserved_preflight_error_keeps_host_locked_across_refresh(
    controller, sample_repo, db_path, monkeypatch, tmp_path
):
    import harness.launch_readiness as readiness
    from tests.test_dashboard_guidance import approved

    tid = approved.__wrapped__(controller, sample_repo, monkeypatch)
    calls = []

    def unavailable(conn, eid):
        calls.append(eid)
        monkeypatch.setattr(
            readiness,
            "readiness",
            lambda *a, **k: {
                "checks": [{"status": "BLOCKED", "reason": "host changed before dispatch"}]
            },
        )
        raise OSError("dispatch unavailable after reservation")

    monkeypatch.setattr("harness.execution_worker.launch_worker", unavailable)
    with (
        serve(db_path, tid) as server,
        chrome(tmp_path / "chrome", server.url + "?tab=implementation", 390, 1000) as call,
    ):
        wait(call, 'Boolean(document.getElementById("implementation-host"))')
        evaluate(
            call,
            """(() => {
          const host = document.getElementById("implementation-host");
          host.value = "claude"; host.dispatchEvent(new Event("change"));
          document.getElementById("implementation-start-button").click();
        })()""",
        )
        wait(
            call,
            'document.getElementById("implementation-start")?.innerText'
            '.includes("remains reserved")',
        )
        evaluate(call, "refreshDashboard()")
        assert evaluate(call, 'document.getElementById("implementation-host").disabled')
        assert evaluate(call, 'document.getElementById("implementation-host").value === "claude"')
        assert evaluate(
            call,
            '!document.getElementById("implementation-start").innerText'
            '.includes("choose another host")',
        )
        assert len(calls) == 1
        assert controller.conn.execute("SELECT host FROM executions").fetchone()[0] == "claude"
        assert controller.conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1
