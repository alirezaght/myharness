"All applicable restrictions intersect, with inherited immutable parents included."

import json

from harness.policy.contracts import policy_digest, policy_document, require
from harness.policy.provenance import verify_record


def membership(conn, execution_id):
    row = conn.execute(
        "SELECT * FROM policy_memberships WHERE execution_id=?", (execution_id,)
    ).fetchone()
    return (
        dict(row)
        if row
        else {"execution_id": execution_id, "root_id": execution_id, "workflow_id": None}
    )


def select(conn, execution, principal, *, roles=None):
    member = membership(conn, execution["id"])
    applicable = {
        "global": "all",
        "project": "current",
        "workflow": member["workflow_id"],
        "role": principal["role"],
        "execution": execution["id"],
    }
    rows = {
        row["id"]: dict(row)
        for row in conn.execute(
            "SELECT v.* FROM policy_versions v JOIN policy_bindings b ON b.version_id=v.id"
        )
    }
    selected, visiting = {}, set()

    def visit(key):
        require(key not in visiting)
        if key not in rows:
            parent = conn.execute("SELECT * FROM policy_versions WHERE id=?", (key,)).fetchone()
            require(parent is not None)
            rows[key] = dict(parent)
        if key in selected:
            return
        visiting.add(key)
        row = rows[key]
        doc = policy_document(json.loads(row["document"]))
        verify_record(conn, row["decision_ref"])
        require(policy_digest([doc]) == row["digest"])
        for parent in doc["parents"]:
            visit(parent)
        visiting.remove(key)
        selected[key] = doc

    for key, row in list(rows.items()):
        if (
            row["scope_ref"] == applicable[row["scope_kind"]]
            or row["scope_kind"] == "role"
            and row["scope_ref"] in (roles or ())
        ):
            visit(key)
    docs = [selected[key] for key in sorted(selected)]
    return docs, policy_digest(docs)


def within(path, scopes):
    return any(scope == "." or path == scope or path.startswith(scope + "/") for scope in scopes)


def evaluate(documents, intent):
    approvals, pools = set(), set()
    for doc in documents:
        rules = doc["rules"]
        require(intent.operation not in rules.get("deny_tools", []), "POLICY_DENIED")
        require(
            "allow_tools" not in rules or intent.operation in rules["allow_tools"], "POLICY_DENIED"
        )
        approvals.update(rules.get("required_approvals", []))
        pools.update(limit["pool_id"] for limit in doc["limits"])
        key = "write_paths" if intent.mutating else "read_paths"
        if key in rules:
            require(bool(intent.resources), "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
            for resource in intent.resources:
                require(
                    resource.startswith("file:") and within(resource[5:], rules[key]),
                    "UNSUPPORTED_ENFORCEMENT_CAPABILITY"
                    if intent.operation not in {"apply_patch", "read_file"}
                    else "POLICY_DENIED",
                )
        if (
            intent.operation
            in {
                "run_command",
                "al_command",
                "environment_operation",
                "delivery_operation",
                "diagram_operation",
            }
            and "read_paths" in rules
        ):
            require("." in rules["read_paths"], "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
        # Existing subprocess sandbox is filesystem-only: it cannot prove these restrictions.
        if intent.operation in {
            "run_command",
            "al_command",
            "environment_operation",
            "delivery_operation",
            "diagram_operation",
        }:
            require(
                not any(rules.get(key) == "deny" for key in ("network", "database", "production")),
                "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
            )
        if intent.operation == "al_command" and any(
            resource == "control:delegation" for resource in intent.resources
        ):
            require(rules.get("delegation") != "deny", "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
    return sorted(approvals), sorted(pools)


def selected_ceilings(documents):
    ceilings = {}
    for document in documents:
        for limit in document["limits"]:
            key, maximum = limit["pool_id"], limit["maximum"]
            ceilings[key] = min(ceilings.get(key, maximum), maximum)
    return ceilings


def effective_pool(row, ceilings):
    pool = dict(row)
    pool["maximum"] = min(pool["maximum"], ceilings[pool["id"]])
    if pool["dimension"] == "execution_elapsed_ms":
        pool["deadline_wall"] = min(
            pool["deadline_wall"], pool["origin_wall"] + pool["maximum"] / 1000
        )
    return pool


def inherited_roles(operation, principal):
    from harness.policy.contracts import require

    roles, seen = {principal["role"]}, set()
    current = principal
    while current.get("parent_thread_id"):
        parent = current["parent_thread_id"]
        require(parent not in seen and len(seen) < 20, "AUTHORITY_STALE")
        seen.add(parent)
        if operation.envelope["host"] == "claude":
            require(parent == operation.registry.root, "AUTHORITY_STALE")
            roles.add("orchestrator")
            break
        from pathlib import Path

        current = json.loads((Path(operation.registry.directory) / (parent + ".json")).read_bytes())
        operation.registry.require_live(current)
        require(
            current["binding"] == operation.registry.binding
            and current["policy"] == operation.registry.policy,
            "AUTHORITY_STALE",
        )
        roles.add(current["role"])
    return roles
