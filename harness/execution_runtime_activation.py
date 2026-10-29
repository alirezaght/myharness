"""Coordinator half of an irreversible issuer-fenced execution epoch selection."""

import hashlib
import json
from pathlib import Path

from harness.execution_models import ExecutionConflict
from harness.execution_runtime_recovery import _locks, _request_id, _settled


def activate_runtime(
    controller,
    execution_id,
    *,
    request_id,
    preparation_sha256,
    expected_epoch_id,
    expected_authority_generation,
):
    import socket

    from harness.db import transaction, utcnow
    from harness.execution_bootstrap import authority_snapshot, execution_environment
    from harness.execution_contract import canonical
    from harness.execution_epochs import require_active_epoch
    from harness.execution_hosts import managed_launcher
    from harness.execution_initialization_channel import receive, send
    from harness.execution_store import get_execution
    from harness.owned_subprocess import run_owned
    from harness.template_classification import finalize_successor_classification

    _request_id(request_id)
    execution = get_execution(controller.conn, execution_id)
    with _locks(execution):
        _settled(controller, execution)
        row = controller.conn.execute(
            "SELECT * FROM execution_runtime_transitions WHERE execution_id=? AND request_id=?",
            (execution_id, request_id),
        ).fetchone()
        if not row or not row["preparation"]:
            raise ExecutionConflict("runtime preparation has not completed")
        receipt = json.loads(row["preparation"])
        original = json.loads(row["request"])
        if receipt["preparation_sha256"] != preparation_sha256 or (
            expected_epoch_id,
            expected_authority_generation,
        ) != (original["expected_epoch_id"], original["expected_authority_generation"]):
            raise ExecutionConflict("runtime activation preparation or expected identity differs")
        if row["state"] == "ACTIVE":
            require_active_epoch(controller.conn, execution_id)
            return json.loads(row["activation"])
        if row["state"] == "PREPARED":
            require_active_epoch(
                controller.conn,
                execution_id,
                expected_epoch_id=expected_epoch_id,
                expected_authority_generation=expected_authority_generation,
                mutation=True,
            )
        # The successor bridge and exact classification are private, initialized resources.
        authority_snapshot(Path(receipt["successor_handoff_ref"]["path"]).parent)
        finalize_successor_classification(execution, receipt)
        with transaction(controller.conn):
            controller.conn.execute(
                (
                    "UPDATE execution_runtime_transitions SET state='FENCING',updated_at=? "
                    "WHERE request_id=?"
                ),
                (utcnow(), request_id),
            )
        _settled(controller, get_execution(controller.conn, execution_id))
        parent, child = socket.socketpair()
        parent.settimeout(120)
        result = {}

        def service(process):
            child.close()
            send(
                parent,
                {
                    "version": 1,
                    "sequence": 1,
                    "operation": "activate",
                    "payload": {
                        "request_id": request_id,
                        "preparation_sha256": preparation_sha256,
                        "expected_epoch_id": expected_epoch_id,
                        "expected_authority_generation": expected_authority_generation,
                    },
                },
            )
            frame = receive(parent)
            if (
                set(frame) != {"version", "sequence", "phase", "payload"}
                or frame["version"] != 1
                or frame["sequence"] != 1
                or frame["phase"] != "fenced"
            ):
                raise ExecutionConflict("issuer activation fence response refused")
            activation = frame["payload"]
            if activation["receipt"] != receipt or activation["request_id"] != request_id:
                raise ExecutionConflict("issuer activation receipt differs")
            from harness.execution_runtime_fences import validate_fence

            validate_fence(activation, original)
            _select_database(controller, execution, activation)
            send(
                parent,
                {
                    "version": 1,
                    "sequence": 2,
                    "operation": "database-selected",
                    "payload": {
                        "activation_sha256": hashlib.sha256(
                            (canonical(activation) + "\n").encode()
                        ).hexdigest()
                    },
                },
            )
            active = receive(parent)
            if (
                active.get("version") != 1
                or active.get("sequence") != 2
                or active.get("phase") != "active"
            ):
                raise ExecutionConflict("issuer activation selection response refused")
            selector = activation["selector"]
            expected = {
                "state": "ACTIVE",
                "epoch_id": selector["epoch_id"],
                "authority_generation": selector["authority_generation"],
                "active_selector_ref": activation["selector_ref"],
                "native_resumed": False,
            }
            if active["payload"] != expected:
                raise ExecutionConflict("issuer activation acknowledgment identity differs")
            result.update(expected)

        envelope = __import__("harness.execution_handoff", fromlist=["read_handoff"]).read_handoff(
            execution["handoff_path"]
        )
        try:
            completed = run_owned(
                [
                    managed_launcher(),
                    "runtime-successor",
                    "activate",
                    "--repository",
                    envelope["repository"],
                    "--platform",
                    execution["host"],
                    "--request-id",
                    request_id,
                    "--coordinator-fd",
                    str(child.fileno()),
                ],
                cwd=envelope["repository"],
                env=execution_environment(),
                timeout=150,
                pass_fds=(child.fileno(),),
                parent_service=service,
            )
            if completed.returncode or result.get("state") != "ACTIVE":
                raise ExecutionConflict(
                    "runtime activation interrupted; roll forward the same recorded request"
                )
            with transaction(controller.conn):
                controller.conn.execute(
                    (
                        "UPDATE execution_epochs SET state='ACTIVE' WHERE execution_id=? "
                        "AND epoch_id=?"
                    ),
                    (execution_id, result["epoch_id"]),
                )
                controller.conn.execute(
                    (
                        "UPDATE execution_runtime_transitions SET "
                        "state='ACTIVE',activation=?,updated_at=? WHERE request_id=?"
                    ),
                    (canonical(result), utcnow(), request_id),
                )
            return result
        finally:
            parent.close()
            child.close()


