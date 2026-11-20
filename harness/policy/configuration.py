"Operator actions consume exact developer-authorized documents, with revision CAS."

import json
import time

from harness.db import transaction
from harness.execution_contract import canonical
from harness.execution_epoch_lock import epoch_action_lock
from harness.execution_epochs import require_active_epoch
from harness.execution_store import get_execution
from harness.policy.composition import membership
from harness.policy.contracts import number, policy_digest, policy_document, require
from harness.policy.deadlines import CLOCK_ID, origin_receipt
from harness.policy.provenance import approved_document


def install(controller, execution_id, decision_id, expected_revision, epoch, generation):
    timing_origin = origin_receipt()
    execution = get_execution(controller.conn, execution_id)
    from pathlib import Path

    from harness.execution_handoff import read_handoff

    repository = str(
        Path(
            read_handoff(execution["handoff_path"], execution["handoff_hash"])["repository"]
        ).resolve()
    )
    with epoch_action_lock(execution), transaction(controller.conn):
        require_active_epoch(
            controller.conn,
            execution_id,
            expected_epoch_id=epoch,
            expected_authority_generation=generation,
            mutation=True,
        )
        doc = policy_document(approved_document(controller, execution_id, decision_id, "install"))
        require(
            not any(
                doc["rules"].get(key) == "deny"
                for key in ("network", "database", "production", "delegation")
            ),
            "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
        )
        require(
            not any(
                limit["dimension"] in {"generation_tokens", "monetary_cost"}
                for limit in doc["limits"]
            ),
            "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
        )
        require(
            doc["scope_kind"] != "execution" or doc["scope_ref"] == execution_id, "AUTHORITY_STALE"
        )
        conn = controller.conn
        if any(limit["dimension"] == "execution_elapsed_ms" for limit in doc["limits"]):
            require(
                not conn.execute(
                    "SELECT 1 FROM execution_attempts WHERE child_pid IS NOT NULL "
                    + ("AND execution_id=? " if doc["scope_kind"] == "execution" else "")
                    + "LIMIT 1",
                    (execution_id,) if doc["scope_kind"] == "execution" else (),
                ).fetchone(),
                "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
            )
        from harness.policy.reconciliation import runtime_support

        # Every execution affected by a broad activation must have actual immutable hooks.
        if doc["scope_kind"] in {"global", "project", "role", "workflow"}:
            for row in conn.execute("SELECT id FROM executions").fetchall():
                runtime_support(get_execution(conn, row["id"]))
        else:
            runtime_support(get_execution(conn, doc["scope_ref"]))
        from harness.policy.domains import enroll

        domain_resources = sorted(set(doc["rules"].get("write_paths", ["."])))
        enroll(conn, repository, domain_resources, decision_id)
        version_id = doc["policy_id"] + "-v" + str(doc["version"])
        prior = conn.execute("SELECT * FROM policy_versions WHERE id=?", (version_id,)).fetchone()
        old = conn.execute(
            "SELECT * FROM policy_bindings WHERE policy_id=?", (doc["policy_id"],)
        ).fetchone()
        if prior:
            require(prior["document"] == canonical(doc) and prior["decision_ref"] == decision_id)
            require(old and old["version_id"] == version_id)
            return {"version_id": version_id, "revision": old["revision"]}
        require((old["revision"] if old else 0) == number(expected_revision), "AUTHORITY_STALE")
        if old:
            previous = json.loads(
                conn.execute(
                    "SELECT document FROM policy_versions WHERE id=?", (old["version_id"],)
                ).fetchone()[0]
            )
            require(
                doc["version"] == previous["version"] + 1
                and doc["scope_kind"] == previous["scope_kind"]
                and doc["scope_ref"] == previous["scope_ref"]
            )
        else:
            require(doc["version"] == 1)
        for parent in doc["parents"]:
            require(
                parent != version_id
                and conn.execute("SELECT 1 FROM policy_versions WHERE id=?", (parent,)).fetchone()
            )
        member = membership(conn, execution_id)
        conn.execute(
            "INSERT INTO policy_versions VALUES (?,?,?,?,?,?,?,?)",
            (
                version_id,
                doc["policy_id"],
                doc["version"],
                doc["scope_kind"],
                doc["scope_ref"],
                canonical(doc),
                policy_digest([doc]),
                decision_id,
            ),
        )
        now = time.time()
        for limit in doc["limits"]:
            domain, owner = limit["domain"], limit["owner_ref"]
            require(
                domain != "execution_tree" or owner == member["root_id"],
                "UNSUPPORTED_ACCOUNTING_DOMAIN",
            )
            require(
                domain != "workflow_instance"
                or owner == member["workflow_id"]
                and conn.execute("SELECT 1 FROM policy_workflows WHERE id=?", (owner,)).fetchone(),
                "UNSUPPORTED_ACCOUNTING_DOMAIN",
            )
            pool = conn.execute(
                "SELECT * FROM policy_pools WHERE id=?", (limit["pool_id"],)
            ).fetchone()
            if pool:
                require(
                    (pool["dimension"], pool["domain"], pool["owner_ref"])
                    == (limit["dimension"], domain, owner)
                )
                owner = conn.execute(
                    "SELECT policy_id FROM policy_versions WHERE id=?", (pool["version_id"],)
                ).fetchone()
                require(owner is not None and owner[0] == doc["policy_id"])
                deadline = (
                    pool["origin_wall"] + limit["maximum"] / 1000
                    if limit["dimension"] == "execution_elapsed_ms"
                    else None
                )
                conn.execute(
                    (
                        "UPDATE policy_pools SET maximum=?,version_id=?,revision=revision+1,deadl"
                        "ine_wall=? WHERE id=?"
                    ),
                    (limit["maximum"], version_id, deadline, limit["pool_id"]),
                )
            else:
                elapsed = limit["dimension"] == "execution_elapsed_ms"
                receipt = timing_origin
                if elapsed and domain == "execution_tree":
                    row = conn.execute(
                        "SELECT wall,monotonic,clock_id FROM policy_origins WHERE execution_id=?",
                        (owner,),
                    ).fetchone()
                    require(row is not None, "CLOCK_UNCERTAIN")
                    receipt = dict(row)
                elif elapsed and domain == "workflow_instance":
                    row = conn.execute(
                        "SELECT wall,monotonic,clock_id FROM policy_workflows WHERE id=?", (owner,)
                    ).fetchone()
                    require(row is not None, "CLOCK_UNCERTAIN")
                    receipt = dict(row)
                if elapsed:
                    require(
                        CLOCK_ID is not None
                        and receipt["clock_id"] == CLOCK_ID
                        and now >= receipt["wall"],
                        "CLOCK_UNCERTAIN",
                    )
                conn.execute(
                    (
                        "INSERT INTO policy_pools(id,dimension,domain,owner_ref,maximum,version_i"
                        "d,origin_wall,deadline_wall,origin_monotonic,clock_id,last_wall) VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?)"
                    ),
                    (
                        limit["pool_id"],
                        limit["dimension"],
                        domain,
                        owner,
                        limit["maximum"],
                        version_id,
                        receipt["wall"] if elapsed else None,
                        receipt["wall"] + limit["maximum"] / 1000 if elapsed else None,
                        receipt["monotonic"] if elapsed else None,
                        receipt["clock_id"] if elapsed else None,
                        now if elapsed else None,
                    ),
                )
        revision = expected_revision + 1
        conn.execute(
            (
                "INSERT INTO policy_bindings VALUES (?,?,?) ON CONFLICT(policy_id) DO UPD"
                "ATE SET version_id=excluded.version_id,revision=excluded.revision"
            ),
            (doc["policy_id"], version_id, revision),
        )
        return {"version_id": version_id, "revision": revision}


