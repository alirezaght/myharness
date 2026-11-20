"""Exact immutable grant bindings, consumed atomically and never restored."""

import json
import secrets
import time

from harness.db import transaction, utcnow
from harness.execution_contract import canonical, identity
from harness.execution_epoch_lock import epoch_action_lock
from harness.execution_epochs import require_active_epoch
from harness.execution_store import get_execution
from harness.policy.contracts import number, require
from harness.policy.provenance import approved_document, verify_record

FIELDS = {
    "id",
    "execution_id",
    "epoch_id",
    "generation",
    "principal_id",
    "role",
    "operation",
    "resources",
    "binding",
    "policy_digest",
    "maximum_uses",
    "maximum_units",
    "expires_at",
}


def record_action(conn, execution, capability_id, decision_id, action, generation):
    prior = conn.execute(
        "SELECT action_id,decision_ref FROM policy_capabili"
        "ty_actions WHERE capability_id=? AND action=? AND "
        "revocation_generation=?",
        (capability_id, action, generation),
    ).fetchone()
    if prior:
        require(prior["decision_ref"] == decision_id, "AUTHORITY_STALE")
        return prior["action_id"]
    action_id = secrets.token_hex(16)
    conn.execute(
        "INSERT INTO policy_capability_actions VALUES (?,?,?,?,?,?,?,?)",
        (
            action_id,
            capability_id,
            execution["id"],
            execution["epoch_id"],
            decision_id,
            action,
            generation,
            utcnow(),
        ),
    )
    from harness.reliability.timeline_store import link

    link(
        conn,
        execution,
        source_kind="policy",
        source_id=action_id,
        event_kind="policy.capability_" + action,
        provenance="policy_authority",
        reference_id=capability_id,
    )
    return action_id


def issue(controller, execution_id, decision_id, epoch, generation):
    execution = get_execution(controller.conn, execution_id)
    with epoch_action_lock(execution), transaction(controller.conn):
        require_active_epoch(
            controller.conn,
            execution_id,
            expected_epoch_id=epoch,
            expected_authority_generation=generation,
            mutation=True,
        )
        doc = approved_document(controller, execution_id, decision_id, "capability")
        require(isinstance(doc, dict) and set(doc) == FIELDS)
        for key in ("id", "execution_id", "epoch_id", "operation"):
            identity(doc[key])
        require(doc["principal_id"] is not None or doc["role"] is not None)
        for key in ("principal_id", "role"):
            if doc[key] is not None:
                identity(doc[key])
        number(doc["generation"])
        require(
            doc["execution_id"] == execution_id
            and doc["epoch_id"] == epoch
            and doc["generation"] == generation
        )
        require(
            isinstance(doc["resources"], list)
            and len(doc["resources"]) <= 64
            and all(isinstance(v, str) and len(v) <= 512 for v in doc["resources"])
        )
        number(doc["maximum_uses"], positive=True)
        number(doc["maximum_units"])
        require(
            type(doc["expires_at"]) is int
            and time.time() < doc["expires_at"] <= time.time() + 86400 * 30
        )
        require(doc["binding"] == canonical(execution["binding"]))
        prior = controller.conn.execute(
            "SELECT * FROM policy_capabilities WHERE id=?", (doc["id"],)
        ).fetchone()
        if prior:
            require(prior["document"] == canonical(doc) and prior["decision_ref"] == decision_id)
        else:
            from harness.policy.deadlines import CLOCK_ID

            controller.conn.execute(
                "INSERT INTO policy_capabilities(id,execution_id,epoch_id,generation,"
                "document,decision_ref,issued_wall,issued_monotonic,clock_id,last_wall) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    doc["id"],
                    execution_id,
                    epoch,
                    generation,
                    canonical(doc),
                    decision_id,
                    time.time(),
                    time.monotonic(),
                    CLOCK_ID,
                    time.time(),
                ),
            )
        action_id = record_action(controller.conn, execution, doc["id"], decision_id, "issued", 0)
        return {"capability_id": doc["id"], "action_ref": action_id}


