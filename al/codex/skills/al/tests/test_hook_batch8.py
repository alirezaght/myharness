"""Public regressions for the two final normal-hook inventory findings."""
import hashlib
import json
import subprocess

from al_artifacts import read_json
from hook_regression_helpers import invoke
from test_hook_batch7 import _planning


def test_uv_separator_cannot_hide_git_reset_from_shared_ownership(cli):
    command = "uv run -- git reset --hard"
    invoke(cli, "start", "--spec", "Reject package wrappers that conceal shared Git mutations.")
    code, out, err = cli("ownership", "--engineer", "svc/app.py", "--builder", "docs/notes.md",
                         "--engineer-commands", command)
    assert code != 0, f"ownership admitted a wrapped Git mutation: {out}{err}"


def test_wrapped_harmless_search_remains_allowed(cli):
    command = "uv run -- rg 'git reset --hard' ."
    invoke(cli, "start", "--spec", "Keep read-only wrapped searches available to parallel owners.")
    invoke(cli, "ownership", "--engineer", "svc/app.py", "--builder", "docs/notes.md",
           "--engineer-commands", command)


def _seed_two_unit_ci_repo(repo):
    pipeline = repo / ".gitlab-ci.yml"
    pipeline.write_text("web-test:\n  script: python3 -m json.tool apps/web/package.json\n  rules:\n    - changes: [apps/web/**/*]\nadmin-test:\n  script: python3 -m json.tool apps/admin/package.json\n  rules:\n    - changes: [apps/admin/**/*]\n")
    for name in ("web", "admin"):
        unit = repo / "apps" / name
        (unit / "src").mkdir(parents=True, exist_ok=True)
        (unit / "package.json").write_text(json.dumps({"name": name}) + "\n")
        (unit / "src/app.ts").write_text("export const changed = true;\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                    "commit", "-m", "add sourced CI jobs and bounded applications"], cwd=repo, check=True, capture_output=True)


def _discovered_ci_manifest(cli, repo, tmp_path):
    _seed_two_unit_ci_repo(repo)
    _planning(cli)
    paths = ["apps/web/src/app.ts", "apps/admin/src/app.ts"]
    paths_file = tmp_path / "paths.json"
    paths_file.write_text(json.dumps(paths))
    invoke(cli, "gates", "discover", "--paths-file", str(paths_file))
    state = json.loads(cli("show")[1])
    ref = state["gate_manifests"][-1]
    manifest = read_json(ref)
    source = {"path": ".gitlab-ci.yml", "sha256": hashlib.sha256((repo / ".gitlab-ci.yml").read_bytes()).hexdigest()}
    return manifest, source


def _replace_ci(manifest, gates):
    manifest["gates"] = [gate for gate in manifest["gates"] if gate["category"] != "ci"] + gates


def test_ci_manifest_must_cover_each_touched_application_unit(cli, repo, tmp_path, state_file):
    manifest, source = _discovered_ci_manifest(cli, repo, tmp_path)
    _replace_ci(manifest, [gate for gate in manifest["gates"] if gate["category"] == "ci" and "apps/web" in gate["units"]])
    plan = tmp_path / "web-only-ci.json"
    plan.write_text(json.dumps(manifest))
    before = state_file().read_bytes()
    code, out, err = cli("gates", "plan", "--file", str(plan))
    assert code != 0, f"manifest omitted the admin CI obligation but was admitted: {out}{err}"
    assert state_file().read_bytes() == before


def test_ci_manifest_accepts_explicit_coverage_for_both_touched_units(cli, repo, tmp_path):
    manifest, source = _discovered_ci_manifest(cli, repo, tmp_path)
    assert all(gate.get("execution_signature") for gate in manifest["gates"] if gate["category"] == "ci")
    plan = tmp_path / "web-and-admin-ci.json"
    plan.write_text(json.dumps(manifest))
    invoke(cli, "gates", "plan", "--file", str(plan))
