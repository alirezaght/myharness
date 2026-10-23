"""Reconstruct a planned destination and its bounded local verification inputs."""

import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

import al_ledger as L
import al_runtime_resources as Resources
import al_store
import al_task
from al_schema import refuse


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def environment(names):
    from al_environment_observation import environment as current
    return current(names)


def ambient_environment():
    from al_environment_observation import ambient_environment as current
    return current()


def execution_environment(declared):
    from al_environment_observation import execution_environment as current
    return current(declared)


def environment_gaps(policy):
    """Current-environment check for a recorded preparation/projection policy."""
    if policy.get("kind") == "declared-execution-v1":
        return [] if policy["inputs"] == execution_environment(policy["declared_names"]) else [
            "execution environment inputs differ"]
    if policy.get("kind") == "exact-ambient-v1":
        return [] if policy["inputs"] == ambient_environment() else ["execution ambient inputs differ"]
    return ["unsupported execution environment policy"]


def executable(value):
    selected = value if os.path.isabs(value) else shutil.which(value)
    if not selected:
        raise ValueError("required executable is unavailable: " + value)
    path = Path(selected).resolve(strict=True)
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if before != after or not stat.S_ISREG(before.st_mode) or not before.st_mode & 0o111:
        raise ValueError("unstable or nonexecutable verification tool")
    return {
        "requested": value,
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "mode": stat.S_IMODE(before.st_mode),
    }


def destination(st, *, area=None, gate=None, stage=None):
    if bool(area) == bool(gate):
        refuse("select exactly one planned area or gate")
    if area:
        if stage:
            refuse("an evidence area has no gate stage")
        spec = L.ledger(st)["plan"].get(area)
        if spec is None:
            refuse("unknown verification area")
        return {"kind": "area", "area_or_gate": area, "plan_digest": digest(spec)}, dict(spec)
    if stage not in {"base", "candidate"}:
        refuse("verification gate preparation requires explicit stage")
    import al_gates as G

    manifest = G.manifest(st)
    if not manifest:
        refuse("verification gate manifest missing")
    matches = [g for g in manifest["gates"] if g["id"] == gate]
    if len(matches) != 1 or matches[0]["applicability"] != "APPLICABLE":
        refuse("gate is not an executable current obligation")
    row = matches[0]
    spec = {
        "area": "gate-" + stage + "-" + gate,
        "argv": row["argv"],
        "scope": row["scope"],
        "sources": row.get("sources", []),
        "env": row["env_names"],
        "cwd": row["cwd"],
        "kind": "check",
        "tier": "focused",
        "execution_kind": row["execution_kind"],
        "sourced_signature": row.get("execution_signature"),
    }
    return {
        "kind": "gate",
        "area_or_gate": gate,
        "stage": stage,
        "manifest_ref": st["gate_manifests"][-1],
        "plan_digest": digest(row),
        "obligation_ids": row.get("obligation_ids", []),
        "execution_signature": row.get("execution_signature"),
    }, spec


