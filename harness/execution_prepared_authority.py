"""Fixed, read-only prepared-start authority; it cannot activate an execution."""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from harness.controller import HarnessController
from harness.execution_authority import require_execution_authority
from harness.execution_contract import canonical, digest, loads
from harness.execution_handoff import read_handoff
from harness.execution_store import get_execution
from harness.template_classification import (
    _ordinary,
    prepared_context,
    validate_prepared_classification,
)


def validate_plan(controller, execution_id, context):
    plan = prepared_context(context)
    execution = get_execution(controller.conn, execution_id)
    envelope = require_execution_authority(controller, execution_id, operation="read")
    prior = context["predecessor"]
    if (
        execution["al_session_id"] != prior["session_id"]
        or execution["al_run_id"] != prior["run_id"]
        or envelope["repository"] != context["repository"]
        or execution["id"] != plan["request"]["execution_id"]
    ):
        raise ValueError("prepared-start execution identity differs")
    if execution["status"] not in {
        "Interrupted",
        "Unavailable",
        "RecoveryRequired",
        "Completed",
        "Failed",
    }:
        raise ValueError("prepared-start execution is not settled")
    attempts = controller.conn.execute(
        "SELECT status,child_pid FROM execution_attempts WHERE execution_id=?", (execution_id,)
    )
    from harness.execution_store import process_alive

    if any(
        row["status"] not in {"Exited", "Reconciled", "PreSpawnFailed"}
        or process_alive(row["child_pid"])
        for row in attempts
    ):
        raise ValueError("prepared-start native attempt is unsettled")
    raw, _ = _ordinary(plan["predecessor_state_ref"]["stored_path"], 16 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != plan["predecessor_state_ref"]["sha256"]:
        raise ValueError("prepared-start predecessor archive differs")
    state = loads(raw, max_bytes=16 * 1024 * 1024)
    if state.get("integration", {}).get("binding", {}).get("execution_id") != execution_id:
        raise ValueError("prepared-start source binding differs")
    scoped = {}
    for amendment in state["amendments"]:
        if not amendment.get("scoped"):
            continue
        row = controller.conn.execute(
            "SELECT delivery,registry_path,registry_hash FROM execution_decisions "
            "WHERE id=? AND execution_id=?",
            (amendment["id"], execution_id),
        ).fetchone()
        ack = state["integration"]["deliveries"].get(amendment["id"])
        if (
            not row
            or not row["delivery"]
            or not ack
            or digest(json.loads(row["delivery"])) != amendment["delivery_hash"]
            or ack["decision_hash"] != amendment["delivery_hash"]
            or digest(_ordinary(row["registry_path"])[0]) != row["registry_hash"]
            or amendment["registry"]["sha256"] != row["registry_hash"]
        ):
            raise ValueError("inherited amendment acceptance provenance differs")
        scoped[amendment["id"]] = amendment["artifact"]["sha256"]
    return execution, envelope, state, scoped


def validate_start(controller, execution_id, context, resources):
    execution, old, state, scoped = validate_plan(controller, execution_id, context)
    if set(resources) != {"handoff_ref", "package_ref", "classification_ref"}:
        raise ValueError("invalid prepared start resources")
    handoff_raw, _ = _ordinary(resources["handoff_ref"]["path"], 4 * 1024 * 1024)
    package_raw, _ = _ordinary(resources["package_ref"]["path"], 4 * 1024 * 1024)
    if (
        digest(handoff_raw) != resources["handoff_ref"]["sha256"]
        or digest(package_raw) != resources["package_ref"]["sha256"]
    ):
        raise ValueError("prepared start resources changed")
    handoff = read_handoff(resources["handoff_ref"]["path"], resources["handoff_ref"]["sha256"])
    expected = dict(
        old,
        al_session_id=context["successor"]["session_id"],
        runtime_layout="harness-session-directory-v1",
    )
    if handoff != expected or json.loads(package_raw) != old["task_package"]:
        raise ValueError("prepared handoff changed approved execution")
    classified = validate_prepared_classification(resources["classification_ref"], context)
    binding = dict(
        state["integration"]["binding"],
        handoff_hash=resources["handoff_ref"]["sha256"],
        al_session_id=context["successor"]["session_id"],
        al_run_id=context["successor"]["run_id"],
    )
    return {
        "authorized_start_binding": binding,
        "scoped_acceptance": scoped,
        "classified_paths": classified,
        "database": old["database"],
    }


def validate_selected(controller, execution_id, context, activation):
    plan = prepared_context(context)
    execution = get_execution(controller.conn, execution_id)
    selector = activation["selector"]
    receipt = activation["receipt"]
    if (
        activation["kind"] != "successor-activation-plan-v1"
        or activation["request_id"] != plan["request"]["request_id"]
        or receipt["preparation_sha256"] != context["preparation_identity"]
        or selector["execution_id"] != execution_id
        or selector["epoch_id"] != execution["epoch_id"]
        or selector["authority_generation"] != execution["authority_generation"]
        or selector["successor_transaction"] != receipt["successor_transaction"]
        or execution["al_session_id"] != context["successor"]["session_id"]
        or execution["al_run_id"] != context["successor"]["run_id"]
        or execution["handoff_path"] != receipt["successor_handoff_ref"]["path"]
        or execution["handoff_hash"] != receipt["successor_handoff_ref"]["sha256"]
    ):
        raise ValueError("activation database selection differs")
    epoch = controller.conn.execute(
        "SELECT state,identity FROM execution_epochs WHERE execution_id=? AND epoch_id=?",
        (execution_id, execution["epoch_id"]),
    ).fetchone()
    transition = controller.conn.execute(
        "SELECT state FROM execution_runtime_transitions WHERE request_id=? AND execution_id=?",
        (activation["request_id"], execution_id),
    ).fetchone()
    if (
        not epoch
        or epoch["state"] not in {"DB_SELECTED", "ACTIVE"}
        or not transition
        or transition["state"] not in {"DB_SELECTED", "ACTIVE"}
        or json.loads(epoch["identity"])["active_selector_ref"] != activation["selector_ref"]
    ):
        raise ValueError("activation durable journal differs")
    require_execution_authority(
        controller, execution_id, operation="read", _transition_validation=True
    )
    classified = validate_prepared_classification(receipt["prepared_classification_ref"], context)
    return {
        "classified_paths": classified,
        "database_selected": True,
        "epoch_id": execution["epoch_id"],
        "authority_generation": execution["authority_generation"],
    }


def validate_inputs(controller, execution_id, value):
    from harness.template_classification import selected_reference, validate_reference

    execution = get_execution(controller.conn, execution_id)
    envelope = require_execution_authority(controller, execution_id, operation="read")
    previous = value["predecessor"]
    if (
        set(value) != {"predecessor", "execution_id"}
        or value["execution_id"] != execution_id
        or previous["session_id"] != execution["al_session_id"]
        or previous["run_id"] != execution["al_run_id"]
        or previous["repository_identity"]["root"] != envelope["repository"]
    ):
        raise ValueError("preparation observation identity differs")
    storage = Path(previous["initialization"]["stored_path"]).parent.parent
    initial = {"pin_root": str(storage / "pins" / previous["pin"]["pin_identity"])}
    reference = selected_reference(execution, envelope, initial)
    return {"classified_paths": validate_reference(reference)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--context-file", required=True)
    parser.add_argument("--resources-file")
    parser.add_argument("--activation-file")
    parser.add_argument("--inputs-file")
    args = parser.parse_args()
    envelope = read_handoff(args.handoff)
    context = loads(_ordinary(args.context_file)[0], max_bytes=4 * 1024 * 1024)
    conn = sqlite3.connect(
        Path(envelope["database"]).resolve().as_uri() + "?mode=ro", uri=True, autocommit=True
    )
    conn.row_factory = sqlite3.Row
    try:
        controller = HarnessController(conn)
        if args.inputs_file:
            value = loads(_ordinary(args.inputs_file)[0])
            result = validate_inputs(controller, envelope["execution_id"], value)
        elif args.activation_file:
            activation = loads(_ordinary(args.activation_file)[0], max_bytes=4 * 1024 * 1024)
            result = validate_selected(controller, envelope["execution_id"], context, activation)
        elif args.resources_file:
            resources = loads(_ordinary(args.resources_file)[0])
            result = validate_start(controller, envelope["execution_id"], context, resources)
        else:
            _, _, _, scoped = validate_plan(controller, envelope["execution_id"], context)
            result = {"scoped_acceptance": scoped}
        print(canonical(result))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
