"""The test-session guard catches run state leaked into the real AL registry (#37)."""

import json
import os
import pwd

import pytest

from al.tests import registry_guard as G


def _registry(tmp_path):
    registry = tmp_path / "registry"
    for name in ("runs", "initializations", "harness-runtimes"):
        (registry / "al-run-pins" / name).mkdir(parents=True)
    return registry


def test_the_guarded_registry_is_the_account_home_not_the_test_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = pwd.getpwuid(os.getuid()).pw_dir
    assert G.real_registry() == G.Path(home) / ".local/state/al-distribution/registry"


def test_a_binding_naming_a_temporary_repository_is_a_leak(tmp_path):
    registry = _registry(tmp_path)
    guard = G.RegistryGuard(registry, markers=G.temporary_markers(tmp_path / "pytest-base"))
    leaked = registry / "al-run-pins/runs" / ("b" * 64 + ".json")
    leaked.write_text(json.dumps({"repository_identity": {"root": str(tmp_path / "pytest-base/repo")}}))
    (registry / "al-run-pins/harness-runtimes" / ("a" * 64) / "exec-1").mkdir(parents=True)
    with pytest.warns(UserWarning, match="not attributable"):
        assert guard.finish() == [str(leaked)]
    assert "prune-stale-runs" in G.failure([str(leaked)])


def test_a_concurrent_real_run_only_warns(tmp_path):
    registry = _registry(tmp_path)
    guard = G.RegistryGuard(registry, markers=["/private/var/folders/x/T/"])
    real = registry / "al-run-pins/runs" / ("c" * 64 + ".json")
    real.write_text(json.dumps({"repository_identity": {"root": "/Users/someone/project"}}))
    with pytest.warns(UserWarning, match="concurrent real AL run"):
        assert guard.finish() == []


def test_an_untouched_registry_passes_silently(tmp_path, recwarn):
    registry = _registry(tmp_path)
    guard = G.RegistryGuard(registry, markers=G.temporary_markers())
    assert guard.finish() == [] and not recwarn.list


def test_temporary_markers_cover_resolved_and_given_roots(tmp_path):
    markers = G.temporary_markers(tmp_path)
    assert str(tmp_path).rstrip("/") + "/" in markers
    assert os.path.realpath(tmp_path) + "/" in markers
