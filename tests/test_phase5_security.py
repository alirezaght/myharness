"""Phase 5 dashboard/report additions remain read-only and treat inputs as text."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_delivery_dashboard_has_no_browser_effect_or_html_injection_surface():
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


def test_report_cli_is_read_only_and_browser_writes_are_named_commands():
    source = (ROOT / "harness/cli_browser.py").read_text(encoding="utf-8")
    assert 'if args.command == "report"' in source
    assert 'elif args.action == "approve"' in source
    assert 'elif args.action == "run"' in source
    assert 'elif args.action == "reconcile"' in source
    assert "completion_report(controller.conn, args.execution_id)" in source
    # The report dispatch branch has no call to an effectful browser command.
    report_branch = source.split('if args.command == "report":', maxsplit=1)[1].split(
        'elif args.action == "capability":', maxsplit=1
    )[0]
    assert "approve_browser(" not in report_branch
    assert "run_browser(" not in report_branch


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
    from harness.cli import build_parser

    args = build_parser().parse_args(["browser", "status", "task-id", "execution-id"])
    assert args.command == "browser"
    assert args.action == "status"
