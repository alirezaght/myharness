"""Six public-workflow regressions from normal-hook findings E270/E271."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import al_shell
from hook_regression_helpers import (
    CATEGORIES, begin, invoke, manifest_for, report_data,
)
from launcher_context import admit_planning

GUARD = Path(__file__).resolve().parents[1] / "scripts" / "al_readonly_guard.py"


def _source(repo, path):
    return {"path": path, "sha256": hashlib.sha256((repo / path).read_bytes()).hexdigest()}


def _manifest(state, source=None, rule=None, active=None, argv=None):
    import al_store
    manifest = manifest_for(state, al_store.REPO_ROOT, anticipated_paths=("svc/app.py",),
                            category=active, argv=argv, source=source if source else rule)
    if rule:
        current = {entry["path"]: entry for entry in manifest["rules"]}
        current[rule["path"]] = {**rule, "job": rule.get("job")}
        manifest["rules"] = [current[path] for path in sorted(current)]
    return manifest


def _start(cli, tmp_path, repo, classification="SMALL", designer="no", manifest=None):
    invoke(cli, "start", "--spec", "Preserve every gate and review obligation through recovery.")
    invoke(cli, "spec-gate", "SPEC_READY")
    invoke(cli, "classify", classification, "--designer", designer, "--implementers", "al-engineer")
    state = json.loads(cli("show")[1])
    path = tmp_path / "gates.json"; path.write_text(json.dumps(manifest or _manifest(state)))
    invoke(cli, "gates", "plan", "--file", str(path))
    invoke(cli, "gates", "run", "--stage", "base")
    return state


def _to_verification(cli):
    admit_planning(cli)
    invoke(cli, "phase", "IMPLEMENTATION")
    invoke(cli, "writer", "start", "--id", "engineer", "--role", "al-engineer", "--files", "svc/app.py")
    invoke(cli, "writer", "finish", "--id", "engineer", "--outcome", "success")
    invoke(cli, "phase", "VERIFICATION")
    invoke(cli, "evidence", "plan", "--area", "unit", "--tier", "broad", "--kind", "check",
           "--scope", "svc/app.py", "--", sys.executable, "-c", "print('verified')")
    invoke(cli, "evidence", "run", "--area", "unit")


def test_changed_sourced_rule_can_resume_and_replan(cli, repo, tmp_path, state_file):
    makefile = repo / "Makefile"; makefile.write_text("lint-imports:\n\ttrue\n")
    subprocess.run(["git", "add", "Makefile"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                    "commit", "-m", "add sourced rule"], cwd=repo, check=True, capture_output=True)
    rule = _source(repo, "Makefile")
    invoke(cli, "start", "--spec", "Resume conservatively when a sourced repository rule changes.")
    invoke(cli, "spec-gate", "SPEC_READY")
    invoke(cli, "classify", "SMALL", "--designer", "no", "--implementers", "al-engineer")
    state = json.loads(cli("show")[1]); path = tmp_path / "initial-gates.json"
    path.write_text(json.dumps(_manifest(state, rule=rule))); invoke(cli, "gates", "plan", "--file", str(path))
    invoke(cli, "gates", "run", "--stage", "base")
    _to_verification(cli)
    stop = tmp_path / "interrupt.json"
    stop.write_text(json.dumps({"reason": "Wait for rule maintenance", "resume_instructions": "Revalidate gates."}))
    _, checkpoint, _ = invoke(cli, "interrupt", "--file", str(stop))
    makefile.write_text("lint-imports:\n\ttrue # reviewed revision\n")
    invoke(cli, "resume", "--checkpoint", checkpoint.strip())
    resumed = json.loads(state_file().read_text()); assert resumed["status"] == "RUNNING"
    replacement = tmp_path / "replacement-gates.json"
    replacement.write_text(json.dumps(_manifest(resumed, rule=_source(repo, "Makefile"))))
    invoke(cli, "gates", "plan", "--file", str(replacement))


def test_amendment_requires_candidate_gate_to_be_reexecuted(cli, repo, tmp_path, state_file):
    invoke(cli, "start", "--spec", "Revalidate gate evidence after the user amends this task.")
    invoke(cli, "spec-gate", "SPEC_READY"); invoke(cli, "classify", "SMALL", "--designer", "no", "--implementers", "al-engineer")
    current = json.loads(cli("show")[1])
    manifest = _manifest(current, source=_source(repo, "svc/app.py"), active="loc",
                         argv=[sys.executable, "-c", "print('candidate gate passed')"])
    path = tmp_path / "gates.json"; path.write_text(json.dumps(manifest)); invoke(cli, "gates", "plan", "--file", str(path))
    invoke(cli, "gates", "run", "--stage", "base")
    admit_planning(cli)
    invoke(cli, "phase", "IMPLEMENTATION")
    invoke(cli, "writer", "start", "--id", "writer-one", "--role", "al-engineer", "--files", "svc/app.py")
    invoke(cli, "writer", "finish", "--id", "writer-one", "--outcome", "success"); invoke(cli, "phase", "VERIFICATION")
    invoke(cli, "evidence", "plan", "--area", "unit", "--tier", "broad", "--kind", "check",
           "--scope", "svc/app.py", "--", sys.executable, "-c", "print('verified')")
    invoke(cli, "evidence", "run", "--area", "unit"); invoke(cli, "gates", "run", "--stage", "candidate", "--id", "loc")
    amendment = tmp_path / "amendment.json"
    amendment.write_text(json.dumps({"id": "AM-1", "text": "User adds a substantive verification requirement.",
                                     "source": "user", "affected_criteria": ["AC-01"]}))
    invoke(cli, "task", "amend", "--file", str(amendment)); invoke(cli, "spec-gate", "SPEC_READY")
    invoke(cli, "gates", "run", "--stage", "base"); admit_planning(cli);invoke(cli, "phase", "IMPLEMENTATION")
    invoke(cli, "writer", "start", "--id", "writer-two", "--role", "al-engineer", "--files", "svc/app.py")
    invoke(cli, "writer", "finish", "--id", "writer-two", "--outcome", "success"); invoke(cli, "phase", "VERIFICATION")
    invoke(cli, "evidence", "run", "--area", "unit", "--fresh")
    invoke(cli, "gates", "run", "--stage", "candidate", "--id", "imports-root")
    before = state_file().read_bytes()
    code, out, err = cli("review-start")
    assert code != 0, f"amendment-invalidated candidate gate was reused: {out}{err}"
    assert "candidate gate loc requires passing evidence" in str(code) + out + err
    assert state_file().read_bytes() == before
    invoke(cli, "gates", "run", "--stage", "candidate", "--id", "loc")
    invoke(cli, "review-start")


def test_unsatisfied_fresh_criterion_requires_a_same_report_blocker(cli, repo, tmp_path, state_file):
    begin(cli, tmp_path, repo)
    invoke(cli, "review-start"); invoke(cli, "review-packet", "--stage", "fresh")
    fresh = report_data(cli, "fresh", "unsatisfied-fresh")
    fresh["criteria"][0]["status"] = "UNSATISFIED"
    fresh["status"] = "CHANGES_REQUIRED"
    fresh_path = tmp_path / "unsatisfied.json"; fresh_path.write_text(json.dumps(fresh))
    before = state_file().read_bytes()
    code, out, err = cli("review-fresh", "--file", str(fresh_path))
    assert code != 0, f"UNSATISFIED criterion without a linked finding was saved: {out}{err}"
    assert "AC-01" in str(code) + out + err
    assert state_file().read_bytes() == before


def test_second_plan_conflict_needs_its_own_architect_resolution(cli, repo, tmp_path, state_file):
    _start(cli, tmp_path, repo, classification="COMPLEX", designer="yes"); invoke(cli, "phase", "PSEUDOCODE")
    first = tmp_path / "first-resolution.md"; first.write_text("Resolve the first design conflict with the accepted constraint.\n")
    invoke(cli, "phase", "PLAN_CONFLICT")
    invoke(cli, "conflict-resolved", "--file", str(first), "--reason", "First conflict was resolved.")
    invoke(cli, "phase", "PSEUDOCODE"); invoke(cli, "phase", "PLAN_CONFLICT")
    before = state_file().read_bytes(); code, out, err = cli("phase", "PSEUDOCODE")
    assert code != 0, f"a previous conflict resolution bypassed the new occurrence: {out}{err}"
    assert state_file().read_bytes() == before


def test_git_external_diff_side_effect_is_denied_by_both_policies_and_native_guard(cli, repo):
    (repo / "old.txt").write_text("old contents\n"); (repo / "new.txt").write_text("new contents\n")
    command = "git -c diff.external='touch marker; :' diff --no-index --ext-diff old.txt new.txt"
    executed = subprocess.run(["git", "-c", "diff.external=touch marker; :", "diff", "--no-index",
                              "--ext-diff", "old.txt", "new.txt"], cwd=repo, capture_output=True, text=True)
    marker = repo / "marker"
    assert marker.is_file(), f"fixture did not trigger Git's external diff: {executed.stderr}"
    marker.unlink()
    for policy in ("readonly", "shared"):
        assert al_shell.check(command, policy), f"{policy} policy accepted an executable Git config override"
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    guard = subprocess.run([sys.executable, str(GUARD)], input=payload, capture_output=True, text=True)
    response = json.loads(guard.stdout) if guard.stdout else {}
    decision = response.get("hookSpecificOutput", {}).get("permissionDecision")
    assert guard.returncode == 2 and decision == "deny", "the real read-only hook allowed the external write"


def test_fresh_resolution_cannot_clear_historical_blocker(cli, repo, tmp_path, state_file):
    begin(cli, tmp_path, repo)
    finding = {"id": "F-history", "source": "AC-01", "severity": "BLOCKING",
               "description": "An earlier Architect round left this issue unresolved.",
               "resolution": {"status": "fixed", "rationale": "Fresh reports do not own history.",
                              "locations": ["svc/app.py:1"], "evidence": ["E1"]}}
    invoke(cli, "review-start"); invoke(cli, "review-packet", "--stage", "fresh")
    fresh = report_data(cli, "fresh", "history-fresh", findings=[finding])
    fresh["status"] = "CHANGES_REQUIRED"
    fresh_path = tmp_path / "fresh-with-resolution.json"; fresh_path.write_text(json.dumps(fresh))
    before = state_file().read_bytes()
    code, out, err = cli("review-fresh", "--file", str(fresh_path))
    assert code != 0, f"fresh report resolution altered historical blocker ownership: {out}{err}"
    assert state_file().read_bytes() == before
