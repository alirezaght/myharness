"""Validate retained issuer preparation independently of a lost channel acknowledgment."""

import hashlib
import json
from pathlib import Path

from harness.execution_contract import digest
from harness.execution_models import ExecutionConflict
from harness.template_classification import _ordinary


def _read(path, expected=None):
    raw, mode = _ordinary(path, 32 * 1024 * 1024)
    if mode != 0o600 or expected is not None and hashlib.sha256(raw).hexdigest() != expected:
        raise ExecutionConflict("protected preparation bytes or permissions changed")
    return json.loads(raw)


def validate_preparation(controller, execution, request, observation):
    """Only a receipt rooted in the previously admitted issuer can close PREPARING."""
    row = controller.conn.execute(
        "SELECT identity FROM execution_initializations WHERE execution_id=? AND epoch_id=?",
        (execution["id"], request["expected_epoch_id"]),
    ).fetchone()
    if not row or not row["identity"]:
        raise ExecutionConflict("retained protected initialization identity unavailable")
    original = json.loads(row["identity"])
    storage = Path(original["initialization_path"]).parent.parent
    directory = storage / "successors" / request["request_id"]
    reference = observation.get("receipt_ref")
    if observation.get("state") != "PREPARED" or not isinstance(reference, dict):
        raise ExecutionConflict("protected preparation has not completed; retain its reservation")
    if set(reference) != {"stored_path", "sha256"} or reference["stored_path"] != str(
        directory / "receipt.json"
    ):
        raise ExecutionConflict("preparation receipt escaped the admitted issuer")
    receipt = _read(reference["stored_path"], reference["sha256"])
    plan = _read(directory / "plan.json", receipt["preparation_sha256"])
    journal = _read(directory / "journal.json")
    predecessor = _read(
        receipt["predecessor_binding_ref"]["stored_path"],
        receipt["predecessor_binding_ref"]["sha256"],
    )
    successor = receipt["successor_transaction"]
    subject = {k: successor[k] for k in ("repository_identity", "platform", "session_id")}
    if (
        receipt != observation.get("receipt")
        or receipt.get("kind") != "prepared-successor-v1"
        or receipt.get("state") != "PREPARED"
        or receipt["request_id"] != request["request_id"]
        or receipt["request_sha256"] != digest(request).split(":")[1]
        or plan["request"] != request
        or predecessor != plan["predecessor_transaction"]
        or predecessor["session_id"] != request["predecessor_subject"]["session_id"]
        or predecessor["run_id"] != request["predecessor_subject"]["run_id"]
        or predecessor["platform"] != execution["host"]
        or successor["repository_identity"] != predecessor["repository_identity"]
        or successor["platform"] != predecessor["platform"]
        or successor["status"] != "ACTIVE"
        or successor["session_id"] == predecessor["session_id"]
        or receipt["successor_subject"] != subject
        or successor["run_id"] != receipt["successor_run_id"]
        or plan["successor_transaction"] != dict(successor, status="RESERVED", initialization=None)
        or journal.get("receipt_ref") not in (None, reference)
        or receipt["candidate_identity"] != request["expected_candidate_identity"]
        or receipt["distribution_identity"] != request["distribution_identity"]
        or receipt.get("native_resumed") is not False
        or receipt.get("product_authority") is not False
    ):
        raise ExecutionConflict("protected preparation does not match the recorded request")
    for key, filename in (
        ("predecessor_binding_ref", "predecessor-binding.json"),
        ("lineage_ref", "lineage.json"),
        ("successor_state_ref", "successor-state.json"),
    ):
        ref = receipt[key]
        if ref["stored_path"] != str(directory / filename):
            raise ExecutionConflict("protected preparation artifact escaped its request")
        _read(ref["stored_path"], ref["sha256"])
    initial = successor["initialization"]
    if initial["stored_path"] != str(storage / "initializations" / initial["sha256"]):
        raise ExecutionConflict("successor initialization escaped the admitted issuer")
    _read(initial["stored_path"], initial["sha256"])
    return receipt
