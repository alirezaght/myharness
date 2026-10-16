"""Focused reproductions for five normal-hook findings (E218-1..5)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import al_gate_discovery
import al_shell
from hook_regression_helpers import begin, invoke, report_data, save_report


def _start_discovery_run(cli):
    invoke(cli, "start", "--spec", "Execute the repository obligations for each changed path.")
    return json.loads(cli("show")[1])


def test_discovery_uses_the_makefile_service_name(repo, cli):
    service = repo / "services/user-listing-service/src/settings.py"
    service.parent.mkdir(parents=True)
    service.write_text("SETTING = True\n")
    (repo / "Makefile").write_text(
        "lint-imports:\n"
        "\ttest -d services/$(SVC)-service/src\n"
    )
    state = _start_discovery_run(cli)

    manifest = al_gate_discovery.discover(
        state, ["services/user-listing-service/src/settings.py"]
    )
    gate = next(g for g in manifest["gates"] if g["category"] == "lint-imports")
    result = subprocess.run(gate["argv"], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"discovered gate must target the existing service directory: {gate['argv']!r}\n"
        f"{result.stdout}{result.stderr}"
    )


def test_discovery_passes_supported_directories_to_loc_checker(repo, cli):
    source = repo / "services/user-listing-service/src/settings.py"
    source.parent.mkdir(parents=True)
    source.write_text("SETTING = True\n")
    scripts = repo / "scripts"
    scripts.mkdir()
    real_scripts = Path(__file__).resolve().parents[4] / "scripts"
    shutil.copy2(real_scripts / "check-loc.py", scripts / "check-loc.py")
    allowlist = real_scripts / "check-loc.allowlist"
    if allowlist.exists():
        shutil.copy2(allowlist, scripts / allowlist.name)
    state = _start_discovery_run(cli)

    manifest = al_gate_discovery.discover(
        state, ["services/user-listing-service/src/settings.py"]
    )
    gate = next(g for g in manifest["gates"] if g["category"] == "loc")
    result = subprocess.run(gate["argv"], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"discovered LOC gate must use the checker's directory interface: {gate['argv']!r}\n"
        f"{result.stdout}{result.stderr}"
    )


def test_failed_agent_can_take_its_single_guarded_retry(cli, repo, state_file):
    invoke(cli, "start", "--spec", "Retry one transient agent failure when no edits landed.")
    invoke(cli, "agent-failed", "al-engineer", "--reason", "transient runner error",
           "--retry-safe", "yes")

    code, out, err = cli("retry", "al-engineer", "--reason", "retry transient runner error")
    assert code == 0, f"documented retry after agent-failed was refused: {out}{err}"
    state = json.loads(state_file().read_text())
    assert state["status"] == "RUNNING"
    assert state["failure"] is None
    assert state["retries"]["al-engineer"] == 1

    code, out, err = cli("retry", "al-engineer", "--reason", "budget is already spent")
    assert code == 7, f"the single retry budget was not enforced: {out}{err}"
    state = json.loads(state_file().read_text())
    assert state["status"] == "STOPPED_WITH_BLOCKERS"
    assert state["retries"]["al-engineer"] == 1


def test_fresh_blocker_survives_comparison_only_unresolved_resolution(
    cli, repo, tmp_path, state_file
):
    begin(cli, tmp_path, repo)
    finding = {"id": "F-fresh", "source": "AC-01", "severity": "BLOCKING",
               "description": "Fresh review found a blocker that remains unresolved."}
    invoke(cli, "review-start")
    invoke(cli, "review-packet", "--stage", "fresh")
    fresh = report_data(cli, "fresh", "fresh-round-one", findings=[finding])
    fresh_path = tmp_path / "fresh-round-one.json"
    fresh_path.write_text(json.dumps(fresh))
    invoke(cli, "review-fresh", "--file", str(fresh_path))

    plan = tmp_path / "plan.md"
    plan.write_text("Preserve all fresh blockers across correction rounds.\n")
    invoke(cli, "review-compare-start", "--plan", str(plan))
    invoke(cli, "review-packet", "--stage", "compare")
    evidence_id = json.loads(cli("show")[1])["evidence"]["entries"][-1]["id"]
    comparison = report_data(cli, "compare", "compare-round-one", prior_resolutions=[{
        "id": finding["id"], "source": finding["source"], "status": "unresolved",
        "rationale": "The fresh blocker is still present.", "locations": ["svc/app.py:1"],
        "evidence": [evidence_id],
    }])
    comparison["status"] = "CHANGES_REQUIRED"
    compare_path = tmp_path / "compare-round-one.json"
    compare_path.write_text(json.dumps(comparison))
    invoke(cli, "review-result", "--file", str(compare_path))
    assert json.loads(cli("show")[1])["findings"]["blocking"] == 1

    invoke(cli, "correction", "--route", "al-engineer")
    invoke(cli, "phase", "FINALIZATION")
    invoke(cli, "writer", "start", "--id", "fresh-blocker-fix", "--role", "al-engineer",
           "--files", "svc/app.py")
    (repo / "svc/app.py").write_text("fixed source\n")
    invoke(cli, "writer", "finish", "--id", "fresh-blocker-fix", "--outcome", "success")
    invoke(cli, "phase", "VERIFICATION")
    invoke(cli, "evidence", "run", "--area", "unit", "--fresh")

    invoke(cli, "review-start")
    invoke(cli, "review-packet", "--stage", "fresh")
    next_fresh = save_report(cli, tmp_path, "fresh", "fresh-round-two")
    invoke(cli, "review-fresh", "--file", str(next_fresh))
    next_plan = tmp_path / "plan-round-two.md"
    next_plan.write_text("Check the complete candidate after the attempted correction.\n")
    invoke(cli, "review-compare-start", "--plan", str(next_plan))
    invoke(cli, "review-packet", "--stage", "compare")
    next_compare = save_report(cli, tmp_path, "compare", "compare-round-two")
    code, out, err = cli("review-result", "--file", str(next_compare))
    assert code != 0, f"round two dropped the unresolved fresh finding: {out}{err}"
    assert "F-fresh" in str(code) + out + err


def test_git_output_options_are_real_writes_and_blocked_by_shell_policies(repo):
    (repo / "svc/app.py").write_text("changed source\n")
    commands = (
        "git diff --output=svc/diff-equals.txt",
        "git diff --output svc/diff-separated.txt",
        "git log --output=svc/log-equals.txt",
        "git log --output svc/log-separated.txt",
    )
    for command in commands:
        result = subprocess.run(command.split(), cwd=repo, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    for output in ("diff-equals.txt", "diff-separated.txt", "log-equals.txt", "log-separated.txt"):
        written = repo / "svc" / output
        assert written.is_file() and written.stat().st_size > 0

    accepted = [(policy, command) for policy in ("readonly", "shared") for command in commands
                if not al_shell.check(command, policy)]
    assert not accepted, f"output-producing Git options bypassed write protection: {accepted!r}"
