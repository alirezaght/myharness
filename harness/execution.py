"""Reserve one execution per exact current approval, then request detached work."""

import secrets
from dataclasses import asdict
from pathlib import Path

from harness.approval import require_approval
from harness.cli_commands import task_repo_root
from harness.cli_spec import spec_store
from harness.db import default_db_path, transaction
from harness.execution_contract import MAX_HANDOFF, canonical, digest
from harness.execution_handoff import publish
from harness.execution_models import ExecutionConflict
from harness.execution_projection import execution_view
from harness.execution_store import get_execution, reserve_execution
from harness.spec_record import parse_spec
from harness.testplan import TestPlanner


def database_path(conn):
    name = conn.execute("PRAGMA database_list").fetchone()["file"]
    if not name:
        raise ValueError("execution requires durable file-backed SQLite")
    return Path(name).resolve()


def approval_binding(task_id, approval):
    return {
        "task_id": task_id,
        "approval_id": approval.id,
        **{
            key: value
            for key, value in asdict(approval).items()
            if key in {"spec_version", "content_hash", "revision", "review_state_hash"}
        },
    }


def _override(readiness_result):
    for check in readiness_result["checks"]:
        if check["id"] == "native-host-and-al-protocol" and "override" in check:
            return check["override"]
    return None


def _record_override(controller, task_id, execution_id, directory, record):
    """Store an explicit developer host-version override as execution evidence."""
    from harness.native_tool_policy import record_override

    record_override(directory, record)
    controller.workspace(task_id).record_event(
        "execution.host_version_override",
        {"execution_id": execution_id, **record},
    )


def start_execution(
    controller,
    task_id,
    *,
    host="claude",
    specs=None,
    launch=True,
    expected_approval_binding=None,
    accept_unverified_host_version=False,
):
    if host not in {"claude", "codex"}:
        raise ValueError("host must be claude or codex")
    conn = controller.conn
    root = task_repo_root(controller, task_id).resolve()
    store = spec_store(controller, task_id, specs)
    override = None
    with transaction(conn):
        approval = require_approval(controller, task_id, store, root)
        plan = TestPlanner(controller, task_id).document()
        if not plan.review.current:
            raise ExecutionConflict("current approved test plan is required")
        current_binding = approval_binding(task_id, approval)
        if expected_approval_binding is not None and expected_approval_binding != current_binding:
            raise ExecutionConflict(
                "The displayed approval changed. Reload and explicitly start again."
            )
        binding = {"execution_id": "", **current_binding}
        old = conn.execute(
            "SELECT id FROM executions WHERE approval_id=?", (approval.id,)
        ).fetchone()
        if old:
            saved = get_execution(conn, old["id"])
            binding["execution_id"] = saved["id"]
            if saved["host"] != host or saved["binding"] != binding:
                raise ExecutionConflict("approval already reserved with another host/binding")
            created = False
            if launch and saved["status"] == "Reserved":
                from harness.launch_readiness import LaunchReadinessError, require_launch_ready
                from harness.native_tool_policy import recorded_override

                directory = Path(saved["handoff_path"]).parent
                try:
                    override = _override(
                        require_launch_ready(
                            root,
                            host,
                            accept_unverified_host_version=accept_unverified_host_version
                            or recorded_override(directory),
                        )
                    )
                except LaunchReadinessError as error:
                    raise LaunchReadinessError(str(error), execution_id=saved["id"]) from error
                if override:
                    _record_override(controller, task_id, saved["id"], directory, override)
        else:
            if launch:
                from harness.launch_readiness import require_launch_ready

                override = _override(
                    require_launch_ready(
                        root,
                        host,
                        accept_unverified_host_version=accept_unverified_host_version,
                    )
                )
            eid = "exec-" + secrets.token_hex(12)
            binding["execution_id"] = eid
            # Uses the existing per-repository private state root, regardless of DB placement.
            runtime = (default_db_path(root).parent / "executions" / eid).resolve()
            if root == runtime or root in runtime.parents:
                raise ExecutionConflict("private execution runtime must be outside Git")
            sealed = Path(approval.path).read_bytes()
            body = parse_spec(Path(approval.path)).body
            package = {
                "original_request": controller.get_task(task_id).request,
                "accepted_spec": body,
                "acceptance": {
                    "text": f"Explicit developer approval #{approval.id}",
                    "source": "harness immutable approval",
                },
                "constraints": [
                    "Preserve approved specification and test plan",
                    "Use existing AL guards",
                ],
                "ticket": {"present": False, "reason": "not supplied"},
                "criteria": ["AC-01"],
                "invariants": [],
            }
            from harness.requirement_registry import registry

            package.update(registry(controller, task_id, body))
            from harness.complexity_assessment import current

            risk = current(controller, task_id)
            if risk:
                if not risk["current"]:
                    raise ValueError(
                        "implementation risk assessment is stale; reassess before handoff"
                    )
                package["implementation_risk"] = risk
            task = controller.get_task(task_id)
            if risk and task.complexity_source == "developer":
                package["routing_override"] = {
                    "source": "explicit developer classification",
                    "classification": {
                        "trivial": "SMALL",
                        "simple": "SMALL",
                        "standard": "STANDARD",
                        "complex": "COMPLEX",
                    }[task.complexity],
                }
            envelope = {
                "schema_version": 1,
                "runtime_layout": "harness-durable-epoch-v1",
                "execution_id": eid,
                "task_id": task_id,
                "approval": {**binding, "path": approval.path},
                "repository": str(root),
                "database": str(database_path(conn)),
                "specs": str(store.directory.parent),
                "host": host,
                "al_session_id": "harness-" + secrets.token_hex(12),
                "sealed_bytes": sealed.decode("utf-8"),
                "sealed_hash": digest(sealed),
                "plan": plan.to_dict(),
                "plan_text": plan.render(),
                "task_package": package,
            }
            if len(canonical(envelope).encode()) > MAX_HANDOFF:
                raise ValueError("approved execution handoff exceeds the 4 MiB limit")
            handoff = runtime / "handoff.json"
            from harness.execution_bootstrap import authority_snapshot

            authority_snapshot(runtime)
            from harness.execution_bootstrap import prepared_authority_snapshot

            envelope["runtime_authority_ref"] = prepared_authority_snapshot(runtime)
            hh = publish(handoff, envelope)
            publish(runtime / "task-package.json", package)
            saved, created = reserve_execution(
                conn,
                execution_id=eid,
                task_id=task_id,
                approval_id=approval.id,
                host=host,
                binding=binding,
                handoff_path=handoff,
                handoff_hash=hh,
                al_session_id=envelope["al_session_id"],
            )
            controller.workspace(task_id).record_event(
                "execution.reserved",
                {"execution_id": eid, "approval_id": approval.id, "host": host},
            )
            if override:
                _record_override(controller, task_id, eid, runtime, override)
    outcome = {"status": saved["status"]}
    if launch and saved["status"] == "Reserved":
        from harness.execution_worker import launch_worker

        outcome = launch_worker(conn, saved["id"])
    return {"created": created, "execution": execution_view(conn, saved["id"]), "launch": outcome}