def _select_database(controller, previous, activation):
    from harness.db import transaction, utcnow
    from harness.execution_contract import canonical
    from harness.execution_store import get_execution

    selector, receipt = activation["selector"], activation["receipt"]
    identity = {
        "al_session_id": receipt["successor_subject"]["session_id"],
        "al_run_id": receipt["successor_run_id"],
        "handoff_path": receipt["successor_handoff_ref"]["path"],
        "handoff_hash": receipt["successor_handoff_ref"]["sha256"],
        "active_selector_ref": activation["selector_ref"],
        "preparation": receipt,
        "lineage_ref": receipt["lineage_ref"],
    }
    with transaction(controller.conn):
        current = get_execution(controller.conn, previous["id"])
        if current["epoch_id"] == selector["epoch_id"]:
            existing = controller.conn.execute(
                "SELECT identity FROM execution_epochs WHERE execution_id=? AND epoch_id=?",
                (current["id"], current["epoch_id"]),
            ).fetchone()
            if json.loads(existing["identity"]) != identity:
                raise ExecutionConflict("selected epoch identity differs from recovery journal")
            controller.conn.execute(
                (
                    "UPDATE execution_runtime_transitions SET "
                    "state='DB_SELECTED',updated_at=? WHERE request_id=?"
                ),
                (utcnow(), activation["request_id"]),
            )
            return
        if (current["epoch_id"], current["authority_generation"]) != (
            previous["epoch_id"],
            previous["authority_generation"],
        ):
            raise ExecutionConflict("stale_epoch: database selection changed")
        controller.conn.execute(
            "UPDATE execution_epochs SET cursor=?,state=? WHERE execution_id=? AND epoch_id=?",
            (current["cursor"], "ARCHIVED", current["id"], current["epoch_id"]),
        )
        controller.conn.execute(
            (
                "INSERT INTO "
                "execution_epochs(execution_id,epoch_id,authority_generation,state,identity"
                ",created_at) VALUES (?,?,?,?,?,?)"
            ),
            (
                current["id"],
                selector["epoch_id"],
                selector["authority_generation"],
                "DB_SELECTED",
                canonical(identity),
                utcnow(),
            ),
        )
        controller.conn.execute(
            (
                "UPDATE executions SET "
                "epoch_id=?,authority_generation=?,al_session_id=?,al_run_id=?,handoff_path"
                "=?,handoff_hash=?,cursor=0,status=?,worker_token=NULL,error=NULL,result=NU"
                "LL WHERE id=? AND epoch_id=? AND authority_generation=?"
            ),
            (
                selector["epoch_id"],
                selector["authority_generation"],
                identity["al_session_id"],
                identity["al_run_id"],
                identity["handoff_path"],
                identity["handoff_hash"],
                "Interrupted",
                current["id"],
                current["epoch_id"],
                current["authority_generation"],
            ),
        )
        controller.conn.execute(
            (
                "UPDATE execution_runtime_transitions SET state='DB_SELECTED',updated_at=? "
                "WHERE request_id=?"
            ),
            (utcnow(), activation["request_id"]),
        )
