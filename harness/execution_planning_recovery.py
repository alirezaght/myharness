"""Exact-preview fresh planning recovery on the same sealed execution."""

import hashlib
import json
import secrets
from pathlib import Path

from harness.db import transaction, utcnow
from harness.execution_contract import canonical, digest
from harness.execution_models import ExecutionConflict
from harness.execution_runtime_recovery import _launcher_call, _locks, _request_id
from harness.execution_store import get_execution
from harness.template_classification import _ordinary


def _condition(execution, envelope):
    from harness.execution_hosts import al_source

    source = al_source(execution, envelope)
    try:
        raw, _ = _ordinary(source, 32 * 1024 * 1024)
    except FileNotFoundError:
        return "missing"
    except PermissionError:
        return "unreadable"
    except (OSError, ValueError):
        return "binding-mismatch"
    try:
        state = json.loads(raw)
    except (ValueError, UnicodeError):
        return "corrupt"
    if not isinstance(state, dict):
        return "corrupt"
    if (
        state.get("session_id") != execution["al_session_id"]
        or state.get("run_id") != execution["al_run_id"]
        or state.get("integration", {}).get("binding")
        != execution["binding"]
        | {
            "handoff_hash": execution["handoff_hash"],
            "al_session_id": execution["al_session_id"],
            "al_run_id": execution["al_run_id"],
        }
    ):
        return "binding-mismatch"
    return "intact"


