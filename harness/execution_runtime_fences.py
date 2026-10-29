"""Independently validate protected issuer publications before SQLite selection."""

import hashlib
import json
from pathlib import Path

from harness.execution_contract import canonical, digest
from harness.execution_models import ExecutionConflict
from harness.template_classification import _ordinary


def _reference(reference):
    raw, mode = _ordinary(reference["stored_path"], 32 * 1024 * 1024)
    if mode != 0o600 or hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise ExecutionConflict("protected issuer reference changed")
    return json.loads(raw)


def validate_fence(activation, request):
    receipt, selector = activation["receipt"], activation["selector"]
    old = _reference(receipt["predecessor_binding_ref"])
    storage = Path(receipt["successor_transaction"]["initialization"]["stored_path"]).parent.parent
    directory = storage / "successors" / request["request_id"]
    expected_subject = {
        k: receipt["successor_transaction"][k]
        for k in ("repository_identity", "platform", "session_id")
    }
    selector_path = (
        storage / "successor-active" / (digest(expected_subject).split(":")[1] + ".json")
    )
    if (
        selector["kind"] != "active-successor-epoch-v1"
        or selector["schema_version"] != 1
        or selector["execution_id"] != request["execution_id"]
        or selector["epoch_id"] != "epoch-" + receipt["successor_run_id"]
        or selector["authority_generation"] != request["expected_authority_generation"] + 1
        or selector["successor_subject"] != expected_subject
        or selector["successor_transaction"] != receipt["successor_transaction"]
        or selector["storage"] != str(storage)
        or selector["request_id"] != request["request_id"]
        or selector["preparation_sha256"] != receipt["preparation_sha256"]
        or activation["selector_ref"]
        != {
            "stored_path": str(selector_path),
            "sha256": hashlib.sha256((canonical(selector) + "\n").encode()).hexdigest(),
        }
    ):
        raise ExecutionConflict("issuer selection identity differs from exact recovery request")
    raw, mode = _ordinary(directory / "activation-plan.json")
    activation_ref = {
        "stored_path": str(directory / "activation-plan.json"),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    if mode != 0o600 or raw != (canonical(activation) + "\n").encode():
        raise ExecutionConflict("issuer activation publication differs from channel")
    prior_subject = {k: old[k] for k in ("repository_identity", "platform", "session_id")}
    path = storage / "runs" / (digest(prior_subject).split(":")[1] + ".json")
    tombstone = json.loads(_ordinary(path)[0])
    expected = {
        "schema_version": 1,
        "kind": "superseded-run-binding-v1",
        "status": "SUPERSEDED",
        "repository_identity": old["repository_identity"],
        "platform": old["platform"],
        "session_id": old["session_id"],
        "run_id": old["run_id"],
        "predecessor_binding_ref": receipt["predecessor_binding_ref"],
        "successor_subject": receipt["successor_subject"],
        "activation_ref": activation_ref,
    }
    if tombstone != expected:
        raise ExecutionConflict("protected predecessor has not been irreversibly fenced")
