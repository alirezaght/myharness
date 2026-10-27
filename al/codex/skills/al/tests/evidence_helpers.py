"""Shared fixtures for the evidence-ledger test modules."""
import json
import sys
from pathlib import Path
from hook_regression_helpers import manifest_for
from launcher_context import admit_planning, observe_behavior

PY = sys.executable
SAME_SESSION = "al-test"

def ok_cmd(text, marker=None):
    body = f"print({text!r})"
    if marker:
        body = f"open({str(marker)!r}, 'a').write('x'); " + body
    return [PY, "-c", body]

def entries(state_file):
    return json.loads(state_file().read_text())["evidence"]["entries"]

def plan_both(cli):
    cli("evidence", "plan", "--area", "svc", "--scope", "svc", "--", *ok_cmd("12 passed in 0.3s"))
    cli("evidence", "plan", "--area", "web", "--scope", "web", "--", *ok_cmd("Tests  4 passed (4)"))

def wait_running(state_file, area, timeout=10, process=None):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if any(entry["area"] == area and entry["status"] == "RUNNING" and entry.get("pid")
                   for entry in entries(state_file)):
                return
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.05)
    detail = ""
    if process is not None:
        try:
            process.wait(timeout=0.2)
            stdout, stderr = process.communicate(timeout=0.2)
            detail = f"; child exited {process.returncode}: {stdout}{stderr}"
        except Exception:
            detail = f"; child still running (pid={process.pid})"
    raise AssertionError(f"{area} never started{detail}")

def slow_check(release):
    return [PY, "-c", f"import os,time\nwhile not os.path.exists({str(release)!r}): time.sleep(0.05)\nprint('2 passed')"]

def git(repo, *args):
    import subprocess
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo,
                   check=True, capture_output=True)


def start_implementation(cli, tmp_path, anticipated_paths=("svc/app.py", "svc/fixed")):
    """Enter a supported STANDARD implementation route with sourced N/A gates."""
    for args in (
        ("start", "--spec", "Verify a scoped implementation and preserve its repository evidence."),
        ("spec-gate", "SPEC_READY"),
        ("classify", "STANDARD", "--designer", "no", "--implementers", "al-engineer"),
    ):
        result, out, err = cli(*args)
        assert result == 0, f"{args[0]} failed: {result} {out}{err}"
    plan_na_gates(cli, tmp_path, anticipated_paths)
    admit_planning(cli)
    result, out, err = cli("phase", "IMPLEMENTATION")
    assert result == 0, f"implementation admission failed: {result} {out}{err}"


def plan_na_gates(cli, tmp_path, anticipated_paths=("svc/app.py", "svc/fixed")):
    import json
    state = json.loads(cli("show")[1])
    manifest = manifest_for(state, Path.cwd(), anticipated_paths)
    gates = tmp_path / "gates.json"
    gates.write_text(json.dumps(manifest))
    result, out, err = cli("gates", "plan", "--file", str(gates))
    assert result == 0, f"gate plan failed: {result} {out}{err}"
    result, out, err = cli("gates", "run", "--stage", "base")
    assert result == 0, f"required base gates failed: {result} {out}{err}"


def plan_and_run_broad(cli, area="suite"):
    cli("evidence", "plan", "--area", area, "--tier", "broad", "--kind", "check", "--scope", "svc",
        "--", PY, "-m", "compileall", "-q", "svc")
    return cli("evidence", "run", "--area", area)[0]


def _review_report(cli, stage, invocation, blocking):
    state = json.loads(cli("show")[1])
    review = state["review"]
    packet = review["packets"][stage]
    evidence_id = state["evidence"]["entries"][-1]["id"]
    prior_resolutions = ([{"id": "F-1", "source": "AC-01", "status": "fixed",
                           "rationale": "The correction updated the cited implementation and passed verification.",
                           "locations": ["svc/app.py:1"], "evidence": [evidence_id]}]
                        if stage == "compare" and any(item.get("blocking", 0) for item in state["review_history"]) else [])
    findings = ([{"id": "F-1", "source": "AC-01", "severity": "BLOCKING",
                  "description": "The verified fixture exposes one required correction."}]
                if blocking else [])
    inspected = [packet["sha256"]]
    if stage == "compare":
        inputs = review["comparison_inputs"]
        inspected.extend(inputs[name]["sha256"] for name in ("plan", "pseudocode") if name in inputs)
    if "proof_admission" in state:
        return _review_report_v2(state, stage, invocation, findings, prior_resolutions, inspected, evidence_id)
    return {
        "schema_version": 1, "review_id": review["id"], "stage": stage,
        "reviewer_role": "al-architect", "model": state["model"],
        "invocation_id": invocation, "candidate_hash": review["candidate"]["fingerprint"],
        "task_hash": review["task_hash"], "packet_hash": packet["sha256"],
        "inspected_artifacts": inspected,
        "criteria": [{"id": "AC-01", "status": "SATISFIED", "rationale": "The public workflow was exercised.",
                      "locations": ["svc/app.py:1"], "evidence": [evidence_id]}],
        "findings": findings, "prior_resolutions": prior_resolutions, "plan_defects": [],
        "status": "CHANGES_REQUIRED" if blocking else "APPROVED",
        "assessments": {"rollout": {"applicable": False, "reason": "No deployment surface."},
                        "ui": {"applicable": False, "reason": "No rendered interface."}},
    }


