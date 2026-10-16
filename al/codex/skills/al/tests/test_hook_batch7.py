"""Public regressions for the seven post-rework hook findings."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from al_artifacts import read_json
from hook_regression_helpers import begin, invoke, report_data
from test_hook_batch6 import _manifest, _source

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _copy_project(repo, paths):
    for rel in paths:
        source = PROJECT_ROOT / rel
        if not source.is_file():
            continue
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _commit(repo, message):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=diagnostic", "-c", "user.email=diagnostic@example.invalid",
                    "commit", "-m", message], cwd=repo, check=True, capture_output=True)


def _planning(cli):
    invoke(cli, "start", "--spec", "Verify the exact sourced repository obligations.")
    invoke(cli, "spec-gate", "SPEC_READY")
    invoke(cli, "classify", "SMALL", "--designer", "no", "--implementers", "al-engineer")


def test_declared_environment_change_invalidates_candidate_gate(cli, repo, tmp_path, monkeypatch):
    begin(cli, tmp_path, repo)
    state = json.loads(cli("show")[1])
    command = [sys.executable, "-c", "import os; assert os.environ['AL_DIAGNOSTIC_VARIANT']=='before'"]
    manifest = _manifest(state, source=_source(repo, "svc/app.py"), active="loc", argv=command)
    manifest["gates"][1]["env_names"] = ["AL_DIAGNOSTIC_VARIANT"]
    path = tmp_path / "env-gates.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setenv("AL_DIAGNOSTIC_VARIANT", "before")
    invoke(cli, "gates", "plan", "--file", str(path))
    invoke(cli, "gates", "run", "--stage", "base", "--id", "loc")
    invoke(cli, "gates", "run", "--stage", "candidate", "--id", "loc")
    monkeypatch.setenv("AL_DIAGNOSTIC_VARIANT", "after")
    code, out, err = cli("gates", "status")
    assert code == 0, f"gate currency query failed: {out}{err}"
    status = json.loads(out)
    assert status["candidate"], (
        "candidate evidence remained current after its declared environment changed: "
        f"{status}"
    )


def test_fresh_primary_review_survives_recorded_fallback(cli, tmp_path, repo):
    begin(cli, tmp_path, repo)
    invoke(cli, "review-start")
    invoke(cli, "review-packet", "--stage", "fresh")
    fresh = tmp_path / "fresh-primary.json"
    fresh.write_text(json.dumps(report_data(cli, "fresh", "diagnostic-fresh-primary")))
    invoke(cli, "review-fresh", "--file", str(fresh))
    before = json.loads(cli("show")[1])
    primary_model = before["model"]
    assert before["model_tier"]["al-architect"] == "primary"
    invoke(cli, "model-unavailable", primary_model,
           "--reason", "diagnostic simulation: primary unavailable before comparison")
    after = json.loads(cli("show")[1])
    assert after["model_tier"]["al-architect"] == "fallback" and after["model"] != primary_model
    plan = tmp_path / "comparison-plan.md"
    plan.write_text("The separately completed fresh review remains bound to its original invocation.\n")
    invoke(cli, "review-compare-start", "--plan", str(plan))
    invoke(cli, "review-packet", "--stage", "compare")
    comparison = tmp_path / "comparison-fallback.json"
    comparison.write_text(json.dumps(report_data(cli, "compare", "diagnostic-compare-fallback")))
    invoke(cli, "review-result", "--file", str(comparison))
    code, out, err = cli("phase", "FINAL_VERIFICATION")
    assert code == 0, f"valid primary fresh provenance was rejected after fallback: {out}{err}"
    code, out, err = cli("approved")
    assert code == 0, f"approval rejected valid stage-bound reports: {out}{err}"


def test_repository_discovery_cannot_be_erased_by_empty_rules_and_na(cli, repo, tmp_path, state_file):
    _copy_project(repo, ["AGENTS.md", "Makefile", "package.json", ".husky/pre-commit",
                         ".githooks/pre-commit", ".gitlab-ci.yml", "scripts/check-loc.py",
                         "scripts/check-loc.allowlist", "services/academy-service/AGENTS.md"])
    app = repo / "services/academy-service/src/academy_service/app.py"
    app.parent.mkdir(parents=True, exist_ok=True)
    app.write_text("from pyshared.context import request_id_var\n\nVALUE = request_id_var\n")
    _commit(repo, "seed sourced service gate obligation")
    _planning(cli)
    paths = tmp_path / "service-paths.json"
    paths.write_text(json.dumps(["services/academy-service/src/academy_service/app.py"]))
    invoke(cli, "gates", "discover", "--paths-file", str(paths))
    discovered = read_json(json.loads(cli("show")[1])["gate_manifests"][-1])
    assert any(g["category"] == "lint-imports" and g["applicability"] == "APPLICABLE"
               for g in discovered["gates"])
    forged = json.loads(json.dumps(discovered))
    forged["rules"] = []
    for gate in forged["gates"]:
        gate.update(applicability="NOT_APPLICABLE", required=False,
                    reason="No obligation", argv=[], source=None)
    path = tmp_path / "erased-gates.json"
    path.write_text(json.dumps(forged))
    before = state_file().read_bytes()
    code, out, err = cli("gates", "plan", "--file", str(path))
    assert code != 0, f"empty rules erased independently discovered obligations: {out}{err}"
    assert state_file().read_bytes() == before


def test_shared_pyshared_change_selects_actual_importing_service(cli, repo, tmp_path):
    _copy_project(repo, ["AGENTS.md", "Makefile", "package.json", "packages/AGENTS.md",
                         "docs/runbooks/codex-review-hooks.md", "packages/pyshared/src/pyshared/context.py",
                         "services/shortlist-service/AGENTS.md",
                         "services/shortlist-service/src/shortlist_service/infrastructure/upstream.py"])
    policy = (repo / "docs/runbooks/codex-review-hooks.md").read_text()
    consumer = (repo / "services/shortlist-service/src/shortlist_service/infrastructure/upstream.py").read_text()
    assert "Shared package changes select affected service graphs." in policy
    assert "from pyshared.context import request_id_var" in consumer
    shared = repo / "packages/pyshared/src/pyshared/context.py"
    shared.write_text(shared.read_text() + "\n# diagnostic shared-source change\n")
    _planning(cli)
    paths = tmp_path / "shared-paths.json"
    paths.write_text(json.dumps(["packages/pyshared/src/pyshared/context.py"]))
    invoke(cli, "gates", "discover", "--paths-file", str(paths))
    ref = json.loads(cli("show")[1])["gate_manifests"][-1]
    gates = [g for g in read_json(ref)["gates"] if g["category"] == "lint-imports"]
    assert any(g["id"] == "imports-shortlist" and g["applicability"] == "APPLICABLE" for g in gates), (
        f"shared pyshared edit omitted the known shortlist import obligation: {gates}"
    )


@pytest.mark.parametrize("command,peer_path", [
    ("pnpm format", "docs/peer.md"),
    ("pnpm generate:contracts", "packages/contracts/python/acme_contracts/order_events.openapi.py"),
])
def test_repository_write_scripts_are_not_parallel_owner_commands(cli, repo, tmp_path, command, peer_path):
    _copy_project(repo, ["package.json", "packages/contracts/generate.sh"])
    engineer_path = "services/academy-service/src/owned.py"
    engineer = repo / engineer_path
    engineer.parent.mkdir(parents=True, exist_ok=True)
    engineer.write_text("VALUE = 1\n")
    peer = repo / peer_path
    peer.parent.mkdir(parents=True, exist_ok=True)
    if command == "pnpm format":
        peer.write_text("# peer\ntext  \n")
    else:
        peer.write_text("generated file sentinel\n")
    _commit(repo, "seed actual package write scripts and disjoint owners")
    invoke(cli, "start", "--spec", "Reject repository-wide writes during parallel ownership.")
    code, out, err = cli("ownership", "--engineer", engineer_path, "--builder", peer_path,
                         "--engineer-commands", command)
    if code == 0:
        env = {**os.environ, "PATH": str(PROJECT_ROOT / "node_modules/.bin") + os.pathsep + os.environ["PATH"]}
        if command.endswith("generate:contracts"):
            fakebin = tmp_path / "fakebin"
            fakebin.mkdir()
            uv = fakebin / "uv"
            uv.write_text("#!/bin/sh\nexit 0\n")
            uv.chmod(0o755)
            env["PATH"] = str(fakebin) + os.pathsep + env["PATH"]
        before = peer.read_bytes()
        executed = subprocess.run(command.split(), cwd=repo, env=env, capture_output=True, text=True, timeout=60)
        if command == "pnpm format":
            assert peer.read_bytes() != before, f"copied actual format script did not mutate peer fixture: {executed.stderr}"
        else:
            assert not peer.exists(), f"copied actual generate script did not delete peer sentinel: {executed.stderr}"
    assert code != 0, f"ownership admitted a real repository-wide writer: {command}: {out}{err}"

