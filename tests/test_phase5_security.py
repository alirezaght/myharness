"""Phase 5 dashboard/report additions remain read-only and treat inputs as text."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_delivery_dashboard_has_no_browser_effect_or_html_injection_surface():
    # Static policy guard, kept alongside the runtime proof in test_dashboard_delivery_browser.py
    # (test_hostile_delivery_text_and_urls_stay_inert_and_issue_only_reads): hostile text stays
    # inert, unsafe links are refused and only same-origin GETs are issued.
    source = (ROOT / "harness/dashboard/static/delivery.js").read_text(encoding="utf-8")
    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source
    assert "eval(" not in source
    assert "fetch(" not in source
    assert "XMLHttpRequest" not in source
    assert "textContent" in source
    assert "safeDeliveryUrl" in source
    assert "Browser runs require a separate explicit approval" in source
    assert "stagingDeployment.deployment_id" in source


EFFECTFUL = (
    "approve_browser",
    "run_browser",
    "reconcile_browser",
    "probe_browser",
    "propose_browser",
)


def _forbid_browser_effects(monkeypatch):
    def forbidden(name):
        def call(*args, **kwargs):
            raise AssertionError(f"read-only command called {name}")

        return call

    for name in EFFECTFUL:
        monkeypatch.setattr(f"harness.cli_browser.{name}", forbidden(name))


def _cli(db_path, capsys, *argv):
    from harness.cli import main

    code = main(["--db", str(db_path), *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("argv", [("report",), ("browser", "status")])
def test_report_and_browser_status_are_read_only_and_print_the_stored_view(
    controller, sample_repo, monkeypatch, db_path, capsys, argv
):
    from harness.browser_store import browser_view
    from harness.execution_logs import redact_value
    from harness.reporting import completion_report
    from tests.database_snapshot import logical_contents
    from tests.execution_helpers import approved_execution
    from tests.test_dashboard_delivery import _delivery_row

    task_id, execution_id = approved_execution(controller, sample_repo, monkeypatch)
    _delivery_row(controller.conn, execution_id)
    controller.conn.commit()
    expected = (completion_report if argv == ("report",) else browser_view)(
        controller.conn, execution_id
    )
    _forbid_browser_effects(monkeypatch)
    before = logical_contents(db_path)
    code, out, err = _cli(db_path, capsys, *argv, task_id, execution_id)
    assert (code, err) == (0, "")
    assert json.loads(out) == json.loads(json.dumps(redact_value(expected)))
    if argv == ("report",):
        assert json.loads(out)["merge_request"]["iid"] == 14  # the stored delivery, not a stub
    assert logical_contents(db_path) == before
    # A foreign task is refused before any view is read.
    code, out, err = _cli(db_path, capsys, *argv, "t-00000000", execution_id)
    assert code == 1 and out == "" and "another task" in err
    assert logical_contents(db_path) == before


@pytest.mark.parametrize(
    "argv,handler,arguments",
    [
        (
            ("approve", "--plan", "P1", "--request-id", "req-1", "--attempt", "2"),
            "approve_browser",
            (("P1",), {"request_id": "req-1", "attempt": 2}),
        ),
        (("run", "--approval", "A1"), "run_browser", (("A1",), {})),
        (("reconcile", "--run", "R1"), "reconcile_browser", (("R1",), {})),
    ],
)
def test_each_effectful_browser_action_dispatches_only_its_named_handler(
    controller, sample_repo, monkeypatch, db_path, capsys, argv, handler, arguments
):
    from tests.execution_helpers import approved_execution

    task_id, execution_id = approved_execution(controller, sample_repo, monkeypatch)
    controller.conn.commit()
    _forbid_browser_effects(monkeypatch)
    calls = []

    def record(_controller, called_execution, *args, **kwargs):
        calls.append((called_execution, args, kwargs))
        return {"recorded": handler}

    monkeypatch.setattr(f"harness.cli_browser.{handler}", record)
    code, out, err = _cli(db_path, capsys, "browser", argv[0], task_id, execution_id, *argv[1:])
    assert (code, err) == (0, "")
    assert json.loads(out) == {"recorded": handler}
    assert calls == [(execution_id, *arguments)]


def test_configured_browser_executable_is_shared_with_dashboard_tests(tmp_path, monkeypatch):
    from harness.browser_driver import chrome_executable
    from tests.test_dashboard_browser import _chrome

    browser = tmp_path / "trusted browser"
    browser.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    browser.chmod(0o700)
    monkeypatch.setenv("HARNESS_BROWSER_EXECUTABLE", str(browser))
    assert chrome_executable() == str(browser.resolve())
    assert _chrome() == str(browser.resolve())
    monkeypatch.setenv("HARNESS_BROWSER_EXECUTABLE", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="absolute executable"):
        _chrome()


def test_browser_cli_dispatch_records_the_requested_action():
    # Public parser contract; the dispatch outcome is proven by the tests above.
    from harness.cli import build_parser
    from harness.cli_browser import cmd_browser

    args = build_parser().parse_args(["browser", "status", "task-id", "execution-id"])
    assert args.command == "browser"
    assert args.action == "status"
    assert args.handler is cmd_browser
