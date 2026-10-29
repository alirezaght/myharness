"""Read-only fixed bridge: current sealed approval is the only execution authority."""

import argparse
import json
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
    controller,
    execution_id,
    *,
    binding=None,
    operation="admit",
    delivery=None,
    _transition_validation=False,
):
    from harness.execution_epochs import require_active_epoch

    execution = require_active_epoch(
        controller.conn, execution_id, protected=not _transition_validation
    )
    if execution["status"] in TERMINAL and operation not in {
        "read",
        "delivery",
        "delivery-correction",
    }:
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
    if operation == "delivery-correction":
        from harness.delivery_routing import validate_correction_authority

        validate_correction_authority(controller, execution, delivery)
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


def require_authored_report(execution, state, task_id, resource):
    from harness.execution_artifacts import output_reports

    reports = output_reports(Path(execution["handoff_path"]).parent / "operations/drafts")
    row = next((r for r in reports if r["resource"] == resource), None)
    agent = state["integration"]["agents"].get(row["owner"]) if row else None
    if (
        not agent
        or agent["task"] != task_id
        or agent["status"] != "Completed"
        or not agent.get("ended_at")
        or agent.get("yield_required")
        or agent["role"] != row["role"]
        or agent.get("latest_output") != resource
    ):
        raise ExecutionConflict("report lacks completed observed producer provenance")
    index = json.loads(
        (Path(execution["handoff_path"]).parent / "operations/drafts/index.json").read_bytes()
    )
    record = index["records"][row["artifact_id"]]
    if record["graph_revision"] != state["integration"]["graph_revision"]:
        raise ExecutionConflict("submitted report graph revision is stale")
    return row


def require_dependency_outputs(state, task_id, repository, execution=None, _seen=()):
    import os
    import stat

    from harness.execution_contract import dependency_ready
    from harness.execution_files import parent_descriptor

    def stable(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    if task_id in _seen:
        raise ExecutionConflict("cyclic dependency authority")
    integration = state["integration"]
    requirement = (
        state.get("implementation_readiness", {}).get("binding", {}).get("requirement_hash")
    )
    for dep in integration["tasks"][task_id]["dependencies"]:
        task = integration["tasks"][dep]
        if not dependency_ready(
            task, graph_revision=integration["graph_revision"], requirement_hash=requirement
        ):
            raise ExecutionConflict(
                "dependency has no current authored output or verified completion"
            )
        require_dependency_outputs(state, dep, repository, execution, (*_seen, task_id))
        verified = task["status"] == "Completed"
        receipt = (
            task.get("authored_output")
            if not verified
            else {
                "output_refs_and_hashes": task["completion_binding"]["outputs"],
                "producer_invocation_ids": [],
                "writer_ids": [],
            }
        )
        identity = (
            task["completion_binding"]["candidate_identity"]
            if verified
            else receipt["candidate_identity"]
        )
        expected = {
            key: digest(
                integration["tasks"][key].get("completion_binding")
                if integration["tasks"][key]["status"] == "Completed"
                else integration["tasks"][key].get("authored_output")
            )
            for key in task["dependencies"]
        }
        if identity["dependency_receipts"] != expected:
            raise ExecutionConflict("dependency producer receipt changed")
        for writer in state["writers"].values():
            if writer.get("task") == dep and writer["state"] in {"ACTIVE", "UNRESOLVED"}:
                raise ExecutionConflict("dependency has unsettled producer writer")
        for entry in state["evidence"]["entries"]:
            if (
                (entry["status"] == "RUNNING" or entry.get("interrupted"))
                and not entry.get("reconciliation")
                and any(
                    p == "." or p == q or p.startswith(q + "/") or q.startswith(p + "/")
                    for p in entry["scope"]
                    for q in task["files"]
                )
            ):
                raise ExecutionConflict("dependency has an unsettled check")
        for wid in receipt["writer_ids"]:
            writer = state["writers"].get(wid)
            if not writer or writer["state"] != "FINISHED" or writer.get("outcome") != "success":
                raise ExecutionConflict("dependency writer provenance differs")
        for aid in receipt["producer_invocation_ids"]:
            producer = integration["agents"].get(aid)
            if (
                not producer
                or producer["status"] != "Completed"
                or not producer.get("ended_at")
                or producer.get("yield_required")
                or producer["task"] != dep
            ):
                raise ExecutionConflict("dependency producer lifecycle differs")
        for output in receipt["output_refs_and_hashes"]:
            if output["kind"] == "report":
                if execution is None:
                    raise ExecutionConflict("report dependency requires execution authority")
                require_authored_report(execution, state, dep, output["resource"])
                continue
            with parent_descriptor(repository, output["path"]) as (directory, name):
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                try:
                    before = os.fstat(fd)
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                        raise ExecutionConflict("dependency output is not an ordinary file")
                    with os.fdopen(fd, "rb", closefd=False) as stream:
                        raw = stream.read()
                    after = os.fstat(fd)
                    named = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if (
                        stable(before) != stable(after)
                        or stable(named) != stable(after)
                        or digest(raw) != output["sha256"]
                        or stat.S_IMODE(before.st_mode) != output["mode"]
                    ):
                        raise ExecutionConflict("dependency authored bytes changed")
                finally:
                    os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", required=True)
    parser.add_argument(
        "--operation",
        required=True,
        choices=[
            "start",
            "admit",
            "launch",
            "resume",
            "decision",
            "revalidate",
            "approve",
            "delivery-correction",
            "authored-output",
        ],
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
            from harness.execution_hosts import al_source

            source = al_source(execution, read_handoff(execution["handoff_path"]))
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
            from harness.execution_lineage import scoped_amendments, validate_inherited_scope

            inherited = validate_inherited_scope(conn, execution, state, args.task)
            amendments = scoped_amendments(state, args.task)
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
                if amendment["id"] in inherited:
                    continue
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
        if args.operation == "authored-output":
            if not args.task:
                raise ExecutionConflict("authored report requires a task scope")
            require_authored_report(execution, state, args.task, args.delivery)
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
