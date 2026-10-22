"""Closed data-only profiles for new attribution and gate reservation records."""

import hashlib
import stat
from pathlib import Path

KINDS = {
    "task-attribution-v1": {
        "task_hash",
        "baseline_capture_ref",
        "baseline_changed_paths",
        "admitted_paths",
        "contributions",
    },
    "task-content-capture-v1": {"files", "contents"},
    "task-contribution-admission-v1": {"writer_id", "task_hash", "ownership_paths", "capture_ref"},
    "task-contribution-v1": {
        "task_hash",
        "ended_task_hash",
        "writer_id",
        "ownership_paths",
        "outcome",
        "admission_ref",
        "before_capture_ref",
        "after_capture_ref",
        "changed_records",
    },
    "task-attribution-reconciliation-v1": {
        "task_hash",
        "snapshot_sha",
        "contribution_shas",
        "prior_reconciliation_shas",
        "paths",
        "capture_ref",
        "source",
        "reason",
    },
}
PREPARATION = set(
    """schema_version kind repository_identity session_id run_id task_hash destination spec
evidence_plan_digest runtime_binding command_profiles configuration_bindings
dependency_closure setup_recipe
executable_identities prerequisite_receipts environment_policy isolation_policy cache_policy
expected_evidence_destinations limitations blockers""".split()
)
PREPARATIONS = {
    "verification-preparation-v1": PREPARATION,
    "verification-preparation-v2": PREPARATION - {"task_hash"}
    | {"document_hash", "verification_authority", "invalidation_epoch"},
}
EXECUTION = {
    "execution-receipt-v1": set(
        """schema_version kind projection projection_digest original_evidence_id
original_reservation before_observation after_observation process_ownership cleanup status exit_code
counts log_path log_sha256 generated_output_disposition measured_durations""".split()
    ),
    "execution-association-v1": set(
        """schema_version kind receipt_ref repository_identity run_id
task_hash destination reservation current_observation""".split()
    ),
    # Version two records are written once planning authority is admitted
    # (al_execution_versions.V2_FIELDS); their field sets are closed as well.
    "execution-receipt-v2": set(
        """schema_version kind projection projection_digest original_evidence_id
original_reservation before_observation after_observation process_ownership cleanup status exit_code
counts log_path log_sha256 generated_output_disposition measured_durations execution_identity_digest""".split()
    ),
    "execution-association-v2": set(
        """schema_version kind receipt_ref repository_identity run_id destination reservation
current_observation document_hash verification_authority editorial_lineage""".split()
    ),
}


