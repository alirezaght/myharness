"Only structured protected developer deliveries authorize configuration or grants."

import json
from pathlib import Path

from harness.execution_authority import require_execution_authority
from harness.execution_contract import canonical, digest
from harness.execution_decisions import _registry_provenance, _request
from harness.execution_store import get_execution
from harness.policy.contracts import require


def verify_record(conn, decision_id):
    row = conn.execute("SELECT * FROM execution_decisions WHERE id=?", (decision_id,)).fetchone()
    require(row is not None, "APPROVAL_REQUIRED")
    row = dict(row)
    request = _request(row)
    require(request["verdict"] == "accept" and row["delivery"] is not None, "APPROVAL_REQUIRED")
    from harness.execution_handoff import read_handoff

    execution = get_execution(conn, row["execution_id"])
    envelope = read_handoff(execution["handoff_path"], execution["handoff_hash"])
    require(_registry_provenance(row, envelope) is not None, "APPROVAL_REQUIRED")
    from harness.decisions import parse_decision

    parsed = parse_decision(Path(row["registry_path"]))
    require(
        parsed.status == "approved"
        and parsed.decision == "Explicit developer accept: " + request["text"],
        "APPROVAL_REQUIRED",
    )
    delivery = json.loads(row["delivery"])
    require(
        delivery["text"] == request["text"] and delivery["verdict"] == "accept", "APPROVAL_REQUIRED"
    )
    return row, delivery


def approved_document(controller, execution_id, decision_id, action):
    from harness.execution_hosts import al_source

    execution = get_execution(controller.conn, execution_id)
    envelope = require_execution_authority(controller, execution_id, operation="read")
    row, delivery = verify_record(controller.conn, decision_id)
    require(
        row["execution_id"] == execution_id and row["epoch_id"] == execution["epoch_id"],
        "APPROVAL_REQUIRED",
    )
    state = json.loads(al_source(execution, envelope).read_bytes())
    ack = state.get("integration", {}).get("deliveries", {}).get(decision_id)
    require(
        ack
        and ack["decision_hash"] == digest(delivery)
        and row["state"] in {"Resolved", "RevalidationRequired"},
        "APPROVAL_REQUIRED",
    )
    require(
        delivery["binding"]
        == execution["binding"]
        | {
            "handoff_hash": execution["handoff_hash"],
            "al_session_id": execution["al_session_id"],
            "al_run_id": execution["al_run_id"],
        },
        "APPROVAL_REQUIRED",
    )
    try:
        value = json.loads(delivery["text"])
    except (ValueError, TypeError):
        require(False, "APPROVAL_REQUIRED")
    require(
        isinstance(value, dict)
        and set(value) == {"schema_version", "kind", "action", "binding", "document"},
        "APPROVAL_REQUIRED",
    )
    require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["kind"] == "runtime-policy-authorization"
        and value["action"] == action
        and value["binding"] == delivery["binding"],
        "APPROVAL_REQUIRED",
    )
    require(canonical(value) == delivery["text"], "APPROVAL_REQUIRED")
    return value["document"]
