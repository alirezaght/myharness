"""Read-only fixed bridge: current sealed approval is the only execution authority."""

import argparse
import sqlite3
from pathlib import Path

from harness.approval import require_approval
from harness.cli_spec import spec_store
from harness.controller import HarnessController
from harness.execution_contract import canonical, digest, loads, validate_binding
from harness.execution_handoff import read_handoff, same_path
from harness.execution_models import TERMINAL, ExecutionConflict
from harness.execution_store import get_execution
from harness.testplan import TestPlanner


def require_execution_authority(
    controller, execution_id, *, binding=None, operation="admit", delivery=None
):
    execution = get_execution(controller.conn, execution_id)
    if execution["status"] in TERMINAL:
        raise ExecutionConflict("terminal execution is read-only")
    envelope = read_handoff(execution["handoff_path"], execution["handoff_hash"])
    if not same_path(
        envelope["database"], controller.conn.execute("PRAGMA database_list").fetchone()["file"]
    ):
        raise ExecutionConflict("handoff database identity mismatch")
    if (
        envelope["execution_id"] != execution_id
        or envelope["task_id"] != execution["task_id"]
        or envelope["host"] != execution["host"]
        or envelope["al_session_id"] != execution["al_session_id"]
    ):
        raise ExecutionConflict("handoff identity mismatch")
    store = spec_store(controller, execution["task_id"], envelope["specs"])
    approval = require_approval(
        controller, execution["task_id"], store, Path(envelope["repository"])
    )
    expected = {
        "execution_id": execution_id,
        "task_id": execution["task_id"],
        "approval_id": approval.id,
        **{
            k: getattr(approval, k)
            for k in ("spec_version", "content_hash", "revision", "review_state_hash")
        },
    }
    if expected != execution["binding"]:
        raise ExecutionConflict("original execution approval is superseded")
    if (
        Path(approval.path).read_bytes() != envelope["sealed_bytes"].encode()
        or digest(Path(approval.path).read_bytes()) != envelope["sealed_hash"]
    ):
        raise ExecutionConflict("sealed specification bytes changed")
    plan = TestPlanner(controller, execution["task_id"]).document()
    if (
        not plan.review.current
        or plan.to_dict() != envelope["plan"]
        or plan.render() != envelope["plan_text"]
    ):
        raise ExecutionConflict("approved test plan changed")
    if binding is not None:
        validate_binding(binding)
        immutable = expected | {
            "handoff_hash": execution["handoff_hash"],
            "al_session_id": execution["al_session_id"],
            "al_run_id": execution["al_run_id"] or binding["al_run_id"],
        }
        if binding != immutable:
            raise ExecutionConflict("foreign authority request binding")
        if execution["al_run_id"] is None and operation != "start":
            raise ExecutionConflict("AL source not yet bound by worker")
    if operation == "decision":
        row = controller.conn.execute(
            "SELECT * FROM execution_decisions WHERE id=? AND execution_id=?",
            (delivery, execution_id),
        ).fetchone()
        if row is None or row["delivery"] is None:
            raise ExecutionConflict("unknown developer delivery")
        path = Path(execution["handoff_path"]).parent / "inbox" / (row["id"] + ".json")
        if (
            path.read_bytes() != row["delivery"].encode()
            or digest(Path(row["registry_path"]).read_bytes()) != row["registry_hash"]
        ):
            raise ExecutionConflict("decision inbox/Registry bytes changed")
    # All materialized decisions remain immutable, including acknowledged amendments.
    for row in controller.conn.execute(
        "SELECT registry_path,registry_hash FROM execution_decisions "
        "WHERE execution_id=? AND registry_hash IS NOT NULL AND state != 'Intent'",
        (execution_id,),
    ):
        if digest(Path(row["registry_path"]).read_bytes()) != row["registry_hash"]:
            raise ExecutionConflict("execution Decision Registry provenance changed")
    return envelope


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", required=True)
    parser.add_argument(
        "--operation",
        required=True,
        choices=["start", "admit", "launch", "resume", "decision", "revalidate", "approve"],
    )
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--binding", required=True)
    parser.add_argument("--task")
    parser.add_argument("--delivery")
    parser.add_argument("--scope-revision")
    args = parser.parse_args()
    untrusted = read_handoff(args.handoff)
    db = Path(untrusted["database"])
    if not db.is_file():
        raise ExecutionConflict("authority database unavailable")
    conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, autocommit=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        execution = get_execution(conn, untrusted["execution_id"])
        if not same_path(execution["handoff_path"], args.handoff):
            raise ExecutionConflict("unrecognized authority handoff")
        binding = loads(args.binding)
        require_execution_authority(
            HarnessController(conn),
            execution["id"],
            binding=binding,
            operation=args.operation,
            delivery=args.delivery,
        )
        if args.task:
            source = (
                Path(execution["handoff_path"]).parent
                / "al-runtime"
                / (execution["al_session_id"] + ".json")
            )
            if source.stat().st_size > 32 * 1024 * 1024:
                raise ExecutionConflict("authority source exceeds limit")
            import json

            state = json.loads(source.read_bytes())
            integration = state.get("integration", {})
            if integration.get("binding") != binding:
                raise ExecutionConflict("authority scope source identity mismatch")
            task = integration.get("tasks", {}).get(args.task)
            if task is None:
                raise ExecutionConflict("authority task has not been committed")
            amendments = [
                a for a in state["amendments"] if args.task in a.get("affected_tasks", [])
            ]
            expected_revision = digest(
                {
                    "task": args.task,
                    "revision": task["revision"],
                    "graph_revision": integration["graph_revision"],
                    "amendments": [a["artifact"]["sha256"] for a in amendments],
                }
            )
            if args.scope_revision != expected_revision:
                raise ExecutionConflict("stale task/scope authority revision")
            for amendment in amendments:
                decision = conn.execute(
                    "SELECT delivery FROM execution_decisions WHERE id=? AND execution_id=?",
                    (amendment["id"], execution["id"]),
                ).fetchone()
                ack = integration.get("deliveries", {}).get(amendment["id"])
                if (
                    not decision
                    or not decision["delivery"]
                    or digest(json.loads(decision["delivery"])) != amendment["delivery_hash"]
                    or not ack
                    or ack["decision_hash"] != amendment["delivery_hash"]
                ):
                    raise ExecutionConflict(
                        "scoped authority lacks its exact acknowledged developer amendment"
                    )
        print(
            canonical(
                {
                    "allowed": True,
                    "nonce": args.nonce,
                    "operation": args.operation,
                    "binding": binding,
                    "task": args.task,
                    "delivery": args.delivery,
                    "scope_revision": args.scope_revision,
                }
            )
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
