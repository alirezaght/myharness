"No PID inference, replay, stale callback exception or speculative capacity refund."

from harness.policy.contracts import require


def require_closed(conn, execution):
    row = conn.execute(
        (
            "SELECT 1 FROM policy_attempts WHERE execution_id=? AND legacy=0 AND state IN ('reserv"
            "ed','started','unresolved') LIMIT 1"
        ),
        (execution["id"],),
    ).fetchone()
    require(row is None, "ATTEMPT_NOT_EXECUTABLE")
    require(
        not conn.execute(
            "SELECT 1 FROM policy_processes p JOIN policy_attem"
            "pts a ON a.id=p.attempt_id WHERE a.execution_id=? AND a.legacy=0 "
            "AND p.boundary_closed=0 LIMIT 1",
            (execution["id"],),
        ).fetchone(),
        "ATTEMPT_NOT_EXECUTABLE",
    )


def native_admission(conn, execution):
    from harness.policy.native_presence import require_no_strict_consumers

    require_no_strict_consumers(conn)
    from harness.native_tool_policy import ROLES
    from harness.policy.composition import select

    for role in ("orchestrator", *ROLES):
        docs, _ = select(conn, execution, {"role": role})
        for doc in docs:
            require(doc["rules"].get("delegation") != "deny", "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
            require(
                not any(
                    limit["dimension"]
                    in {
                        "generation_tokens",
                        "monetary_cost",
                        "operation_deadline_ms",
                        "execution_elapsed_ms",
                    }
                    for limit in doc["limits"]
                ),
                "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
            )


def runtime_support(execution):
    import json

    from harness.execution_bridge import bridge_root, verify_snapshot

    root = bridge_root(execution)
    verify_snapshot(root)
    members = json.loads((root / "manifest.json").read_bytes())["files"]
    require(
        all(
            "policy/" + name in members
            for name in (
                "admission.py",
                "resources.py",
                "processes.py",
                "retry_channel.py",
                "claims.py",
                "domains.py",
                "resource_bindings.py",
                "control_qualification.py",
                "control_channel.py",
                "control_git.py",
                "control_boundary.py",
                "control_sources_installer.py",
                "control_sources_codex.py",
                "control_sources_claude.py",
            )
        ),
        "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
    )


def native_deadline(conn, execution):
    from harness.db import transaction
    from harness.execution_epoch_lock import epoch_action_lock
    from harness.native_tool_policy import ROLES
    from harness.policy.composition import effective_pool, select, selected_ceilings
    from harness.policy.deadlines import deadline

    if not conn.execute("SELECT 1 FROM policy_bindings LIMIT 1").fetchone():
        return None
    pools = set()
    documents = []
    for role in ("orchestrator", *ROLES):
        docs, _ = select(conn, execution, {"role": role})
        documents.extend(docs)
        for doc in docs:
            pools.update(
                limit["pool_id"]
                for limit in doc["limits"]
                if limit["dimension"] == "execution_elapsed_ms"
            )
    if not pools:
        return None
    with epoch_action_lock(execution), transaction(conn):
        rows = [
            effective_pool(
                conn.execute("SELECT * FROM policy_pools WHERE id=?", (key,)).fetchone(),
                selected_ceilings(documents),
            )
            for key in sorted(pools)
        ]
        bound, _ = deadline(conn, rows, 2**31)
    return bound
