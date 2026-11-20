"""Packaged cooperative provider entry points are available through the CLI."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("provider", "provider_marker"),
    [("codex", "CODEX_THREAD_ID"), ("claude", "CLAUDE_SESSION_ID")],
)
def test_skill_show_prints_shared_workflow_and_provider_entry(provider, provider_marker):
    result = subprocess.run(
        [sys.executable, "-m", "harness.cli", "skill", "show", "--provider", provider],
        cwd=Path(__file__).resolve().parents[1],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "Foreground delivery loop" in result.stdout
    assert "same task" in result.stdout.lower()
    assert provider_marker in result.stdout
    assert "poll that same" in result.stdout.lower()
    assert "second concurrent wait" in result.stdout.lower()
    assert "do not wrap several" in result.stdout.lower()
    assert "ctrl+x" in result.stdout.lower()
    assert "source_bundle" in result.stdout
    assert "sha256" in result.stdout
    assert "citation" in result.stdout.lower()
    assert "dashboard_command" in result.stdout
    assert "dashboard_origin" in result.stdout.lower()
    assert "workspace.proposed" in result.stdout
    assert "do not acknowledge" in result.stdout.lower()
    assert "/api/dashboard" in result.stdout
    if provider == "claude":
        assert '--claude-session-id "${CLAUDE_SESSION_ID}"' in result.stdout


def test_session_project_reports_canonical_identity_without_database(tmp_path):
    import hashlib
    import json

    environment = os.environ.copy()
    missing_db = tmp_path / "must-not-be-created.db"
    environment["HARNESS_DB"] = str(missing_db)
    result = subprocess.run(
        [sys.executable, "-m", "harness.cli", "session", "project", "--repo", str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )

    expected = hashlib.sha256(
        json.dumps(
            {"repository": str(tmp_path.resolve())},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"project": expected, "repository": str(tmp_path.resolve())}
    assert not missing_db.exists()