def register_workflow(controller, execution_id, decision_id, epoch, generation):
    timing_origin = origin_receipt()
    execution = get_execution(controller.conn, execution_id)
    with epoch_action_lock(execution), transaction(controller.conn):
        require_active_epoch(
            controller.conn,
            execution_id,
            expected_epoch_id=epoch,
            expected_authority_generation=generation,
            mutation=True,
        )
        doc = approved_document(controller, execution_id, decision_id, "workflow")
        require(
            isinstance(doc, dict)
            and set(doc) == {"workflow_id", "execution_id"}
            and doc["execution_id"] == execution_id
        )
        from harness.execution_contract import identity

        identity(doc["workflow_id"])
        old = controller.conn.execute(
            "SELECT * FROM policy_memberships WHERE execution_id=?", (execution_id,)
        ).fetchone()
        if old:
            require(
                old["workflow_id"] == doc["workflow_id"] and old["root_id"] == execution_id,
                "AUTHORITY_STALE",
            )
        else:
            require(
                not controller.conn.execute(
                    "SELECT 1 FROM policy_attempts WHERE execution_id=?", (execution_id,)
                ).fetchone(),
                "AUTHORITY_STALE",
            )
            controller.conn.execute(
                "INSERT OR IGNORE INTO policy_workflows VALUES (?,?,?,?,?)",
                (
                    doc["workflow_id"],
                    decision_id,
                    timing_origin["wall"],
                    timing_origin["monotonic"],
                    timing_origin["clock_id"],
                ),
            )
            controller.conn.execute(
                "INSERT INTO policy_memberships VALUES (?,?,?)",
                (execution_id, execution_id, doc["workflow_id"]),
            )
            if controller.conn.execute(
                "SELECT 1 FROM execution_attempts WHERE execution_i"
                "d=? AND child_pid IS NOT NULL LIMIT 1",
                (execution_id,),
            ).fetchone():
                from harness.policy.reconciliation import native_admission

                native_admission(controller.conn, execution)
        return {"workflow_id": doc["workflow_id"], "execution_id": execution_id}
