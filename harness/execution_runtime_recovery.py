"""Trusted runtime recovery metadata; protected selection must agree with SQLite."""

import hashlib
import json
from pathlib import Path

from harness.execution_contract import digest
from harness.execution_models import ExecutionConflict
from harness.template_classification import _ordinary


def verify_selected_authority(execution, epoch):
    identity = epoch["identity"]
    reference = identity.get("active_selector_ref")
    if not isinstance(reference, dict) or set(reference) != {"stored_path", "sha256"}:
        raise ExecutionConflict("runtime selector missing; recover the recorded transition")
    raw, mode = _ordinary(reference["stored_path"])
    value = json.loads(raw)
    if (
        mode != 0o600
        or hashlib.sha256(raw).hexdigest() != reference["sha256"]
        or value.get("kind") != "active-successor-epoch-v1"
        or value.get("execution_id") != execution["id"]
        or value.get("epoch_id") != execution["epoch_id"]
        or value.get("authority_generation") != execution["authority_generation"]
        or value.get("successor_transaction", {}).get("session_id") != execution["al_session_id"]
        or value.get("successor_transaction", {}).get("run_id") != execution["al_run_id"]
        or identity.get("handoff_hash") != execution["handoff_hash"]
        or identity.get("handoff_path") != execution["handoff_path"]
    ):
        raise ExecutionConflict(
            "runtime selector/database mismatch; recover the recorded transition"
        )
    path = Path(reference["stored_path"])
    expected = (
        Path(value["storage"])
        / "successor-active"
        / (digest(value["successor_subject"]).split(":")[1] + ".json")
    )
    if path != expected:
        raise ExecutionConflict("runtime selector escaped protected issuer storage")
    return value


def _request_id(value):
    from harness.execution_contract import identity

    identity(value)
    return value


def _launcher_call(launcher, repository, *arguments):
    from harness.execution_bootstrap import execution_environment
    from harness.execution_contract import loads
    from harness.owned_subprocess import run_owned

    result = run_owned(
        [launcher, "runtime-successor", *arguments],
        cwd=repository,
        env=execution_environment(),
        timeout=120,
    )
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise ExecutionConflict("protected runtime issuer refused; inspect the preparation journal")
    return loads(result.stdout, max_bytes=1024 * 1024)


def _locks(execution):
    import contextlib

    from harness.execution_epoch_lock import epoch_action_lock
    from harness.execution_worker import worker_lock

    @contextlib.contextmanager
    def acquired():
        with worker_lock(execution), epoch_action_lock(execution, exclusive=True):
            yield

    return acquired()


def _settled(controller, execution):
    conn = controller.conn
    if conn.execute(
        (
            "SELECT 1 FROM browser_runs WHERE execution_id=? AND state IN "
            "('Dispatching','Running','Uncertain')"
        ),
        (execution["id"],),
    ).fetchone():
        raise ExecutionConflict("runtime recovery requires settled browser operations")
    if conn.execute(
        (
            "SELECT 1 FROM delivery_operations WHERE execution_id=? AND state IN "
            "('Dispatching','Uncertain')"
        ),
        (execution["id"],),
    ).fetchone():
        raise ExecutionConflict("runtime recovery requires settled external delivery operations")


