"""Strict source consumers: exact claims, legacy presence and source-qualified bridge coverage."""

import hashlib
import json
import stat
from pathlib import Path

from harness.execution_contract import canonical
from harness.execution_models import TERMINAL
from harness.policy.claims import overlaps
from harness.policy.contracts import require

PROTOCOL_FILES = (
    "policy/source_dependencies.py",
    "policy/native_presence.py",
    "policy/claims.py",
    "policy/domains.py",
    "policy/admission.py",
    "policy/resources.py",
    "policy/resource_bindings.py",
    "policy/reconciliation.py",
    "execution_worker.py",
    "execution_authority.py",
    "execution_operations.py",
    "execution_recovery_settlement.py",
    "task_environment.py",
)
FIXED = ("id", "epoch_id", "authority_generation", "handoff_path", "handoff_hash", "worker_token")


def selected(values):
    return tuple(v for v in values if v.get("purpose") == "mutable_dependency")


def active_executions(conn):
    from harness.execution_store import get_execution

    return [
        get_execution(conn, r["id"])
        for r in conn.execute("SELECT id,status FROM executions")
        if r["status"] not in TERMINAL
    ]


def local_identity():
    root = Path(__file__).resolve().parents[1]
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in PROTOCOL_FILES}


def selector_version(path):
    try:
        info = Path(path).lstat()
    except FileNotFoundError:
        return None
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )


def binding_refs(observations):
    return {key: value["manifest"] for key, value in sorted(observations.items())}


def bind_protocol(values, observations):
    refs = binding_refs(observations)
    return tuple(
        dict(v, source_protocol=refs) if v.get("purpose") == "mutable_dependency" else v
        for v in values
    )


def base_claim(value):
    return {k: v for k, v in value.items() if k != "source_protocol"}


def prepare_support(conn):
    """All import/manifest probes precede the epoch/SQLite admission transaction."""
    from harness.execution_bridge import bridge_root, verify_snapshot

    expected, observations = local_identity(), {}
    for execution in active_executions(conn):
        try:
            root = bridge_root(execution)
            fingerprint = verify_snapshot(root)
            files = json.loads((root / "manifest.json").read_bytes())["files"]
            require(
                all(files.get(name) == sha for name, sha in expected.items()),
                "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
            )
        except (OSError, ValueError, KeyError):
            require(False, "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
        observations[execution["id"]] = {
            "binding": {k: execution[k] for k in FIXED},
            "manifest": fingerprint,
            "selectors": [
                (
                    str(Path(execution["handoff_path"]).parent / "bridge-selection.json"),
                    selector_version(
                        Path(execution["handoff_path"]).parent / "bridge-selection.json"
                    ),
                ),
                (str(root / "manifest.json"), selector_version(root / "manifest.json")),
            ],
        }
    return observations


def validate_support(conn, observations):
    # Selected immutable bridges cannot be silently upgraded by an agent. Epoch/worker changes,
    # including newly admitted executions, invalidate the prepared source-support observation.
    require(isinstance(observations, dict), "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
    current = {e["id"]: {k: e[k] for k in FIXED} for e in active_executions(conn)}
    require(set(current) == set(observations), "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
    require(all(current[k] == observations[k]["binding"] for k in current), "AUTHORITY_STALE")
    require(
        all(
            selector_version(path) == version
            for value in observations.values()
            for path, version in value["selectors"]
        ),
        "AUTHORITY_STALE",
    )


def require_predecessors_clear(conn, dependency_claims, *, own_attempt_id=None):
    values = selected(dependency_claims)
    if not values:
        return
    for row in conn.execute("SELECT attempt_id,resources FROM policy_legacy_admissions"):
        if row["attempt_id"] == own_attempt_id:
            continue
        for old in json.loads(row["resources"]):
            require(
                not any(
                    old["access"] == "unknown"
                    or new["access"] == "unknown"
                    or old["repository"] == new["repository"]
                    and overlaps(old["resource"], new["resource"])
                    for new in values
                ),
                "ATTEMPT_NOT_EXECUTABLE",
            )
    # An outer process is not excluded by execution/principal identity. Status and PID absence
    # cannot discharge its launch possibility. Only the exact fenced disposition can do so.
    require(
        not conn.execute(
            "SELECT 1 FROM execution_attempts WHERE dependency_launch_state "
            "NOT IN ('not_started','qualified_closed') LIMIT 1"
        ).fetchone(),
        "ATTEMPT_NOT_EXECUTABLE",
    )
    require(
        not conn.execute(
            "SELECT 1 FROM reliability_process_spans s WHERE NOT EXISTS "
            "(SELECT 1 FROM policy_processes p WHERE p.span_id=s.span_id) LIMIT 1"
        ).fetchone(),
        "ATTEMPT_NOT_EXECUTABLE",
    )


def require_covered(conn, attempt, required):
    if not required:
        return
    require(attempt is not None and not attempt.legacy, "ATTEMPT_NOT_EXECUTABLE")
    actual = selected(attempt.intent.claims)
    require(
        {canonical(base_claim(v)) for v in required} <= {canonical(base_claim(v)) for v in actual},
        "AUTHORITY_STALE",
    )
    rows = [
        json.loads(r["claim"])
        for r in conn.execute(
            "SELECT claim FROM policy_resource_claims WHERE attempt_id=? "
            "AND state IN ('reserved','active')",
            (attempt.intent.id,),
        )
    ]
    require(
        {canonical(v) for v in actual} <= {canonical(v) for v in rows}, "ATTEMPT_NOT_EXECUTABLE"
    )
    require_predecessors_clear(conn, actual, own_attempt_id=attempt.intent.id)