def _review_report_v2(state, stage, invocation, findings, prior_resolutions, inspected, evidence_id):
    """Closed version-two report bound to the issued packet of a new-origin run."""
    issued = json.loads(Path(state["review"]["packets"][stage]["stored_path"]).read_bytes())
    value = issued.get("content", issued)
    attempt = value["attempt"]
    criteria = [c["id"] for c in value["behavior"]["obligations"]["content"]["criteria_registry"]]
    for finding in findings:
        finding["source"] = criteria[0]
    for resolution in prior_resolutions:
        resolution["source"] = criteria[0]
    report = {
        "schema_version": 2, "kind": "architect-review-report-v2", "review_id": value["review_id"],
        "stage": stage, "reviewer_role": "al-architect", "model": attempt["model"],
        "invocation_id": invocation, "attempt_id": attempt["attempt_id"], "effort": attempt["effort"],
        "policy_digest": attempt["policy_digest"], "candidate_hash": value["candidate_hash"],
        "task_hash": value["task_hash"], "document_hash": value["document_hash"],
        "verification_authority_hash": value["verification_authority"]["hash"],
        "packet_hash": state["review"]["packets"][stage]["sha256"], "inspected_artifacts": inspected,
        "criteria": [{"id": c, "status": "SATISFIED", "rationale": "The public workflow was exercised.",
                      "locations": ["svc/app.py:1"], "evidence": [evidence_id]} for c in criteria],
        "findings": findings, "status": "CHANGES_REQUIRED" if findings else "APPROVED",
    }
    if stage == "fresh":
        report["counterexample_search"] = [{
            "obligation_ids": [g["id"] for g in value["behavior"]["obligations"]["content"]["obligations"]],
            "scenario": "A candidate that skipped the exercised workflow would contradict the declared outcome.",
            "inspected_locations": ["svc/app.py:1"], "evidence": [evidence_id], "result": "none_found",
            "rationale": "The linked verification exercised the cited source in the frozen candidate."}]
    else:
        report.update(prior_resolutions=prior_resolutions, plan_defects=[], assessments={
            "rollout": {"applicable": False, "reason": "No deployment surface."},
            "ui": {"applicable": False, "reason": "No rendered interface."}})
    return report


def complete_review(cli, tmp_path, blocking=True, inspect_report=None):
    state = json.loads(cli("show")[1])
    if any(item.get("blocking", 0) for item in state["review_history"]):
        source = Path.cwd() / "svc/app.py"
        source.write_text(f"x = {len(state['review_history']) + 1}\n")
        result, out, err = cli("evidence", "run", "--area", "suite", "--fresh")
        assert result == 0, f"corrected candidate verification failed: {result} {out}{err}"
    result, out, err = cli("gates", "run", "--stage", "candidate")
    assert result == 0, f"required candidate gates failed: {result} {out}{err}"
    observe_behavior(cli, directory=tmp_path)
    result, out, err = cli("review-start")
    assert result == 0, f"review-start failed with {result}: {out}{err}"
    review_id = json.loads(cli("show")[1])["review"]["id"]
    result, out, err = cli("review-packet", "--stage", "fresh")
    assert result == 0, f"fresh packet failed: {result} {out}{err}"
    fresh = tmp_path / "fresh-review.json"
    fresh_report=_review_report(cli, "fresh", f"{review_id}-fresh", False)
    if inspect_report:inspect_report(fresh_report)
    fresh.write_text(json.dumps(fresh_report))
    result, out, err = cli("review-fresh", "--file", str(fresh))
    assert result == 0, f"fresh report failed: {result} {out}{err}"
    plan = tmp_path / "approved-plan.md"
    plan.write_text("The reviewed plan covers the accepted criterion and required failure paths.\n")
    args = ["review-compare-start", "--plan", str(plan)]
    if state.get("designer_required"):
        pseudocode = tmp_path / "approved-pseudocode.md"
        pseudocode.write_text("The approved pseudocode covers the required transformation and failure paths.\n")
        args.extend(["--pseudocode", str(pseudocode)])
    result, out, err = cli(*args)
    assert result == 0, f"comparison start failed ({result!r}): {out}{err}"
    result, out, err = cli("review-packet", "--stage", "compare")
    assert result == 0, f"comparison packet failed: {result} {out}{err}"
    comparison = tmp_path / "comparison-review.json"
    compare_report=_review_report(cli, "compare", f"{review_id}-compare", blocking)
    if inspect_report:inspect_report(compare_report)
    comparison.write_text(json.dumps(compare_report))
    result, out, err = cli("review-result", "--file", str(comparison))
    assert result == 0, f"comparison report failed: {result} {out}{err}"
