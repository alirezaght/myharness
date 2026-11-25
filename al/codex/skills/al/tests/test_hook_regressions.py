"""Focused behavior regressions for the nine rebase hook findings (H166-1..9)."""
import json
import subprocess
import sys

import al_store
import al_artifacts
import al_gate_discovery
import al_store
import al_distribution as distribution
import al_distribution_registry as registry
from hook_regression_helpers import (
    applicable_manifest, begin, finish_review, invoke, save_report,
)


def test_h166_1_changed_candidate_does_not_skip_planning_after_resume(cli, repo, tmp_path, state_file):
    invoke(cli, "start", "--spec", "Keep required planning work after an interrupted candidate changes.")
    payload = tmp_path / "interrupt.json"
    payload.write_text(json.dumps({"reason": "Wait for input", "resume_instructions": "Continue planning."}))
    _, out, _ = invoke(cli, "interrupt", "--file", str(payload))
    (repo / "svc/app.py").write_text("changed after interruption\n")
    invoke(cli, "resume", "--checkpoint", out.strip())
    resumed = json.loads(state_file().read_text())
    assert resumed["phase"] == "PLANNING"
    assert "VERIFICATION" not in resumed["completed_phases"]


def test_h166_2_identical_unmanaged_distribution_file_is_not_claimed_or_deleted(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    (source / "shared.py").write_text("same bytes\n")
    target = tmp_path / "target"; target.mkdir()
    (target / "shared.py").write_text("same bytes\n")
    store = tmp_path / "registry"
    identity = distribution.repo_identity(target)
    instructions = distribution.instructions(target, identity)
    targets = [{"path": str(target), "platform": "codex", "identity": identity, "instructions": instructions}]
    reviews = {str(target): {"reviewed": True, "instructions": instructions}}
    distribution.apply(distribution.plan(source, targets, reviews, registry=store), tmp_path / "backup-one")
    record = json.loads(registry.record_path(store, str(target), "codex").read_text())
    assert "shared.py" not in record["files"]
    (source / "shared.py").unlink()
    distribution.apply(distribution.plan(source, targets, reviews, registry=store), tmp_path / "backup-two")
    assert (target / "shared.py").read_text() == "same bytes\n"


def test_h166_5_real_candidate_gate_observes_task_mode_deletion(cli, repo, tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    git("branch", "-M", "main")
    (repo / "obsolete.txt").write_text("baseline-only file\n")
    git("add", "obsolete.txt"); git("commit", "-m", "add obsolete file")
    git("checkout", "-b", "feature")
    (repo / "obsolete.txt").unlink(); git("add", "-u"); git("commit", "-m", "delete obsolete file")
    task_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    invoke(cli, "start", "--target", "main", "--spec", "Verify task candidate deletions against the actual baseline.")
    state = json.loads(cli("show")[1])
    assert state["baseline_head"] == task_head
    assert state["baseline_head"] != state["identity"]["merge_base"]
    manifest = applicable_manifest(
        state, repo,
        argv=[sys.executable, "-c", "from pathlib import Path; raise SystemExit(0 if not Path('obsolete.txt').exists() else 1)"],
        anticipated_paths=("obsolete.txt",),
    )
    path = tmp_path / "deletion-gates.json"; path.write_text(json.dumps(manifest))
    invoke(cli, "gates", "plan", "--file", str(path))
    invoke(cli, "gates", "run", "--stage", "candidate", "--id", "loc")


def test_h166_7_missing_active_manifest_base_gate_blocks_review_start(cli, repo, tmp_path):
    begin(cli, tmp_path, repo)
    state = json.loads(cli("show")[1])
    manifest = applicable_manifest(state, repo, argv=[sys.executable, "-c", "print('candidate gate passed')"])
    path = tmp_path / "extended-gates.json"; path.write_text(json.dumps(manifest))
    invoke(cli, "gates", "plan", "--file", str(path))
    invoke(cli, "gates", "run", "--stage", "candidate", "--id", "loc")
    before = al_store.state_path(state["session_id"]).read_bytes()
    code, out, err = cli("review-start")
    assert code != 0 and "base gate loc" in str(code) + out + err
    assert al_store.state_path(state["session_id"]).read_bytes() == before


def test_h166_7_approval_rechecks_manifest_extended_after_valid_review(cli, repo, tmp_path):
    begin(cli, tmp_path, repo)
    finish_review(cli, tmp_path, invocation="extended-manifest")
    invoke(cli, "phase", "FINAL_VERIFICATION")
    state = json.loads(cli("show")[1])
    manifest = applicable_manifest(state, repo, argv=[sys.executable, "-c", "print('candidate gate passed')"])
    manifest.update(id="G2", revision=len(state["gate_manifests"]) + 1)
    al_gate_discovery.validate_manifest(state, manifest)
    reference = al_artifacts.snapshot_bytes(state["session_id"], json.dumps(manifest, sort_keys=True).encode(),
                                             "gate-manifest", "simulated manifest extension after valid review")
    state["gate_manifests"].append(reference)
    al_store.save(state["session_id"], state)
    # Model a newly discovered repository obligation arriving after review; load
    # the saved state before approval so the test reaches the independent barrier.
    al_store.load(state["session_id"])
    invoke(cli, "gates", "run", "--stage", "candidate", "--id", "loc")
    code, out, err = cli("approved")
    assert code != 0 and "base gate loc" in str(code) + out + err, f"approval ignored the extended base obligation: {out}{err}"


def test_h166_9_comparison_requires_an_approved_plan_artifact(cli, repo, tmp_path, state_file):
    begin(cli, tmp_path, repo)
    invoke(cli, "review-start")
    invoke(cli, "review-packet", "--stage", "fresh")
    fresh = save_report(cli, tmp_path, "fresh", "missing-plan-fresh")
    invoke(cli, "review-fresh", "--file", str(fresh))
    before = state_file().read_bytes()
    code, out, err = cli("review-compare-start")
    assert code != 0, f"comparison started without a plan: {out}{err}"
    assert state_file().read_bytes() == before, "refusing missing plan must leave review state unchanged"