def validate(conn, intent, digest, identifiers, *, consume=False):
    now = time.time()
    for identifier in identifiers:
        row = conn.execute("SELECT * FROM policy_capabilities WHERE id=?", (identifier,)).fetchone()
        require(row is not None and row["state"] == "active", "CAPABILITY_INVALID")
        verify_record(conn, row["decision_ref"])
        doc = json.loads(row["document"])
        expected = {
            "execution_id": intent.execution_id,
            "epoch_id": intent.epoch_id,
            "generation": intent.generation,
            "principal_id": intent.principal_id,
            "role": intent.role,
            "operation": intent.operation,
            "resources": list(intent.resources),
            "binding": intent.binding,
            "policy_digest": digest,
        }
        require(
            all(
                doc[key] == value or key in {"principal_id", "role"} and doc[key] is None
                for key, value in expected.items()
            ),
            "CAPABILITY_INVALID",
        )
        from harness.policy.deadlines import CLOCK_ID

        require(
            CLOCK_ID is not None and row["clock_id"] == CLOCK_ID and now >= row["last_wall"],
            "CLOCK_UNCERTAIN",
        )
        require(
            now < doc["expires_at"]
            and time.monotonic() - row["issued_monotonic"] < doc["expires_at"] - row["issued_wall"],
            "CAPABILITY_INVALID",
        )
        own_use = conn.execute(
            "SELECT 1 FROM policy_capability_uses WHERE attempt_id=? AND capability_id=?",
            (intent.id, identifier),
        ).fetchone()
        if own_use is None:
            require(
                row["uses"] < doc["maximum_uses"]
                and intent.units <= doc["maximum_units"] - row["units"],
                "CAPABILITY_INVALID",
            )
        conn.execute("UPDATE policy_capabilities SET last_wall=? WHERE id=?", (now, identifier))
        if consume:
            require(own_use is None, "CAPABILITY_INVALID")
            require(
                row["uses"] < doc["maximum_uses"]
                and intent.units <= doc["maximum_units"] - row["units"],
                "CAPABILITY_INVALID",
            )
            conn.execute(
                "UPDATE policy_capabilities SET uses=uses+1,units=units+? WHERE id=?",
                (intent.units, identifier),
            )
            conn.execute(
                "INSERT INTO policy_capability_uses VALUES (?,?,?)",
                (intent.id, identifier, intent.units),
            )


def revoke(controller, execution_id, decision_id, expected_generation, epoch, generation):
    execution = get_execution(controller.conn, execution_id)
    with epoch_action_lock(execution), transaction(controller.conn):
        require_active_epoch(
            controller.conn,
            execution_id,
            expected_epoch_id=epoch,
            expected_authority_generation=generation,
            mutation=True,
        )
        doc = approved_document(controller, execution_id, decision_id, "revoke")
        require(isinstance(doc, dict) and set(doc) == {"capability_id", "revocation_generation"})
        row = controller.conn.execute(
            "SELECT * FROM policy_capabilities WHERE id=? AND execution_id=?",
            (doc["capability_id"], execution_id),
        ).fetchone()
        require(
            row is not None
            and row["epoch_id"] == epoch
            and row["generation"] == generation
            and doc["revocation_generation"] == expected_generation,
            "CAPABILITY_INVALID",
        )
        if row["state"] == "revoked" and row["revocation_generation"] == expected_generation + 1:
            action_id = record_action(
                controller.conn,
                execution,
                row["id"],
                decision_id,
                "revoked",
                row["revocation_generation"],
            )
            return {
                "action_ref": action_id,
                "capability_id": row["id"],
                "revocation_generation": row["revocation_generation"],
            }
        require(row["revocation_generation"] == expected_generation, "AUTHORITY_STALE")
        controller.conn.execute(
            (
                "UPDATE policy_capabilities SET state='revoked',revocation_generation=rev"
                "ocation_generation+1 WHERE id=?"
            ),
            (row["id"],),
        )
        action_id = record_action(
            controller.conn, execution, row["id"], decision_id, "revoked", expected_generation + 1
        )
        return {
            "capability_id": row["id"],
            "revocation_generation": expected_generation + 1,
            "action_ref": action_id,
        }
