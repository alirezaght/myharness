"""Public installation uses genuine guarded terminal authority, never state patches."""

import json
from pathlib import Path
import subprocess

from al.installer.registry import read_json
from al.tests.workspace_journey import WorkspaceJourney


def test_guarded_terminal_source_installs_and_verifies_both_global_platforms(tmp_path):
    flow = WorkspaceJourney(tmp_path, "codex")
    flow.verification()
    flow.replacement()
    flow.approve()
    assert flow.state()["status"] == "APPROVED" and flow.state()["phase"] == "DONE"
    state = flow.runtime / (flow.session + ".json")
    before_state, before_index = state.read_bytes(), (flow.repo / ".git/index").read_bytes()
    value, manifest = flow.plan()
    assert value["activity"]["profile"] == "ordinary-terminal-v1"
    assert value["target"]["platforms"] == ["codex", "claude"]
    assert "historical" not in value["activity"]
    assert not (flow.global_home / ".local/bin/harness").exists()
    assert json.loads(flow.workspace("apply", manifest).stdout)["status"] == "updated"
    assert json.loads(flow.workspace("verify", manifest).stdout)["status"] == "verified"
    assert (
        state.read_bytes() == before_state
        and (flow.repo / ".git/index").read_bytes() == before_index
    )
    for platform in ("codex", "claude"):
        skill = flow.global_home / ("." + platform) / "skills/al"
        assert (skill / "SKILL.md").is_file() and not (skill / "tests").exists()
    assert (flow.global_home / ".codex/skills/harness/SKILL.md").is_file()
    assert (flow.global_home / ".claude/commands/harness.md").is_file()
    neutral = tmp_path / "neutral"
    neutral.mkdir()
    for argv in (
        ["harness", "--version"],
        ["harness", "--help"],
        ["harness-redesign", "--help"],
        ["alctl", "list", "--state-root", str(flow.global_state), "--json"],
        ["alctl", "doctor", "--state-root", str(flow.global_state), "--json"],
    ):
        result = subprocess.run(
            [str(flow.global_home / ".local/bin" / argv[0]), *argv[1:]],
            cwd=neutral,
            env=flow.env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (argv, result.stdout[-1500:], result.stderr[-1500:])
    records = list((flow.global_state / "installer/workspace-records").glob("*.json"))
    assert len(records) == 1
    receipt = read_json(records[0])["current"]
    assert receipt["plan_digest"] == value["digest"]
    journal = read_json(Path(receipt["journal"]))
    assert journal["status"] == "COMPLETE"
    assert all(action["status"] == "APPLIED" for action in journal["ancillary_actions"])
