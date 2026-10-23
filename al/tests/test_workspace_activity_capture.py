"""Every ordinary mutation check re-captures the approved source after observing activity."""
from contextlib import contextmanager

import pytest

from al.installer import workspace_activity as W
from al.installer import workspace_ordinary
from al.installer.registry import canonical, digest


def _binding(profile):
    value = {"schema_version": 2, "kind": "workspace-activity-binding-v2", "profile": profile,
             "source_digest": "s", "deployment_intent_digest": "d", "scope": {"roots": []},
             "inventory": {"path": "inventory"}}
    if profile == "acknowledged-history-v1":
        value["historical"] = {"reviewed_history": [], "acknowledgements": []}
    return dict(value, digest=digest(canonical(value)))


@pytest.fixture
def events(monkeypatch):
    log = []
    inventory = {"invoking_run": "run", "orphan_review": None}
    monkeypatch.setattr(W, "revalidate_source", lambda source: log.append("capture"))
    import al.installer.workspace_source as source_module
    monkeypatch.setattr(source_module, "revalidate_source", lambda source: log.append("capture"))
    monkeypatch.setattr(W, "revalidate_scope", lambda scope, root: None)
    monkeypatch.setattr(W, "read_reference", lambda ref: inventory)
    monkeypatch.setattr(W, "validate_seal", lambda value, kind: value)
    monkeypatch.setattr(W, "invoking", lambda source: "run")
    monkeypatch.setattr(W, "_nonstate", lambda stored: None)
    monkeypatch.setattr(W, "inventory_activity", lambda roots, **kw: inventory)
    monkeypatch.setattr(W, "select_profile", lambda current: W._PROFILE)

    @contextmanager
    def locked(current):
        yield [], []
    monkeypatch.setattr(W, "locked_activity", locked)
    monkeypatch.setattr(W, "observe_activity", lambda current, **kw: log.append("observe") or {"observation": True})
    monkeypatch.setattr(workspace_ordinary, "assess", lambda current, observation, source: source_module.revalidate_source(source) or {"eligible": True})
    monkeypatch.setattr(W, "require_workspace_quiescent", lambda plan, acknowledgements, observation: {"eligible": True})
    return log


@pytest.mark.parametrize("profile,expected", [
    ("ordinary-terminal-v1", ["observe", "capture"]),
    ("acknowledged-history-v1", ["capture", "observe"]),
])
def test_check_captures_source_once_and_after_observation_for_ordinary(monkeypatch, events, profile, expected):
    monkeypatch.setattr(W, "_PROFILE", profile, raising=False)
    with W.activity_context(_binding(profile), {"digest": "s"}, "/state") as check:
        events.clear()
        check()
        assert events == expected
