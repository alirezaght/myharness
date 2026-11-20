"""Deterministic App Server subprocess boundary; no live native calls."""

import pytest

from harness.authoring import AuthoringService
from harness.investigation_native import NativeInvestigator
from harness.investigation_policy import AcquisitionError, authorize_request
from tests.investigation_fixture import native_fixture as native_fixture


def request(controller, tmp_path, qualified):
    task = controller.create_task("AR-2906", "trivial")
    return authorize_request(AuthoringService(controller, tmp_path, task.id), qualified=qualified)


def test_production_transport_dispatches_only_exact_reads_and_reaps(
    controller, tmp_path, native_fixture
):
    qualified, _, calls = native_fixture
    adapter = NativeInvestigator(qualified)
    result = adapter.acquire(request(controller, tmp_path, qualified))
    assert result["issue"] == "AR-2906" and result["sources"][0]["kind"] == "ticket"
    assert adapter.process.poll() == 0 and adapter.settlement == "settled"
    outbound = [c for c in calls() if c.get("method") == "mcpServer/tool/call"]
    assert [c["params"]["tool"] for c in outbound] == [
        "getAccessibleAtlassianResources",
        "getJiraIssue",
    ]
    assert outbound[-1]["params"]["arguments"] == {
        "cloudId": "cloud-one",
        "issueIdOrKey": "AR-2906",
        "fields": ["summary", "description", "status", "updated"],
    }
    assert not any(c.get("method", "").startswith(("turn/", "command/")) for c in calls())


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("auth", "authentication_required"),
        ("wrong-issue", "scope_denied"),
        ("wrong-id", "protocol_error"),
        ("runtime", "issue_unavailable"),
        ("interactive", "scope_denied"),
        ("oversize", "output_limit"),
        ("timeout", "timeout"),
        ("read-denied", "issue_unavailable"),
        ("nonzero", "protocol_error"),
    ],
)
def test_transport_negative_cases_settle_without_raw_diagnostics(
    controller, tmp_path, native_fixture, mode, reason
):
    qualified, set_mode, _ = native_fixture
    set_mode(mode)
    adapter = NativeInvestigator(qualified, timeout=1, rpc_timeout=1)
    with pytest.raises(AcquisitionError) as failure:
        adapter.acquire(request(controller, tmp_path, qualified))
    assert failure.value.reason == reason
    assert adapter.process.poll() is not None and adapter.settlement == "settled"
    assert "secret-value" not in str(failure.value)


def test_explicit_reconnect_has_exact_scope_and_still_reads_bound_issue(
    controller, tmp_path, native_fixture
):
    qualified, _, calls = native_fixture
    handoffs = []
    adapter = NativeInvestigator(qualified)
    result = adapter.reconnect(
        request(controller, tmp_path, qualified), publish_handoff=handoffs.append
    )
    assert result["issue"] == "AR-2906" and len(handoffs) == 1
    logins = [c for c in calls() if c.get("method") == "mcpServer/oauth/login"]
    assert len(logins) == 1 and logins[0]["params"]["scopes"] == ["read:jira:agent-interface"]
    assert handoffs[0]["login_id"] == "owned-login"


def test_foreign_login_completion_cannot_clear_recovery(controller, tmp_path, native_fixture):
    qualified, set_mode, calls = native_fixture
    set_mode("foreign-login")
    with pytest.raises(AcquisitionError, match="scope"):
        NativeInvestigator(qualified).reconnect(
            request(controller, tmp_path, qualified), publish_handoff=lambda value: None
        )
    assert len([c for c in calls() if c.get("method") == "mcpServer/oauth/login"]) == 1


def test_reconnect_does_not_require_prior_read_or_site_grant(controller, tmp_path, native_fixture):
    from dataclasses import replace

    qualified, _, calls = native_fixture
    qualified = replace(qualified, read_qualified=False, sites=(), tools={}, arguments={})
    task = controller.create_task("AR-2906", "trivial")
    selected = authorize_request(
        AuthoringService(controller, tmp_path, task.id), qualified=qualified, purpose="reconnect"
    )
    result = NativeInvestigator(qualified).reconnect(selected, publish_handoff=lambda value: None)
    assert result["reconnected"] and result["read_qualified"] is False
    assert not any(c.get("method") == "mcpServer/tool/call" for c in calls())
    assert len([c for c in calls() if c.get("method") == "mcpServer/oauth/login"]) == 1


def test_authentication_failure_before_inventory_still_allows_explicit_consent(
    controller, tmp_path, native_fixture
):
    qualified, set_mode, calls = native_fixture
    set_mode("auth-before-login")
    result = NativeInvestigator(qualified).reconnect(
        request(controller, tmp_path, qualified), publish_handoff=lambda value: None
    )
    assert result["issue"] == "AR-2906"
    assert len([c for c in calls() if c.get("method") == "mcpServer/oauth/login"]) == 1


def test_login_success_without_issue_permission_is_not_acquisition_success(
    controller, tmp_path, native_fixture
):
    qualified, set_mode, calls = native_fixture
    set_mode("read-denied")
    with pytest.raises(AcquisitionError, match="could not be read"):
        NativeInvestigator(qualified).reconnect(
            request(controller, tmp_path, qualified), publish_handoff=lambda value: None
        )
    assert len([c for c in calls() if c.get("method") == "mcpServer/oauth/login"]) == 1


@pytest.mark.parametrize(
    "mode", ["remote-missing", "remote-enabled", "remote-malformed", "remote-unknown"]
)
def test_remote_control_must_be_disabled_before_thread_or_login(
    controller, tmp_path, native_fixture, mode
):
    qualified, set_mode, calls = native_fixture
    set_mode(mode)
    adapter = NativeInvestigator(qualified, timeout=1, rpc_timeout=0.2)
    with pytest.raises(AcquisitionError):
        adapter.reconnect(request(controller, tmp_path, qualified), publish_handoff=lambda _: None)
    assert not any(
        c.get("method") in {"thread/start", "mcpServer/oauth/login", "mcpServer/tool/call"}
        for c in calls()
    )
    assert adapter.process.poll() is not None and adapter.settlement == "settled"


def test_child_remote_control_marker_does_not_change_parent(
    controller, tmp_path, native_fixture, monkeypatch
):
    import json
    import os
    from pathlib import Path

    qualified, _, _ = native_fixture
    key = "CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED"
    monkeypatch.setenv(key, "parent-value")
    NativeInvestigator(qualified).acquire(request(controller, tmp_path, qualified))
    assert (
        json.loads(Path(qualified.executable["path"] + ".environment").read_text())["marker"] == "1"
    )
    assert os.environ[key] == "parent-value"


def test_remote_control_transition_stops_owned_attempt_before_any_tool(
    controller, tmp_path, native_fixture
):
    qualified, set_mode, calls = native_fixture
    set_mode("remote-later-enabled")
    adapter = NativeInvestigator(qualified)
    with pytest.raises(AcquisitionError):
        adapter.acquire(request(controller, tmp_path, qualified))
    assert not any(c.get("method", "").startswith("mcpServer/") for c in calls())
    assert adapter.process.poll() is not None and adapter.settlement == "settled"
