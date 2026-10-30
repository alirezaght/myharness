"""Audited failed-writer recovery from recorded exited native invocations."""

import json
import time
from pathlib import Path

from harness.execution_bootstrap import execution_environment
from harness.execution_contract import canonical, digest
from harness.execution_handoff import durable_file
from harness.execution_hosts import al_argv, al_source, managed_launcher
from harness.execution_initialization import validate_existing_origin
from harness.execution_models import ExecutionConflict
from harness.execution_store import get_execution, process_alive
from harness.execution_worker import worker_lock
from harness.owned_subprocess import run_owned


def record_closed_context(execution, token, process, runtime):
    if getattr(runtime, "operations", None) is None:
        return
    records = list(runtime.operations.processes.records.values())
    if process.poll() is None or any(
        r["process"].poll() is None or r.get("thread") and r["thread"].is_alive() for r in records
    ):
        raise ExecutionConflict("native context teardown remains unsettled")
    observation = {
        "kind": "closed-native-context-v1",
        "execution_id": execution["id"],
        "epoch_id": execution["epoch_id"],
        "authority_generation": execution["authority_generation"],
        "token": token,
        "child_pid": process.pid,
        "native_returncode": process.returncode,
        "owned_processes_closed": True,
        "tool_transport_closed": True,
    }
    path = Path(execution["handoff_path"]).parent / "settlements" / ("native-" + token + ".json")
    durable_file(path, canonical(observation).encode())


def closed_context(execution, native):
    path = (
        Path(execution["handoff_path"]).parent
        / "settlements"
        / ("native-" + native["token"] + ".json")
    )
    if not path.exists():
        return False
    if path.is_symlink() or path.stat().st_nlink != 1 or path.stat().st_size > 65536:
        raise ExecutionConflict("invalid native teardown receipt")
    value = json.loads(path.read_bytes())
    if type(value.get("native_returncode")) is not int:
        raise ExecutionConflict("native teardown has no verified exit result")
    return value == {
        "kind": "closed-native-context-v1",
        "execution_id": execution["id"],
        "epoch_id": execution["epoch_id"],
        "authority_generation": execution["authority_generation"],
        "token": native["token"],
        "child_pid": native["child_pid"],
        "native_returncode": value.get("native_returncode"),
        "owned_processes_closed": True,
        "tool_transport_closed": True,
    }


def settle_exited_work(controller, execution_id):
    execution = get_execution(controller.conn, execution_id)
    with worker_lock(execution):
        execution = get_execution(controller.conn, execution_id)
        rows = controller.conn.execute(
            "SELECT * FROM execution_attempts WHERE execution_id=? ORDER BY rowid DESC",
            (execution_id,),
        ).fetchall()
        native = next((r for r in rows if r["mode"] != "reconnect" and r["child_pid"]), None)
        if not native or native["epoch_id"] != execution["epoch_id"]:
            return {"status": "NoExitedInvocation", "reconciled": []}
        if native["status"] not in {"Exited", "Reconciled"} or any(
            r["status"] not in {"Exited", "Reconciled", "PreSpawnFailed"}
            or process_alive(r["child_pid"])
            for r in rows
        ):
            raise ExecutionConflict(
                "native invocation is live or uncertain; cannot settle its work"
            )
        from harness.execution_authority import require_execution_authority

        envelope = require_execution_authority(controller, execution_id, operation="read")
        source = al_source(execution, envelope)
        state = json.loads(source.read_bytes())
        closed = closed_context(execution, native)
        candidates = [
            w
            for w in state.get("writers", {}).values()
            if w.get("agent") in state.get("integration", {}).get("agents", {})
            and (
                closed
                and w["state"] in {"ACTIVE", "UNRESOLVED"}
                or w["state"] == "UNRESOLVED"
                and w.get("outcome") in {"failed", "abandoned"}
                and state.get("integration", {})
                .get("agents", {})
                .get(w.get("agent"), {})
                .get("status")
                == "Completed"
            )
        ]
        if not candidates:
            return {"status": "Settled", "reconciled": []}
        if any(e.get("status") == "RUNNING" for e in state.get("evidence", {}).get("entries", [])):
            return {"status": "CheckOutcomeUnverified", "reconciled": []}
        launcher = managed_launcher()
        validate_existing_origin(execution, envelope, launcher)
        records = []
        for writer in candidates[:16]:
            observation = {
                "kind": "exited-native-writer-settlement-v1",
                "execution_id": execution_id,
                "epoch_id": execution["epoch_id"],
                "authority_generation": execution["authority_generation"],
                "native_id": execution["native_id"],
                "attempt_id": native["id"],
                "child_pid": native["child_pid"],
                "observed_attempt_status": native["status"],
                "source_sha256": digest(source.read_bytes()),
                "writer": writer["id"],
                "original_outcome": writer.get("outcome"),
                "actual_agent_status": state.get("integration", {})
                .get("agents", {})
                .get(writer.get("agent"), {})
                .get("status"),
                "closed_context_observed": closed,
                "verification_approval": False,
                "at": time.time(),
            }
            artifact = (
                Path(execution["handoff_path"]).parent
                / "settlements"
                / (digest(observation).split(":")[1] + ".json")
            )
            durable_file(artifact, canonical(observation).encode())
            result = run_owned(
                al_argv(
                    launcher,
                    execution["host"],
                    envelope["repository"],
                    execution["al_session_id"],
                    "writer",
                    "reconcile",
                    "--id",
                    writer["id"],
                    "--reason",
                    (
                        "Recorded native invocation exited; owning agent completed failed. "
                        "Preserve its "
                        "failure and verification obligations; settle lifecycle only."
                    ),
                    "--artifact",
                    str(artifact),
                    existing_run=True,
                ),
                cwd=envelope["repository"],
                env=execution_environment(),
                timeout=15,
            )
            if result.returncode:
                raise ExecutionConflict(
                    "audited failed-writer settlement refused; no completion asserted"
                )
            records.append(writer["id"])
            controller.workspace(execution["task_id"]).record_event(
                "execution.writer_settled", observation | {"artifact": str(artifact)}
            )
        return {"status": "Settled", "reconciled": records}
