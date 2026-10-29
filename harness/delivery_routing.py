"""Persist one bounded correction admission per exact failed candidate/pipeline."""

import subprocess
import uuid
from pathlib import Path

from harness.db import transaction
from harness.delivery_store import delivery_view, get_delivery, save_snapshot
from harness.execution_authority import require_execution_authority
from harness.execution_contract import canonical, digest, loads
from harness.execution_hosts import al_argv, installed_package
from harness.execution_store import get_execution, process_alive


def route_failure(controller, execution_id, pipeline, jobs):
    require_execution_authority(controller, execution_id, operation="delivery")
    delivery = get_delivery(controller.conn, execution_id)
    if pipeline.get("sha") != delivery["candidate_sha"] or pipeline.get("status") != "failed":
        raise ValueError("correction requires exact failed candidate")
    failed = [j for j in jobs if j.get("status") == "failed" and not j.get("allow_failure")]
    known = {
        "script_failure",
        "runner_system_failure",
        "stuck_or_timeout_failure",
        "scheduler_failure",
        "api_failure",
    }
    if not failed or any(j.get("failure_reason") not in known for j in failed):
        snapshot = delivery["snapshot"] | {
            "blockers": ["Developer review required: failure classification unresolved"]
        }
        save_snapshot(controller, execution_id, snapshot, "DeveloperReview")
        return delivery_view(controller.conn, execution_id)
    row = controller.conn.execute(
        "SELECT id FROM delivery_corrections WHERE execution_id=? "
        "AND candidate_sha=? AND pipeline_id=?",
        (execution_id, pipeline["sha"], pipeline["id"]),
    ).fetchone()
    if row:
        return admit_delivery_correction(controller, row["id"])
    execution = get_execution(controller.conn, execution_id)
    if execution["status"] != "Completed":
        return delivery_view(controller.conn, execution_id)
    request_id = "correction-" + uuid.uuid4().hex
    request = {
        "schema_version": 1,
        "id": request_id,
        "binding": execution["binding"]
        | {
            "handoff_hash": execution["handoff_hash"],
            "al_session_id": execution["al_session_id"],
            "al_run_id": execution["al_run_id"],
        },
        "generation": delivery["generation"] + 1,
        "candidate_sha": pipeline["sha"],
        "pipeline_id": pipeline["id"],
        "prior_completion_digest": digest(execution["result"]),
        "observation_digest": digest(delivery["snapshot"]),
        "observation": delivery["snapshot"],
        "tasks": [],
    }
    from harness.execution_store import entities

    request["tasks"] = [t["id"] for t in entities(controller.conn, execution_id, "task")]
    if not request["tasks"]:
        raise ValueError("correction requires registered affected AL tasks")
    with transaction(controller.conn):
        current = get_delivery(controller.conn, execution_id)
        winner = controller.conn.execute(
            "SELECT id FROM delivery_corrections WHERE execution_id=? "
            "AND candidate_sha=? AND pipeline_id=?",
            (execution_id, pipeline["sha"], pipeline["id"]),
        ).fetchone()
        if winner:
            request_id = winner["id"]
        elif (
            current["generation"] != delivery["generation"]
            or current["candidate_sha"] != pipeline["sha"]
        ):
            raise ValueError("delivery generation/candidate changed before correction intent")
        elif current["retries"] >= current["config"]["retry_limit"]:
            controller.conn.execute(
                "UPDATE deliveries SET status='Blocked',snapshot=? WHERE execution_id=?",
                (
                    canonical(
                        current["snapshot"]
                        | {
                            "blockers": [
                                *current["snapshot"].get("blockers", []),
                                "Technical correction retry limit reached.",
                            ]
                        }
                    ),
                    execution_id,
                ),
            )
            return delivery_view(controller.conn, execution_id)
        else:
            controller.conn.execute(
                (
                    "INSERT INTO "
                    "delivery_corrections(id,execution_id,generation,candidate_sha,pipeline"
                    "_id,request,state,acknowledgement,epoch_id) VALUES "
                    "(?,?,?,?,?,?,?,NULL,?)"
                ),
                (
                    request_id,
                    execution_id,
                    request["generation"],
                    pipeline["sha"],
                    pipeline["id"],
                    canonical(request),
                    "Intent",
                    execution["epoch_id"],
                ),
            )
    return admit_delivery_correction(controller, request_id)


