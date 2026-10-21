"""Fixtures for current-CLI regressions from the rebased hook review."""
import json
import hashlib
import sys
from pathlib import Path
import al_gate_inventory as gate_inventory
import al_service_coverage as Coverage
import al_gate_discovery as Discovery


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
CATEGORIES = ("lint-imports", "loc", "contracts", "pre-commit", "ci")


def manifest_for(state, repo, anticipated_paths=(), category=None, argv=None, source=None):
    inventory = gate_inventory.inventory(state, anticipated_paths)
    paths, units, rules = inventory["paths"], inventory["units"], inventory["rules"]
    root = Path(repo)
    known = {
        "lint-imports": bool(inventory["services"]) or inventory["service_coverage"]["outcome"]=="incomplete",
        "loc": (root / "scripts/check-loc.py").is_file(),
        "pre-commit": (root / ".husky/pre-commit").is_file() or (root / ".githooks/pre-commit").is_file(),
        "contracts": False,
        "ci": bool(inventory["ci"]["obligations"]),
    }
    for package in [root / "package.json", *(root / unit / "package.json" for unit in units)]:
        if not package.is_file():
            continue
        try:
            scripts = json.loads(package.read_text()).get("scripts", {})
        except (ValueError, AttributeError):
            known["contracts"] = True
            continue
        known["contracts"] = known["contracts"] or any(
            "contract" in name and ("gen" in name or "build" in name) for name in scripts
        )
    fallback_source = rules[0] if rules else None
    gates = []
    for name in CATEGORIES:
        active = name == category
        gates.append({
            "id": name, "category": name,
            "applicability": "APPLICABLE" if active else "UNRESOLVED" if known[name] else "NOT_APPLICABLE",
            "required": active or known[name],
            "reason": "The test exercises this sourced gate." if active else
                      "Resolve the known repository obligation." if known[name] else
                      "No obligation exists in this disposable repository.",
            "units": units, "argv": argv if active and argv else [], "scope": paths,
            "source": source if active and source else fallback_source,
            "execution_kind": "local", "cwd": ".",
        })
    discovered=Discovery.discover(state,list(anticipated_paths))
    if category!='lint-imports':
        gates=[g for g in gates if g['category']!='lint-imports']+[g for g in discovered['gates'] if g['category']=='lint-imports']
    if category!='ci' and inventory['ci']['obligations']:
        gates=[g for g in gates if g['category']!='ci']+[g for g in discovered['gates'] if g['category']=='ci']
    coverage=Coverage.declaration(inventory['repository_facts'],inventory['service_coverage'],gates)
    return {"service_coverage":coverage,"schema_version": 1, "base_oid": state["identity"]["merge_base"],
            "units": units, "paths": paths, "rules": rules, "gates": gates}


def invoke(cli, *args, ok=True):
    code, out, err = cli(*args)
    if ok:
        assert code == 0, f"{args!r} failed ({code}): {out}{err}"
    else:
        assert code != 0, f"{args!r} unexpectedly succeeded: {out}{err}"
    return code, out, err


def begin(cli, tmp_path, repo, spec="Verify the guarded candidate before approval.", target=None, anticipated_paths=()):
    args = ["start"]
    if target:
        args.extend(["--target", target])
    args.extend(["--spec", spec])
    invoke(cli, *args)
    invoke(cli, "spec-gate", "SPEC_READY")
    invoke(cli, "classify", "SMALL", "--designer", "no", "--implementers", "al-engineer")
    state = json.loads(cli("show")[1])
    manifest = manifest_for(state, repo, anticipated_paths=("svc/app.py", *anticipated_paths))
    path = tmp_path / "gates.json"
    path.write_text(json.dumps(manifest))
    invoke(cli, "gates", "plan", "--file", str(path))
    invoke(cli, "gates", "run", "--stage", "base")
    invoke(cli, "phase", "IMPLEMENTATION")
    invoke(cli, "writer", "start", "--id", "engineer", "--role", "al-engineer", "--files", "svc/app.py")
    invoke(cli, "writer", "finish", "--id", "engineer", "--outcome", "success")
    invoke(cli, "gates", "run", "--stage", "candidate")
    invoke(cli, "phase", "VERIFICATION")
    invoke(cli, "evidence", "plan", "--area", "unit", "--tier", "broad", "--kind", "check",
           "--scope", "svc/app.py", "--", sys.executable, "-c", "print('verified')")
    invoke(cli, "evidence", "run", "--area", "unit")
    return json.loads(cli("show")[1])


def report_data(cli, stage, invocation, findings=None, prior_resolutions=None, ui=False):
    state = json.loads(cli("show")[1])
    review = state["review"]
    packet = review["packets"][stage]
    evidence_id = state["evidence"]["entries"][-1]["id"]
    findings = findings or []
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
        "findings": findings, "prior_resolutions": prior_resolutions or [], "plan_defects": [],
        "status": "CHANGES_REQUIRED" if findings else "APPROVED",
        "assessments": {
            "rollout": {"applicable": False, "reason": "No deployment surface."},
            "ui": {"applicable": ui, "reason": "No rendered interface evidence was supplied."},
        },
    }


def blocking_finding():
    return [{"id": "F-1", "source": "AC-01", "severity": "BLOCKING",
             "description": "The verified fixture requires one correction."}]


def save_report(cli, tmp_path, stage, invocation, findings=None, prior_resolutions=None, ui=False):
    path = tmp_path / f"{stage}-report.json"
    path.write_text(json.dumps(report_data(cli, stage, invocation, findings, prior_resolutions, ui)))
    return path


def fresh_review(cli, tmp_path, invocation="fresh-invocation"):
    invoke(cli, "review-start")
    invoke(cli, "review-packet", "--stage", "fresh")
    fresh = save_report(cli, tmp_path, "fresh", invocation)
    invoke(cli, "review-fresh", "--file", str(fresh))


def finish_review(cli, tmp_path, findings=None, ui=False, invocation="review"):
    fresh_review(cli, tmp_path, f"{invocation}-fresh")
    plan = tmp_path / "approved-plan.md"
    plan.write_text("Approved plan: verify the required candidate behavior and failure paths.\n")
    invoke(cli, "review-compare-start", "--plan", str(plan))
    invoke(cli, "review-packet", "--stage", "compare")
    comparison = save_report(cli, tmp_path, "compare", f"{invocation}-compare", findings, ui=ui)
    invoke(cli, "review-result", "--file", str(comparison))


def approve_route(cli, tmp_path, findings=None, ui=False, invocation="review"):
    finish_review(cli, tmp_path, findings, ui, invocation)
    invoke(cli, "phase", "FINAL_VERIFICATION")
    return cli("approved")


def applicable_manifest(state, repo, category="loc", argv=None, anticipated_paths=()):
    source = {"path": "svc/app.py", "sha256": hashlib.sha256((repo / "svc/app.py").read_bytes()).hexdigest()}
    return manifest_for(state, repo, anticipated_paths=("svc/app.py", *anticipated_paths), category=category,
                        argv=argv, source=source)