def command_profile(spec):
    argv = spec["argv"]
    blockers = []
    resources = {}
    if not isinstance(argv, list) or not argv or any(not isinstance(v, str) or not v for v in argv):
        return None, ["literal nonempty verification argv required"]
    try:
        tool = executable(argv[0])
    except (ValueError, OSError) as exc:
        return None, [str(exc)]
    kind = None
    independent = False
    outputs = "forbid"
    inputs = []
    # These two stdlib-only readers cannot observe Git or a destination label.
    if (
        len(argv) == 5
        and argv[1] == "-I"
        and argv[2] == Resources.resource_identity("al_architecture_check.py")
        and argv[3] == "--unit"
    ):
        if Path(argv[4]).is_absolute() or ".." in Path(argv[4]).parts:
            blockers.append("unsafe architecture unit")
        if not spec.get("sourced_signature"):
            blockers.append(
                "architecture preparation requires its current sourced executable binding"
            )
        kind = "bundled-module-ban-v1"
        independent = True
        inputs = [(Path(spec.get("cwd", ".")) / argv[4] / "pyproject.toml").as_posix()]
        resources["al_architecture_check.py"] = {
            "identity": argv[2],
            "sha256": hashlib.sha256(
                Resources.resource_bytes("al_architecture_check.py")
            ).hexdigest(),
        }
    elif len(argv) == 5 and argv[1:4] == ["-I", "-m", "json.tool"]:
        if Path(argv[4]).is_absolute() or ".." in Path(argv[4]).parts or argv[4].startswith("-"):
            blockers.append("json.tool requires one literal repository input")
        kind = "isolated-json-tool-v1"
        independent = True
        inputs = [(Path(spec.get("cwd", ".")) / argv[4]).as_posix()]
        import json.tool

        for module in (json, json.tool):
            path = Path(module.__file__).resolve()
            resources[str(path)] = {
                "identity": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "mode": stat.S_IMODE(path.stat().st_mode),
            }
    elif argv[:2] == ["uv", "run"] or argv[:2] == ["uv", "build"]:
        kind = "sourced-locked-toolchain-v1"
        outputs = "discard-and-rebuild"
        if not (al_store.REPO_ROOT / "uv.lock").is_file():
            blockers.append("locked toolchain requires uv.lock")
        if not (al_store.REPO_ROOT / "pyproject.toml").is_file():
            blockers.append("locked toolchain requires pyproject.toml")
        # A plain tool invocation is not a proof of its setup/configuration.
        blockers.append("toolchain preparation requires independently sourced setup chain")
    else:
        blockers.append("unsupported verification command/setup profile")
    if independent and tool["path"] != str(Path(sys.executable).resolve()):
        blockers.append("isolated reader requires the bound running interpreter")
    profile = {
        "id": kind or "unsupported",
        "version": 1,
        "executable_identity": tool,
        "argv": argv,
        "cwd_semantics": spec.get("cwd", "."),
        "stage_independent": independent,
        "resources": resources,
        "repository_inputs": inputs,
        "generated_output_policy": outputs,
    }
    if independent and not blockers:
        from al_verification_scope import closure

        try:
            profile["source_closure"] = closure(profile, spec)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            blockers.append("unsupported reader input closure: " + str(exc))
    return profile, blockers


def reconstruct(st, target, spec, *, no_cache=False):
    gaps = L.check_scope(spec["scope"], spec.get("sources", []), spec.get("cwd", "."))
    gaps += L.unmet(st, spec)
    profile, unsupported = command_profile(spec)
    gaps += unsupported
    if spec.get("execution_kind", "local") != "local":
        gaps.append("remote preparation has no reviewed local ownership recipe")
    from al_verification_scope import source_binding

    source = (
        source_binding(st, profile)
        if profile and "source_closure" in profile
        else {"records": {}, "unsupported": True}
    )
    members = source["records"]
    for path in (profile or {}).get("repository_inputs", []):
        if path not in members or members[path]["worktree"]["type"] != "file":
            gaps.append("verification input is not an observed regular source member: " + path)
        from al_sources import contains

        if not any(contains(scope, path) for scope in spec["scope"]):
            gaps.append("verification input outside declared scope: " + path)
    runtime = {
        "implementation": Resources.implementation_digest(),
        "origin": Resources.execution_origin(),
    }
    prerequisites = []
    for name in ([spec["preflight"]] if spec.get("preflight") else []) + list(
        spec.get("needs", [])
    ):
        entry = L.latest(st, name)
        prerequisites.append(
            {
                "area": name,
                "evidence": entry["id"] if entry else None,
                "reservation": entry.get("reservation") if entry else None,
                "log_sha256": entry.get("log_sha256") if entry else None,
            }
        )
    result = {
        "schema_version": 1,
        "kind": "verification-preparation-v1",
        "repository_identity": st["identity"],
        "session_id": st["session_id"],
        "run_id": st["run_id"],
        "task_hash": al_task.task_hash(st),
        "destination": target,
        "spec": spec,
        "evidence_plan_digest": digest(spec),
        "runtime_binding": runtime,
        "command_profiles": [profile] if profile else [],
        "configuration_bindings": source,
        "dependency_closure": spec["scope"],
        "setup_recipe": {"kind": "isolated-stdlib-reader-v1"}
        if profile and profile["stage_independent"]
        else None,
        "executable_identities": [profile["executable_identity"]] if profile else [],
        "prerequisite_receipts": prerequisites,
        "environment_policy": {
            "kind": "declared-execution-v1",
            "declared_names": sorted(set(spec.get("env", []))),
            "inputs": execution_environment(spec.get("env", [])),
        },
        "isolation_policy": {"kind": "owned-disposable-checkout-v1", "foreign_services": "forbid"},
        "cache_policy": {"enabled": not no_cache, "scope": "exact-execution-projection"},
        "expected_evidence_destinations": [target],
        "limitations": [],
        "blockers": sorted(set(gaps)),
    }

    from al_execution_versions import enable
    return enable(st, result)
