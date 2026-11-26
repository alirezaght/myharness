"""Workspace profiler wiring; spies establish argv/provenance, not performance."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import al.tests.workspace_journey as Journey
from al.installer.registry import canonical, digest


def flow(tmp_path, monkeypatch, directory=None):
    def initialize(self, root, platform):
        self.repo = root / "repository"; self.repo.mkdir()
        self.artifacts = root / "artifacts"; self.artifacts.mkdir()
        self.env = {}; self.transcript = []
    monkeypatch.setattr(Journey.Workflow, "__init__", initialize)
    monkeypatch.setattr(Journey, "bootstrap_cli", lambda *args: {})
    return Journey.WorkspaceJourney(tmp_path, "codex", workspace_profile_dir=directory)


def execution(monkeypatch, *, output=True):
    calls = []
    def execute(argv, **kwargs):
        calls.append((argv.copy(), kwargs.copy()))
        kwargs["output"].write(b'{"ok":true}')
        owned = kwargs["directory"]; owned.mkdir()
        (owned / "process-journal.json").write_text(json.dumps({"status":"FINISHED", "cleanup":{"complete":True}}))
        if "cProfile" in argv and output:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"diagnostic-profile")
        return SimpleNamespace(process=SimpleNamespace(returncode=0))
    monkeypatch.setattr(Journey, "execute", execute)
    return calls


@pytest.mark.parametrize("enabled", [False, True])
def test_profile_argv_transcript_and_assignment_agree(tmp_path, monkeypatch, enabled):
    directory = tmp_path / "artifacts/profiles" if enabled else None
    journey = flow(tmp_path, monkeypatch, directory)
    calls = execution(monkeypatch)
    request = tmp_path / "request.json"; request.write_text("{}")
    result = journey.workspace("plan", request)
    argv, args = calls[0]
    assert argv == result.args == journey.transcript[0]["argv"]
    assert args["assignment"] == digest(canonical(argv)) and args["timeout"] == 600
    assert argv[:4] == [Journey.sys.executable, "-I", "-S", "-B"]
    script_index = 8 if enabled else 4
    assert argv[script_index:] == [str(journey.repo / "scripts/install_global.py"), "plan", "--state-root", str(journey.global_state), "--file", str(request), "--sha256", digest(request.read_bytes()), "--source", str(journey.repo)]
    if enabled:
        assert argv[4:7] == ["-m", "cProfile", "-o"]
        profile = Path(argv[7])
        assert profile.parent == directory and profile.stat().st_mode & 0o777 == 0o600
        row = journey.transcript[0]
        assert row["profile"] == str(profile) and row["profile_sha256"] == digest(profile.read_bytes())
        fresh = journey._new_profile()
        assert fresh != profile and profile.read_bytes() == b"diagnostic-profile"
    else:
        assert "profile" not in journey.transcript[0]
    assert json.loads((journey.artifacts / "transcript.json").read_text()) == journey.transcript


@pytest.mark.parametrize("kind", ["outside", "repository", "escape", "symlink", "public"])
def test_unsafe_profile_directory_refuses(tmp_path, monkeypatch, kind):
    directory = tmp_path / "artifacts/profiles"
    if kind == "outside":directory = tmp_path / "outside"
    elif kind == "repository":directory = tmp_path / "repository/profiles"
    elif kind == "escape":directory = tmp_path / "artifacts/../repository/profiles"
    # Create the invalid leaf after fake Workflow prepares its roots.
    journey = flow(tmp_path, monkeypatch)
    if kind == "symlink":
        elsewhere = tmp_path / "elsewhere"; elsewhere.mkdir()
        directory.symlink_to(elsewhere, target_is_directory=True)
    elif kind == "public":directory.mkdir(mode=0o755)
    with pytest.raises(ValueError):journey._profile_directory(directory)


def test_missing_profile_fails_after_positive_settlement(tmp_path, monkeypatch):
    journey = flow(tmp_path, monkeypatch, tmp_path / "artifacts/profiles")
    calls = execution(monkeypatch, output=False)
    request = tmp_path / "request.json"; request.write_text("{}")
    with pytest.raises(AssertionError, match="profile output missing"):
        journey.workspace("verify", request)
    assert calls and (calls[0][1]["directory"] / "process-journal.json").is_file()
