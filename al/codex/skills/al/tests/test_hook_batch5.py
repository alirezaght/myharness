"""Focused reproductions for five normal-hook findings (E218-1..5)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import al_gate_discovery
from test_service_coverage import unit
from test_repo_adapters import contract
import al_shell
from hook_regression_helpers import begin, invoke, report_data, save_report


def _start_discovery_run(cli):
    invoke(cli, "start", "--spec", "Execute the repository obligations for each changed path.")
    return json.loads(cli("show")[1])


def test_discovery_uses_the_full_service_name_with_real_architecture_check(repo, cli):
    unit(repo,'services/user-listing-service','SETTING = True\n')
    state = _start_discovery_run(cli)
    manifest = al_gate_discovery.discover(state, ['services/user-listing-service/src/app.py'])
    gate = next(g for g in manifest['gates'] if g['id']=='imports-services-user-listing-service')
    assert gate['argv'][-2:]==['--unit','services/user-listing-service']
    assert __import__('al_repository_facts').all_within(gate['scope'],['services/user-listing-service','services/user-listing-service/pyproject.toml'])
    assert subprocess.run(gate['argv'],cwd=repo,capture_output=True).returncode==0
    (repo/'services/user-listing-service/src/app.py').write_text('import forbidden_dependency\n')
    assert subprocess.run(gate['argv'],cwd=repo,capture_output=True).returncode!=0


def test_contract_discovery_binds_full_service_to_declared_chain_without_execution(repo, cli):
    contract(repo)
    unit(repo,'services/user-listing-service',extra='[tool.importlinter]\nroot_packages=["app"]\n')
    state = _start_discovery_run(cli)
    manifest = al_gate_discovery.discover(state, ['services/user-listing-service/src/app.py'])
    gate = next(g for g in manifest['gates'] if g['id']=='imports-services-user-listing-service')
    assert gate['argv']==['make','lint-imports','SVC=user-listing']
    chain={'.al/repository.json','Makefile','ci/scripts/check-architecture.sh','services/user-listing-service/pyproject.toml'}
    assert __import__('al_repository_facts').all_within(gate['scope'],chain)
    expected=next(b for b in manifest['service_coverage']['assessment']['required_bindings'] if b['id']==gate['id'])
    assert chain<={row['path'] for row in expected['source_chain']}
    assert gate['execution_signature']==expected['execution_signature']
    # The declared chain is hash-bound: editing a chain file changes the execution signature.
    (repo/'ci/scripts/check-architecture.sh').write_text('#!/bin/bash\ntrue\n')
    again = al_gate_discovery.discover(state, ['services/user-listing-service/src/app.py'])
    changed = next(g for g in again['gates'] if g['id']==gate['id'])
    assert changed['execution_signature']!=gate['execution_signature']


def test_discovery_passes_supported_directories_to_loc_checker(repo, cli):
    source = repo / "services/user-listing-service/src/settings.py"
    source.parent.mkdir(parents=True)
    source.write_text("SETTING = True\n")
    scripts = repo / "scripts"
    scripts.mkdir()
    fixture = Path(__file__).parent / "fixtures" / "repository" / "scripts"
    shutil.copy2(fixture / "check-loc.py", scripts / "check-loc.py")
    shutil.copy2(fixture / "check-loc.allowlist", scripts / "check-loc.allowlist")
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
    (repo / "svc/app.py").write_text("fixed_source = True\n")
    invoke(cli, "writer", "finish", "--id", "fresh-blocker-fix", "--outcome", "success")
    invoke(cli, "phase", "VERIFICATION")
    invoke(cli, "gates", "run", "--stage", "candidate")
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
