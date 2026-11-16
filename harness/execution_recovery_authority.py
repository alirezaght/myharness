"""Read-only recovery authority rooted in retained issuer and sealed Harness records."""

import hashlib
import json
from pathlib import Path

from harness.execution_contract import digest
from harness.execution_models import ExecutionConflict
from harness.execution_recovery_lineage import build_recovery_lineage
from harness.execution_recovery_settlement import observe_recovery_settlement
from harness.template_classification import _git, _ordinary, selected_reference, validate_reference


def validate_recovery_inputs(controller, execution, envelope, value):
    fields = {
        "kind",
        "execution_id",
        "predecessor",
        "predecessor_initialization",
        "source_anchors",
        "authority_handoff_ref",
    }
    if set(value) != fields or value["execution_id"] != execution["id"]:
        raise ExecutionConflict("invalid closed recovery input contract")
    previous = value["predecessor"]
    handoff = value["authority_handoff_ref"]
    if (
        handoff != {"path": execution["handoff_path"], "sha256": execution["handoff_hash"]}
        or previous["session_id"] != execution["al_session_id"]
        or previous["run_id"] != execution["al_run_id"]
        or previous["platform"] != execution["host"]
        or previous["repository_identity"]["root"] != envelope["repository"]
        or previous["status"] != "ACTIVE"
    ):
        raise ExecutionConflict("recovery protected predecessor differs from current approval")
    row = controller.conn.execute(
        "SELECT identity FROM execution_initializations WHERE execution_id=? AND epoch_id=?",
        (execution["id"], execution["epoch_id"]),
    ).fetchone()
    if not row or not row["identity"]:
        raise ExecutionConflict("retained protected initialization identity is unavailable")
    known = json.loads(row["identity"])
    root = Path(known["initialization_path"]).parent.parent
    subject = {k: previous[k] for k in ("repository_identity", "platform", "session_id")}
    path = root / "runs" / (digest(subject).split(":")[1] + ".json")
    binding_raw, mode = _ordinary(path, 4 * 1024 * 1024)
    initial_raw, initial_mode = _ordinary(known["initialization_path"], 4 * 1024 * 1024)
    if (
        mode != 0o600
        or initial_mode != 0o600
        or json.loads(binding_raw) != previous
        or hashlib.sha256(initial_raw).hexdigest() != previous["initialization"]["sha256"]
        or previous["initialization"]["stored_path"] != known["initialization_path"]
        or json.loads(initial_raw) != value["predecessor_initialization"]
    ):
        raise ExecutionConflict("recovery independent issuer binding changed")
    source = Path(known["source"])
    if source != Path(previous.get("runtime_directory", source.parent)) / (
        previous["session_id"] + ".json"
    ):
        raise ExecutionConflict("recovery source path differs from protected runtime layout")
    # A dangling alias, unreadable file or malformed file is never absence.
    if any(p.is_symlink() for p in (source, *source.parents)):
        raise ExecutionConflict("recovery source alias is not missing state")
    try:
        source.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ExecutionConflict("recovery source is present; use intact runtime replanning")
    anchors = value["source_anchors"]
    head = _git(Path(envelope["repository"]), "rev-parse", "HEAD").decode().strip()
    if (
        anchors["repository_identity"] != previous["repository_identity"]
        or any(
            anchors[k] != head
            for k in (
                "target_label",
                "target_oid",
                "merge_base_oid",
                "baseline_head_oid",
                "observed_head_oid",
            )
        )
        or anchors["predecessor_attribution"] is not None
    ):
        raise ExecutionConflict("missing-state source observation changed")
    classified = validate_reference(selected_reference(execution, envelope, known))
    settlement = observe_recovery_settlement(controller, execution)
    return {
        "classified_paths": classified,
        "harness_binding": {
            "authority_identity": digest(
                {
                    "database": envelope["database"],
                    "approval": execution["binding"],
                }
            ).split(":")[1],
            "execution_id": execution["id"],
            "authority_generation": execution["authority_generation"] + 1,
        },
        "recovery_lineage": build_recovery_lineage(
            controller,
            execution,
            envelope,
            {
                "predecessor_transaction": previous,
                "predecessor_initialization": value["predecessor_initialization"],
                "source_anchors": anchors,
            },
        ),
        "settlement": settlement,
    }


def validate_recovery_plan(controller, execution_id, context):
    from harness.execution_authority import require_execution_authority
    from harness.execution_runtime_fences import _reference
    from harness.execution_store import get_execution
    from harness.template_classification import prepared_context

    plan = prepared_context(context)
    execution = get_execution(controller.conn, execution_id)
    envelope = require_execution_authority(controller, execution_id, operation="read")
    request = plan["request"]
    row = controller.conn.execute(
        "SELECT request,request_hash FROM execution_runtime_transitions "
        "WHERE execution_id=? AND request_id=?",
        (execution_id, request["request_id"]),
    ).fetchone()
    if (
        not row
        or json.loads(row["request"]) != request
        or digest(row["request"].encode()) != row["request_hash"]
        or request["execution_id"] != execution_id
        or request["expected_epoch_id"] != execution["epoch_id"]
        or request["expected_authority_generation"] != execution["authority_generation"]
        or plan["authority_handoff_ref"] != request["authority_handoff_ref"]
    ):
        raise ExecutionConflict("recovery plan is not the exact current epoch request")
    inputs = {
        "kind": "missing-state-recovery-inputs-v1",
        "execution_id": execution_id,
        "predecessor": plan["predecessor_transaction"],
        "predecessor_initialization": plan["predecessor_initialization"],
        "source_anchors": plan["source_anchors"],
        "authority_handoff_ref": plan["authority_handoff_ref"],
    }
    result = validate_recovery_inputs(controller, execution, envelope, inputs)
    lineage = _reference(plan["recovery_lineage_ref"])
    settlement = _reference(request["settlement_ref"])
    if (
        result["recovery_lineage"] != lineage
        or result["settlement"] != settlement
        or settlement["obligations"]
        or lineage["revision_hashes"][-1] != request["approved_requirement_hash"]
    ):
        raise ExecutionConflict("recovery lineage or native settlement changed")
    scoped = {a["id"]: a["artifact"]["sha256"] for a in lineage["amendments"]}
    return execution, envelope, None, scoped