def prepare_runtime(
    controller,
    execution_id,
    *,
    expected_epoch_id,
    expected_authority_generation,
    request_id,
    reason,
):
    from harness.db import transaction, utcnow
    from harness.execution_authority import require_execution_authority
    from harness.execution_bootstrap import execution_environment, prepared_authority_snapshot
    from harness.execution_contract import canonical
    from harness.execution_epochs import require_active_epoch
    from harness.execution_hosts import managed_launcher
    from harness.execution_initialization_channel import (
        InitializationChannel,
        build_start_resources,
    )

    _request_id(request_id)
    if not isinstance(reason, str) or len(reason.strip()) < 12:
        raise ValueError("runtime recovery requires a substantive reason")
    execution = require_active_epoch(
        controller.conn,
        execution_id,
        expected_epoch_id=expected_epoch_id,
        expected_authority_generation=expected_authority_generation,
        mutation=True,
    )
    with _locks(execution):
        _settled(controller, execution)
        envelope = require_execution_authority(controller, execution_id, operation="read")
        directory = Path(execution["authority_directory"])
        authority = prepared_authority_snapshot(directory)
        launcher = managed_launcher()
        inputs = _launcher_call(
            launcher,
            envelope["repository"],
            "inputs",
            "--repository",
            envelope["repository"],
            "--platform",
            execution["host"],
            "--session",
            execution["al_session_id"],
            "--request-id",
            request_id,
            "--authority-ref",
            canonical(authority),
        )
        request = {
            "schema_version": 1,
            "request_id": request_id,
            "execution_id": execution_id,
            "expected_epoch_id": expected_epoch_id,
            "expected_authority_generation": expected_authority_generation,
            "approved_requirement_hash": inputs["approved_requirement_hash"],
            "expected_candidate_identity": inputs["candidate_identity"],
            "predecessor_subject": inputs["predecessor_subject"],
            "distribution_identity": inputs["distribution_identity"],
            "authority_snapshot_ref": authority,
            "reason": reason,
        }
        raw = canonical(request)
        with transaction(controller.conn):
            previous = controller.conn.execute(
                "SELECT request,request_hash FROM execution_runtime_transitions WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if previous and (
                previous["request"] != raw or previous["request_hash"] != digest(raw.encode())
            ):
                raise ExecutionConflict("runtime recovery request ID reused with changed content")
            if not previous:
                now = utcnow()
                controller.conn.execute(
                    (
                        "INSERT INTO "
                        "execution_runtime_transitions(request_id,execution_id,request,requ"
                        "est_hash,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?)"
                    ),
                    (request_id, execution_id, raw, digest(raw.encode()), "PREPARING", now, now),
                )

        def persist(receipt):
            with transaction(controller.conn):
                require_active_epoch(
                    controller.conn,
                    execution_id,
                    expected_epoch_id=expected_epoch_id,
                    expected_authority_generation=expected_authority_generation,
                    mutation=True,
                )
                controller.conn.execute(
                    (
                        "UPDATE execution_runtime_transitions SET "
                        "state='PREPARED',preparation=?,updated_at=? WHERE request_id=?"
                    ),
                    (canonical(receipt), utcnow(), request_id),
                )

        channel = InitializationChannel(
            request,
            lambda notice: build_start_resources(controller, execution, notice["plan_ref"], notice),
            persist,
            directory / "runtime-preparations" / request_id,
        )
        receipt = channel.run(
            launcher, envelope["repository"], execution["host"], environment=execution_environment()
        )
        return {
            "state": "PREPARED",
            "request_id": request_id,
            "preparation_sha256": receipt["preparation_sha256"],
            "successor_session_id": receipt["successor_subject"]["session_id"],
            "successor_run_id": receipt["successor_run_id"],
            "native_resumed": False,
        }


def inspect_runtime(controller, execution_id, *, request_id):
    _request_id(request_id)
    row = controller.conn.execute(
        "SELECT * FROM execution_runtime_transitions WHERE execution_id=? AND request_id=?",
        (execution_id, request_id),
    ).fetchone()
    if not row:
        raise LookupError("runtime recovery request not found")
    receipt = json.loads(row["preparation"]) if row["preparation"] else None
    return {
        "state": row["state"],
        "request_id": request_id,
        "preparation_sha256": receipt["preparation_sha256"] if receipt else None,
        "native_resumed": False,
        "diagnostic": row["diagnostic"],
    }


def activate_runtime(
    controller,
    execution_id,
    *,
    request_id,
    preparation_sha256,
    expected_epoch_id,
    expected_authority_generation,
):
    from harness.execution_runtime_activation import activate_runtime as activate

    return activate(
        controller,
        execution_id,
        request_id=request_id,
        preparation_sha256=preparation_sha256,
        expected_epoch_id=expected_epoch_id,
        expected_authority_generation=expected_authority_generation,
    )


def recover_runtime_transition(controller, execution_id, *, request_id):
    row = controller.conn.execute(
        (
            "SELECT request,preparation FROM execution_runtime_transitions WHERE "
            "request_id=? AND execution_id=?"
        ),
        (request_id, execution_id),
    ).fetchone()
    if not row or not row["preparation"]:
        raise ExecutionConflict("recovery requires a completed immutable preparation")
    request, receipt = json.loads(row["request"]), json.loads(row["preparation"])
    return activate_runtime(
        controller,
        execution_id,
        request_id=request_id,
        preparation_sha256=receipt["preparation_sha256"],
        expected_epoch_id=request["expected_epoch_id"],
        expected_authority_generation=request["expected_authority_generation"],
    )
