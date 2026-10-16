"""Shared fixtures for the evidence-ledger test modules."""
import json
import sys
from pathlib import Path
from hook_regression_helpers import manifest_for

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
    cli("start", "--spec", "Verify a scoped implementation and preserve its repository evidence.")
    cli("spec-gate", "SPEC_READY")
    cli("classify", "STANDARD", "--designer", "no", "--implementers", "al-engineer")
    plan_na_gates(cli, tmp_path, anticipated_paths)
    cli("phase", "IMPLEMENTATION")


def plan_na_gates(cli, tmp_path, anticipated_paths=("svc/app.py", "svc/fixed")):
    import json
    state = json.loads(cli("show")[1])
    manifest = manifest_for(state, Path.cwd(), anticipated_paths)
    gates = tmp_path / "gates.json"
    gates.write_text(json.dumps(manifest))
    cli("gates", "plan", "--file", str(gates))


def plan_and_run_broad(cli, area="suite"):
    cli("evidence", "plan", "--area", area, "--tier", "broad", "--scope", "svc",
        "--", PY, "-c", "print('1 passed')")
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


def complete_review(cli, tmp_path, blocking=True):
    state = json.loads(cli("show")[1])
    if any(item.get("blocking", 0) for item in state["review_history"]):
        source = Path.cwd() / "svc/app.py"
        source.write_text(f"x = {len(state['review_history']) + 1}\n")
        result, out, err = cli("evidence", "run", "--area", "suite", "--fresh")
        assert result == 0, f"corrected candidate verification failed: {out}{err}"
    result, out, err = cli("review-start")
    assert result == 0, f"review-start failed with {result}: {out}{err}"
    review_id = json.loads(cli("show")[1])["review"]["id"]
    result, out, err = cli("review-packet", "--stage", "fresh")
    assert result == 0, f"fresh packet failed: {out}{err}"
    fresh = tmp_path / "fresh-review.json"
    fresh.write_text(json.dumps(_review_report(cli, "fresh", f"{review_id}-fresh", False)))
    result, out, err = cli("review-fresh", "--file", str(fresh))
    assert result == 0, f"fresh report failed: {out}{err}"
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
    assert result == 0, f"comparison packet failed: {out}{err}"
    comparison = tmp_path / "comparison-review.json"
    comparison.write_text(json.dumps(_review_report(cli, "compare", f"{review_id}-compare", blocking)))
    result, out, err = cli("review-result", "--file", str(comparison))
    assert result == 0, f"comparison report failed: {out}{err}"
