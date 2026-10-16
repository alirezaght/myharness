#!/usr/bin/env python3
"""Session id / runtime-directory handling: `start`, `current`, `--session`,
`clear`, `show --spawn`, and the `AL_RUNTIME_DIR` override (A7, A8)."""
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
AL_STATE = SCRIPTS / "al_state.py"
SESSION_ID_RE = re.compile(r"^al-\d{8}T\d{6}Z-[0-9a-f]{6}$")


def test_start_without_session_mints_id_and_writes_current(cli, tmp_path):
    code, out, err = cli("start", "--spec", "Complete the requested change with explicit acceptance criteria and verify its observable behavior.")
    assert code == 0
    current = (tmp_path / "current").read_text().strip()
    assert SESSION_ID_RE.match(current), current
    assert (tmp_path / f"{current}.json").is_file()
    assert f"AL session {current}" in out


def test_show_line_with_no_session_resolves_via_current(cli):
    cli("start", "--spec", "Complete the requested change with explicit acceptance criteria and verify its observable behavior.")
    code, out, err = cli("show", "--line")
    assert code == 0
    assert out.strip()


def test_explicit_session_is_used_verbatim_not_read_from_current(cli, tmp_path):
    cli("start", "--spec", "Complete the first requested change and preserve its immutable run identity and evidence.")  # current now names the minted id
    code, out, err = cli("--session", "custom-1", "start", "--spec", "Complete the second requested change and preserve its immutable run identity and evidence.")
    assert code == 0
    assert (tmp_path / "custom-1.json").is_file()
    assert "AL session custom-1" in out
    # start with an explicit --session still overwrites `current` (A7) — but it
    # never *read* current to choose the id, it used "custom-1" verbatim.
    assert (tmp_path / "current").read_text().strip() == "custom-1"


def test_missing_current_show_line_errors_naming_session_and_start(cli):
    code, out, err = cli("show", "--line")
    assert isinstance(code, str)
    assert "--session" in code
    assert "start" in code


def test_clear_removes_state_file_and_current(cli, tmp_path):
    cli("start", "--spec", "Complete the requested change with explicit acceptance criteria and verify its observable behavior.")
    sid = (tmp_path / "current").read_text().strip()
    code, out, err = cli("clear")
    assert code == 0
    assert not (tmp_path / "current").exists()
    assert (tmp_path / f"{sid}.json").is_file()
    assert cli("--session", sid, "show", "--line")[0] == 0


def test_second_start_mints_a_new_id_and_keeps_the_old_state_file(cli, tmp_path):
    cli("start", "--spec", "Complete the first requested change and preserve its immutable run identity and evidence.")
    first = (tmp_path / "current").read_text().strip()
    cli("start", "--spec", "Complete the second requested change and preserve its immutable run identity and evidence.")
    second = (tmp_path / "current").read_text().strip()
    assert first != second
    assert (tmp_path / f"{first}.json").is_file()
    assert (tmp_path / f"{second}.json").is_file()


def test_invalid_session_chars_are_rejected(cli):
    code, out, err = cli("--session", "///", "start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    assert isinstance(code, str)
    assert "invalid session identity" in code


def test_show_spawn_prints_effective_spawn_args(cli):
    cli("start", "--spec", "Complete the requested change with explicit acceptance criteria and verify its observable behavior.")
    code, out, err = cli("show", "--spawn", "al-builder")
    assert code == 0
    assert out.strip() == (
        "agent_type=al-builder model=gpt-6-luna reasoning_effort=high fork_turns=none"
    )


def test_show_spawn_reflects_a_fallback_switch(cli):
    cli("start", "--spec", "Complete the requested change with explicit acceptance criteria and verify its observable behavior.")
    cli("model-unavailable", "astra", "--reason", "not entitled")
    code, out, err = cli("show", "--spawn", "al-architect")
    assert code == 0
    assert out.strip() == (
        "agent_type=al-architect-fallback model=gpt-6.1-sol "
        "reasoning_effort=high fork_turns=none"
    )


def test_show_spawn_unknown_role_errors(cli):
    cli("start", "--spec", "Complete the requested change with explicit acceptance criteria and verify its observable behavior.")
    code, out, err = cli("show", "--spawn", "al-nonexistent")
    assert isinstance(code, str)
    assert "al-nonexistent" in code


def test_clear_current_leaves_a_current_naming_another_session_untouched(cli, tmp_path):
    cli("--session", "session-a", "start", "--spec", "Complete task A with verified behavior and retained evidence for the current candidate.")
    cli("--session", "session-b", "start", "--spec", "Complete task B with verified behavior and retained evidence for the current candidate.")
    assert (tmp_path / "current").read_text().strip() == "session-b"
    code, out, err = cli("--session", "session-a", "clear")
    assert code == 0
    assert (tmp_path / "session-a.json").is_file()
    assert cli("--session", "session-a", "show", "--line")[0] == 0
    assert (tmp_path / "current").read_text().strip() == "session-b"
    assert (tmp_path / "session-b.json").exists()


def test_al_runtime_dir_env_override_respected():
    """End-to-end: AL_RUNTIME_DIR is read once, at process start, by al_store —
    a genuine subprocess is the only way to prove the override actually moves
    the runtime directory (in-process patching bypasses the env var read)."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        runtime_dir = Path(tmp) / "custom-runtime"
        env = dict(os.environ, AL_RUNTIME_DIR=str(runtime_dir), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            [sys.executable, str(AL_STATE), "start", "--spec", "Verify environment override behavior and retain a complete task package for the run."],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, result.stderr
        assert runtime_dir.is_dir()
        current = (runtime_dir / "current").read_text().strip()
        assert SESSION_ID_RE.match(current)
        assert (runtime_dir / f"{current}.json").is_file()
