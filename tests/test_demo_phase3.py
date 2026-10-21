"""The recorded Phase 3 transcript is reproducible from disposable state."""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/demo_phase3.py"
TRANSCRIPT = ROOT / "docs/demo-phase-3.md"
BLOCK = re.compile(r"<!-- DEMO OUTPUT START -->\n```text\n(.*?)```", re.S)


def test_demo_transcript_matches_real_scenario(tmp_path):
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"HARNESS_DB", "HARNESS_HOME", "AL_RUNTIME_DIR"}
    }
    env.update(HARNESS_HOME=str(tmp_path / "home"), HARNESS_DB=str(tmp_path / "db.sqlite"))
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    match = BLOCK.search(TRANSCRIPT.read_text(encoding="utf-8"))
    assert match is not None, "demo transcript markers are missing"
    assert result.stdout == match.group(1), result.stdout
    assert "simulated; no model request was made" in result.stdout
    assert "DEC-" in result.stdout and "task B completed" in result.stdout
    assert not re.search(r"\bt-[0-9a-f]{8}\b|127\.0\.0\.1:\d", result.stdout)