def _preview_inputs(controller, execution):
    from harness.execution_authority import require_execution_authority
    from harness.execution_bootstrap import prepared_authority_snapshot
    from harness.execution_handoff import atomic_file
    from harness.execution_hosts import managed_launcher
    from harness.execution_recovery_settlement import observe_recovery_settlement

    envelope = require_execution_authority(controller, execution["id"], operation="read")
    condition = _condition(execution, envelope)
    settlement = observe_recovery_settlement(controller, execution)
    authority = prepared_authority_snapshot(Path(execution["handoff_path"]).parent)
    obligations = list(settlement["obligations"])
    inputs, lineage_ref = None, None
    if condition in {"missing", "intact"}:
        # Observation journals are immutable; a later source inventory needs a
        # distinct namespace. This nonce is deliberately absent from preview data.
        identifier = "preview-" + secrets.token_hex(16)
        inputs = _launcher_call(
            managed_launcher(),
            envelope["repository"],
            "recovery-inputs" if condition == "missing" else "inputs",
            "--repository",
            envelope["repository"],
            "--platform",
            execution["host"],
            "--session",
            execution["al_session_id"],
            "--request-id",
            identifier,
            "--authority-ref",
            canonical(authority),
            *(
                [
                    "--handoff-ref",
                    canonical(
                        {"path": execution["handoff_path"], "sha256": execution["handoff_hash"]}
                    ),
                ]
                if condition == "missing"
                else []
            ),
        )
        if condition == "missing":
            lineage = inputs["recovery_lineage"]
            raw = canonical(lineage).encode()
            path = (
                Path(execution["handoff_path"]).parent
                / "recovery-lineages"
                / (hashlib.sha256(raw).hexdigest() + ".json")
            )
            atomic_file(path, raw)
            lineage_ref = {"stored_path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}
            obligations += lineage["obligations"]
    else:
        obligations.append(
            "Use ordinary scoped replanning for intact state"
            if condition == "intact"
            else "Active runtime is " + condition + "; missing-state recovery is unavailable"
        )
    preview = {
        "schema_version": 1,
        "kind": "fresh-planning-preview-v1",
        "execution_id": execution["id"],
        "task_id": execution["task_id"],
        "approval_binding": execution["binding"],
        "expected_epoch_id": execution["epoch_id"],
        "expected_authority_generation": execution["authority_generation"],
        "transition_mode": "fresh-planning-missing-state"
        if condition == "missing"
        else "fresh-planning-intact",
        "predecessor_binding_ref": inputs["predecessor_subject"] if inputs else None,
        "authority_handoff_ref": {
            "path": execution["handoff_path"],
            "sha256": execution["handoff_hash"],
        },
        "initialization_ref": inputs["predecessor"]["initialization"] if inputs else None,
        "state_condition": condition,
        "requirement_lineage_ref": lineage_ref,
        "pending_decisions": inputs["recovery_lineage"]["pending_decisions"]
        if inputs and condition == "missing"
        else [],
        "known_failures": inputs["recovery_lineage"]["historical_refs"]
        if inputs and condition == "missing"
        else [],
        "candidate_identity": inputs["candidate_identity"] if inputs else None,
        "source_inventory_ref": inputs.get("source_inventory") if inputs else None,
        "source_limitations": [
            "Repository files are unchanged by this action",
            "Missing uncommitted source edits cannot be recovered by restarting planning",
            "Original source ownership and requirement history may be incomplete",
        ],
        "settlement_ref": digest(settlement),
        "runtime_layout": "harness-durable-epoch-v1",
        "history_completeness": "incomplete" if condition == "missing" else "observed-current",
        "obligations": obligations,
        "readiness": ("investigation-only" if condition == "missing" else "ready")
        if inputs and not settlement["obligations"]
        else "unavailable",
        "authority_snapshot_ref": authority,
        "distribution_identity": inputs["distribution_identity"] if inputs else None,
        "predecessor_state_sha256": inputs.get("predecessor_state_sha256") if inputs else None,
    }
    return {"preview": preview, "preview_sha256": digest(preview)}, inputs, settlement


def _preview(controller, execution):
    try:
        return _preview_inputs(controller, execution)
    except FileNotFoundError as error:
        raise ExecutionConflict(
            "planning recovery unavailable: required authority artifact is missing ("
            + str(error.filename or "retained source")
            + "); restore independently verified retained bytes before starting planning"
        ) from error
    except PermissionError as error:
        raise ExecutionConflict(
            "planning recovery unavailable: required authority artifact is unreadable ("
            + str(error.filename or "retained source")
            + ")"
        ) from error


def preview_planning(controller, execution_id, *, expected_epoch_id, expected_authority_generation):
    from harness.execution_epochs import require_active_epoch

    execution = require_active_epoch(
        controller.conn,
        execution_id,
        expected_epoch_id=expected_epoch_id,
        expected_authority_generation=expected_authority_generation,
        mutation=True,
    )
    with _locks(execution):
        return _preview(controller, execution)[0]


def start_planning(
    controller,
    execution_id,
    *,
    request_id,
    preview_sha256,
    expected_epoch_id,
    expected_authority_generation,
):
    from harness.execution_epochs import require_active_epoch
    from harness.execution_handoff import atomic_file

    _request_id(request_id)
    existing = controller.conn.execute(
        "SELECT request FROM execution_runtime_transitions WHERE request_id=? AND execution_id=?",
        (request_id, execution_id),
    ).fetchone()
    if existing:
        request = json.loads(existing["request"])
        if (
            request.get("preview_sha256") != preview_sha256
            or request["expected_epoch_id"] != expected_epoch_id
            or request["expected_authority_generation"] != expected_authority_generation
        ):
            raise ExecutionConflict("planning request ID reused with a changed preview or epoch")
        return finish_planning(
            controller, execution_id, request_id=request_id, preview_sha256=preview_sha256
        )
    execution = require_active_epoch(
        controller.conn,
        execution_id,
        expected_epoch_id=expected_epoch_id,
        expected_authority_generation=expected_authority_generation,
        mutation=True,
    )
    with _locks(execution):
        observed, inputs, settlement = _preview(controller, execution)
        preview = observed["preview"]
        if observed["preview_sha256"] != preview_sha256:
            raise ExecutionConflict("stale planning preview; inspect current recovery inputs")
        if preview["readiness"] == "unavailable":
            raise ExecutionConflict(
                "planning recovery unavailable: " + "; ".join(preview["obligations"])
            )
        raw = canonical(settlement).encode()
        path = (
            Path(execution["handoff_path"]).parent
            / "settlements"
            / (hashlib.sha256(raw).hexdigest() + ".json")
        )
        atomic_file(path, raw)
        request = {
            "schema_version": 2,
            "request_id": request_id,
            "execution_id": execution_id,
            "expected_epoch_id": expected_epoch_id,
            "expected_authority_generation": expected_authority_generation,
            "approved_requirement_hash": (
                inputs["recovery_lineage"]["revision_hashes"][-1]
                if "recovery_lineage" in inputs
                else inputs["approved_requirement_hash"]
            ),
            "expected_candidate_identity": inputs["candidate_identity"],
            "predecessor_subject": inputs["predecessor_subject"],
            "distribution_identity": inputs["distribution_identity"],
            "authority_snapshot_ref": preview["authority_snapshot_ref"],
            "reason": "Explicit exact-preview fresh planning recovery on the same approved task",
            "transition_mode": preview["transition_mode"],
            "preview_sha256": preview_sha256,
            "recovery_lineage_ref": preview["requirement_lineage_ref"],
            "settlement_ref": {"stored_path": str(path), "sha256": hashlib.sha256(raw).hexdigest()},
            "authority_handoff_ref": preview["authority_handoff_ref"],
            "expected_predecessor_state_sha256": preview["predecessor_state_sha256"],
        }
        raw = canonical(request)
        with transaction(controller.conn):
            if controller.conn.execute(
                "SELECT 1 FROM execution_runtime_transitions "
                "WHERE execution_id=? AND state!='ACTIVE'",
                (execution_id,),
            ).fetchone():
                raise ExecutionConflict(
                    "another runtime transition owns this execution; finish its request"
                )
            now = utcnow()
            controller.conn.execute(
                "INSERT INTO execution_runtime_transitions "
                "(request_id,execution_id,request,request_hash,state,created_at,updated_at) "
                "VALUES (?,?,?,?,'PREPARING',?,?)",
                (request_id, execution_id, raw, digest(raw.encode()), now, now),
            )
    return finish_planning(
        controller, execution_id, request_id=request_id, preview_sha256=preview_sha256
    )


def inspect_planning(controller, execution_id, *, request_id):
    from harness.execution_runtime_recovery import inspect_runtime

    result = inspect_runtime(controller, execution_id, request_id=request_id)
    row = controller.conn.execute(
        "SELECT request,activation FROM execution_runtime_transitions "
        "WHERE request_id=? AND execution_id=?",
        (request_id, execution_id),
    ).fetchone()
    request = json.loads(row["request"])
    if request.get("transition_mode") not in {
        "fresh-planning-missing-state",
        "fresh-planning-intact",
    }:
        raise ExecutionConflict("request is not a fresh planning transition")
    result["preview_sha256"] = request["preview_sha256"]
    from harness.execution_planning_dispatch import dispatch_view

    result["fresh_dispatch"] = (
        dispatch_view(controller.conn, execution_id, json.loads(row["activation"]))
        if row["activation"]
        else None
    )
    return result


def finish_planning(controller, execution_id, *, request_id, preview_sha256):
    from harness.execution_bootstrap import execution_environment
    from harness.execution_hosts import managed_launcher
    from harness.execution_initialization_channel import (
        InitializationChannel,
        build_start_resources,
    )
    from harness.execution_runtime_recovery import recover_runtime_transition

    _request_id(request_id)
    row = controller.conn.execute(
        "SELECT * FROM execution_runtime_transitions WHERE execution_id=? AND request_id=?",
        (execution_id, request_id),
    ).fetchone()
    if not row:
        raise LookupError("planning recovery request not found")
    request = json.loads(row["request"])
    if (
        request.get("transition_mode")
        not in {"fresh-planning-missing-state", "fresh-planning-intact"}
        or request["preview_sha256"] != preview_sha256
    ):
        raise ExecutionConflict("finish recovery requires its exact original planning preview")
    if row["state"] == "ACTIVE" and json.loads(row["activation"]).get("fresh_dispatch"):
        return inspect_planning(controller, execution_id, request_id=request_id)
    if not row["preparation"]:
        execution = get_execution(controller.conn, execution_id)
        with _locks(execution):

            def persist(receipt):
                with transaction(controller.conn):
                    controller.conn.execute(
                        "UPDATE execution_runtime_transitions SET "
                        "preparation=?,state='PREPARED',updated_at=? "
                        "WHERE request_id=? AND state='PREPARING'",
                        (canonical(receipt), utcnow(), request_id),
                    )

            channel = InitializationChannel(
                request,
                lambda notice: build_start_resources(
                    controller, execution, notice["plan_ref"], notice
                ),
                persist,
                Path(execution["handoff_path"]).parent / "runtime-preparations" / request_id,
            )
            # The issuer reuses the same reservation and validates unchanged inputs.
            from harness.execution_handoff import read_handoff

            envelope = read_handoff(execution["handoff_path"], execution["handoff_hash"])
            channel.run(
                managed_launcher(),
                envelope["repository"],
                execution["host"],
                environment=execution_environment(),
            )
    recover_runtime_transition(controller, execution_id, request_id=request_id)
    return dispatch_fresh_planning(controller, execution_id, request_id=request_id)


def dispatch_fresh_planning(controller, execution_id, *, request_id):
    from harness.execution_worker import launch_worker

    execution = get_execution(controller.conn, execution_id)
    return launch_worker(
        controller.conn,
        execution_id,
        mode="start",
        expected_epoch_id=execution["epoch_id"],
        expected_authority_generation=execution["authority_generation"],
        planning_request_id=request_id,
    )
