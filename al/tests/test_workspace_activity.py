"""Closed profile selection only; real source approval is never fabricated here."""

import copy
from pathlib import Path

import pytest

from al.installer import workspace_activity as W
from al.installer.activity_inventory import inventory_activity
from al.installer.activity_operations import operation_reasons
from al.installer.activity_retirement import seal
from al.tests.activity_helpers import git_repo, legacy, modern, write_state


def inventory(tmp_path):
    repo = git_repo(tmp_path / "source")
    runtime = Path(repo["root"]) / ".codex/al-runtime"
    value = modern(repo, runtime, session="ordinary-fixture")
    value["handoffs"] = 0
    path = write_state(runtime, value)
    invoking = {
        "repository": repo,
        "platform": "codex",
        "runtime_root": str(runtime),
        "state_path": str(path),
        "session_id": value["session_id"],
        "run_id": value["run_id"],
    }
    roots = [
        {
            "repository": repo,
            "platform": platform,
            "runtime_root": str(Path(repo["root"]) / ("." + platform) / "al-runtime"),
        }
        for platform in ("codex", "claude")
    ]
    return inventory_activity(roots, invoking_run=invoking), roots, invoking, value, path


def test_strict_terminal_profile_selection_is_not_an_installation_approval(tmp_path):
    value, _, _, _, path = inventory(tmp_path)
    before = path.read_bytes()
    assert W.select_profile(value) == "ordinary-terminal-v1"
    assert path.read_bytes() == before
    assert "eligible" not in value and "approval" not in value


@pytest.mark.parametrize("status,phase", [("RUNNING", "PLANNING"), ("APPROVED", "VERIFICATION")])
def test_current_run_never_becomes_historical_through_empty_acknowledgements(
    tmp_path, status, phase
):
    _, roots, invoking, state, path = inventory(tmp_path)
    state.update(status=status, phase=phase)
    write_state(path.parent, state)
    current = inventory_activity(roots, invoking_run=invoking)
    with pytest.raises(ValueError, match="invoking run is not ordinary terminal"):
        W.select_profile(current)


def test_unsupported_or_malformed_state_does_not_select_ordinary_or_fallback(tmp_path):
    _, roots, invoking, state, path = inventory(tmp_path)
    state["handoffs"] = None
    write_state(path.parent, state)
    current = inventory_activity(roots, invoking_run=invoking)
    with pytest.raises(ValueError, match="malformed runtime counter"):
        W.select_profile(current)
    state["handoffs"] = 0
    state["unrecognized_authority"] = True
    write_state(path.parent, state)
    current = inventory_activity(roots, invoking_run=invoking)
    assert current["stable"] is False
    record = next(
        row
        for root in current["roots"]
        for row in root["records"]
        if row["identity"]["state_path"] == invoking["state_path"]
    )
    assert record["classification"] == "unknown"
    assert record["identity"]["run_id"] is None
    assert any("unrecognized_authority" in error for error in record["errors"])
    with pytest.raises(ValueError, match="inventory is not stable"):
        W.select_profile(current)


def test_recognized_nonterminal_history_always_requires_historical_profile(tmp_path):
    _, roots, invoking, _, _ = inventory(tmp_path)
    write_state(Path(roots[1]["runtime_root"]), legacy("abandoned-fixture"))
    current = inventory_activity(roots, invoking_run=invoking)
    assert W.select_profile(current) == "acknowledged-history-v1"
    # A profile discriminator is a reconstruction result, never an eligibility grant.
    assert all(
        record["historical_status"] != "APPROVED" for record in current["roots"][1]["records"]
    )


@pytest.mark.parametrize(
    "extra", ["historical", "acknowledgements", "reviewed_history", "caller_force_ordinary"]
)
def test_ordinary_binding_cannot_carry_hidden_historical_authority(tmp_path, extra):
    binding = seal(
        {
            "schema_version": 2,
            "kind": "workspace-activity-binding-v2",
            "profile": "ordinary-terminal-v1",
            "source_digest": "a" * 64,
            "deployment_intent_digest": "b" * 64,
            "scope": {},
            "inventory": {},
            extra: [],
        }
    )
    with pytest.raises(ValueError, match="unknown or foreign"):
        W._fresh(binding, {"digest": "a" * 64}, tmp_path)


def test_missing_invoking_record_and_shared_operational_uncertainty_are_blockers(tmp_path):
    current, _, invoking, _, _ = inventory(tmp_path)
    missing = copy.deepcopy(current)
    missing["roots"][0]["records"] = []
    missing = seal({k: v for k, v in missing.items() if k != "digest"})
    with pytest.raises(ValueError, match="invoking record is absent"):
        W.select_profile(missing)
    root = current["roots"][0]
    record = copy.deepcopy(root["records"][0])
    record["operation_facts"]["writers"] = {"writer": {"state": "UNRESOLVED"}}
    record["operation_facts"]["checks"] = [
        {"id": "E1", "status": "INFRA", "cleanup": {"exit": None, "unresolved": True, "errors": []}}
    ]
    observation = {
        "operations": [{"identity": invoking, "result": "unknown", "reason": "unresolved lease"}],
        "processes": [{"record_identity": invoking, "result": "unknown"}],
    }
    reasons = operation_reasons(record, root, observation)
    assert {
        "active/unresolved writer",
        "unresolved owned-process cleanup",
        "unresolved lease",
        "live or unknown relevant process",
    } <= set(reasons)
