"""The installed dashboard adapter preserves launcher arguments and help purity."""

import os
import subprocess
import sys
from pathlib import Path

from harness.cli import build_parser, main


def test_dashboard_delegates_with_explicit_paths_and_preserves_global_db(monkeypatch):
    received = []
    monkeypatch.setattr("harness.dashboard.launch.main", lambda argv: received.append(argv) or 0)
    assert main(["--db", "global.db", "dashboard", "T1", "--specs", "specs", "--no-browser"]) == 0
    assert received == [["T1", "--db", "global.db", "--specs", "specs", "--no-browser"]]
    assert main(["--db", "global.db", "dashboard", "--db", "explicit.db"]) == 0
    assert received[-1] == ["--db", "explicit.db"]
    assert "redesign" not in build_parser().format_help()


def test_dashboard_help_never_creates_database_or_launches_browser(tmp_path):
    database = tmp_path / "uncreated.db"
    result = subprocess.run(
        [sys.executable, "-m", "harness.cli", "dashboard", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, HARNESS_DB=str(database), PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--no-browser" in result.stdout and "--specs" in result.stdout
    assert "dashboard: http" not in result.stdout and not database.exists()