def correction_request(controller, execution_id, request_id):
    row = controller.conn.execute(
        "SELECT * FROM delivery_corrections WHERE id=? AND execution_id=?",
        (request_id, execution_id),
    ).fetchone()
    if not row:
        raise ValueError("unknown correction request")
    return dict(row), loads(row["request"], max_bytes=4194304)


def validate_correction_authority(controller, execution, request_id):
    row, request = correction_request(controller, execution["id"], request_id)
    delivery = get_delivery(controller.conn, execution["id"])
    from harness.delivery_store import require_delivery_epoch

    require_delivery_epoch(controller.conn, dict(row))
    require_delivery_epoch(controller.conn, delivery)
    if row["state"] not in {"Admitted", "Acknowledged"}:
        raise ValueError("correction has not been admitted by supervisor")
    expected_generation = delivery["generation"] + (row["state"] != "Acknowledged")
    if (
        request["generation"] != expected_generation
        or request["candidate_sha"] != delivery["candidate_sha"]
        or request["binding"]
        != execution["binding"]
        | {
            "handoff_hash": execution["handoff_hash"],
            "al_session_id": execution["al_session_id"],
            "al_run_id": execution["al_run_id"],
        }
    ):
        raise ValueError("correction binding/generation/candidate is stale")
    if (
        row["state"] == "Admitted"
        and digest(execution["result"]) != request["prior_completion_digest"]
    ):
        raise ValueError("correction prior completion differs")
    path = Path(execution["handoff_path"]).parent / "corrections" / (request_id + ".json")
    if path.read_bytes() != canonical(request).encode():
        raise ValueError("immutable correction delivery changed")
    return request


def admit_delivery_correction(controller, request_id):
    conn = controller.conn
    row = conn.execute("SELECT * FROM delivery_corrections WHERE id=?", (request_id,)).fetchone()
    if row is None:
        raise LookupError("correction request not found")
    execution_id = row["execution_id"]
    execution = get_execution(conn, execution_id)
    from harness.execution_worker import replay_source, worker_lock

    with worker_lock(execution):
        attempts = conn.execute(
            "SELECT child_pid,status FROM execution_attempts WHERE execution_id=?", (execution_id,)
        ).fetchall()
        if any(
            a["child_pid"]
            and (process_alive(a["child_pid"]) or a["status"] not in {"Exited", "Reconciled"})
            for a in attempts
        ):
            return delivery_view(conn, execution_id)
        envelope = require_execution_authority(controller, execution_id, operation="delivery")
        row, request = correction_request(controller, execution_id, request_id)
        if row["state"] == "Acknowledged":
            return delivery_view(conn, execution_id)
        if not execution["native_id"]:
            raise ValueError("correction requires recorded native conversation")
        directory = Path(execution["handoff_path"]).parent / "corrections"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / (request_id + ".json")
        raw = canonical(request).encode()
        if path.exists() and path.read_bytes() != raw:
            raise ValueError("correction delivery bytes changed")
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(raw)
            path.chmod(0o600)
        with transaction(conn):
            current = get_delivery(conn, execution_id)
            if row["state"] == "Intent":
                if current["retries"] >= current["config"]["retry_limit"]:
                    raise ValueError("correction budget exhausted")
                conn.execute(
                    "UPDATE deliveries SET retries=retries+1,status='Correcting' "
                    "WHERE execution_id=?",
                    (execution_id,),
                )
                conn.execute(
                    "UPDATE delivery_corrections SET state='Admitted' WHERE id=?", (request_id,)
                )
        validate_correction_authority(controller, execution, request_id)
        package = installed_package(execution["host"], envelope["repository"])
        from harness.execution_bootstrap import execution_environment

        command = al_argv(
            package["launcher"],
            execution["host"],
            envelope["repository"],
            execution["al_session_id"],
            "integration",
            "delivery-correction",
            "--file",
            str(path),
            existing_run=True,
        )
        result = subprocess.run(
            command,
            cwd=envelope["repository"],
            env=execution_environment(),
            capture_output=True,
            timeout=30,
        )
        if result.returncode:
            raise ValueError("guarded AL delivery correction refused")
        replay_source(conn, execution_id, execution["worker_token"])
    from harness.execution_recovery import resume_execution

    resume_execution(controller, execution_id)
    return delivery_view(conn, execution_id)