def validate(value, provenance):
    from . import activity_profiles as P

    runtime = Path(provenance["runtime_root"])
    expected = {k: value[k] for k in ("session_id", "run_id", "identity")}
    visited = set()

    def reference(ref, kind=None):
        fields = {"id", "kind", "sha256", "stored_path", "source", "captured_at"}
        if (
            not isinstance(ref, dict)
            or set(ref) != fields
            or ref["id"] != ref["sha256"]
            or kind is not None
            and ref["kind"] != kind
            or not P._time(ref["captured_at"])
            or not isinstance(ref["source"], str)
        ):
            raise ValueError("malformed workflow artifact reference")
        P._references(ref, provenance["members"], runtime)
        key = (ref["stored_path"], ref["kind"])
        if key in visited:
            return
        visited.add(key)
        if len(visited) > 100000:
            raise ValueError("workflow artifact graph limit")
        row = provenance["members"][Path(ref["stored_path"]).relative_to(runtime).as_posix()]
        mode = Path(ref["stored_path"]).lstat().st_mode
        if row["mode"] != 0o600 or not stat.S_ISREG(mode) or stat.S_IMODE(mode) != 0o600:
            raise ValueError("workflow artifact privacy mode changed")
        # Integrity-checked terminals: the runtime owns and reconstructs these
        # (the authority chain is revalidated by al_verification_authority on load).
        if ref["kind"] in {"task-source-content-v1", "gate-manifest", "verification-authority-v1"}:
            return
        if ref["kind"] not in KINDS and ref["kind"] not in EXECUTION and ref["kind"] not in PREPARATIONS:
            raise ValueError("unsupported workflow artifact kind")
        raw = Path(ref["stored_path"]).read_bytes()
        if len(raw) > 64 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise ValueError("workflow artifact bytes changed")
        data = P.decode(raw)
        fields = EXECUTION.get(ref["kind"]) or PREPARATIONS.get(ref["kind"]) or (
            KINDS[ref["kind"]] | {"schema_version", "kind", "binding"}
        )
        binding = (
            {
                "identity": data.get("repository_identity"),
                "session_id": data.get("session_id"),
                "run_id": data.get("run_id"),
            }
            if ref["kind"] in PREPARATIONS and isinstance(data, dict)
            else data.get("binding")
            if isinstance(data, dict)
            else None
        )
        if ref["kind"] in EXECUTION and isinstance(data, dict):
            projection = (
                data.get("projection")
                if ref["kind"].startswith("execution-receipt-v")
                else data.get("current_observation", {}).get("projection")
            )
            if not isinstance(projection, dict):
                raise ValueError("missing execution projection")
            binding = {
                "identity": projection.get("repository_identity"),
                "run_id": projection.get("run_id"),
                "session_id": value["session_id"],
            }
        if (
            not isinstance(data, dict)
            or set(data) != fields
            or data["schema_version"] != int(ref["kind"].rsplit("-v", 1)[1])
            or data["kind"] != ref["kind"]
            or binding != expected
        ):
            raise ValueError("foreign or unknown workflow artifact profile")

        def children(item):
            if isinstance(item, dict):
                if "stored_path" in item:
                    reference(item)
                else:
                    for child in item.values():
                        children(child)
            elif isinstance(item, list):
                for child in item:
                    children(child)

        children(data)

    attribution = value.get("task_attribution")
    if "verification_preparations" in value:
        if not isinstance(value["verification_preparations"], list):
            raise ValueError("unknown verification preparations")
        for ref in value["verification_preparations"]:
            if not isinstance(ref, dict) or ref.get("kind") not in PREPARATIONS:
                raise ValueError("unknown verification preparation kind")
            reference(ref, ref["kind"])
    if "task_attribution" in value:
        if (
            not isinstance(attribution, dict)
            or set(attribution) != {"snapshot", "contributions", "reconciliations"}
            or not isinstance(attribution["contributions"], list)
            or not isinstance(attribution["reconciliations"], list)
        ):
            raise ValueError("unknown task attribution registry")
        reference(attribution["snapshot"], "task-attribution-v1")
        for ref in attribution["contributions"]:
            reference(ref, "task-contribution-v1")
        for ref in attribution["reconciliations"]:
            reference(ref, "task-attribution-reconciliation-v1")
    for writer in value.get("writers", {}).values():
        if "contribution_before" not in writer:
            continue
        if attribution is None:
            raise ValueError("writer attribution without start snapshot")
        reference(writer["contribution_before"], "task-contribution-admission-v1")
    manifests = {ref["sha256"] for ref in value.get("gate_manifests", [])}
    for entry in value.get("evidence", {}).get("entries", []):
        for field, stem in [("execution_receipt", "execution-receipt"), ("execution_association", "execution-association")]:
            if field in entry:
                kind = entry[field].get("kind") if isinstance(entry[field], dict) else None
                if kind not in {stem + "-v1", stem + "-v2"}:
                    raise ValueError("unknown execution record kind")
                reference(entry[field], kind)
        if "gate_reservation" not in entry:
            continue
        reservation = entry["gate_reservation"]
        if (
            not isinstance(reservation, dict)
            or set(reservation) != {"manifest", "stage", "gate"}
            or reservation["manifest"] not in manifests
            or reservation["stage"] not in {"base", "candidate"}
            or not isinstance(reservation["gate"], str)
            or not P.ID.fullmatch(reservation["gate"])
            or entry["area"] != "gate-" + reservation["stage"] + "-" + reservation["gate"]
            or entry.get("gate_stage", reservation["stage"]) != reservation["stage"]
            or entry.get("gate_manifest", reservation["manifest"]) != reservation["manifest"]
        ):
            raise ValueError("malformed gate destination reservation")
