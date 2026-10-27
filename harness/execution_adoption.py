"""Explicit verified standalone planning reuse; no authority or evidence is transferred."""

import json
import re
from pathlib import Path

from harness.db import transaction
from harness.execution_artifacts import ArtifactStore
from harness.execution_authority import require_execution_authority
from harness.execution_bootstrap import execution_environment
from harness.execution_contract import canonical, digest, loads
from harness.execution_hosts import managed_launcher
from harness.execution_models import ExecutionConflict
from harness.execution_store import get_execution, process_alive
from harness.execution_worker import worker_lock
from harness.owned_subprocess import run_owned


def adoptions(controller, execution_id):
    execution = get_execution(controller.conn, execution_id)
    return [
        e.payload
        for e in controller.workspace(execution["task_id"]).events()
        if e.kind == "execution.planning_adopted" and e.payload["execution_id"] == execution_id
    ]


def adopt_planning(controller, execution_id, request, approved_sha256, *, approval_source):
    if digest(request) != approved_sha256:
        raise ValueError("planning adoption approval must match the exact request hash")
    if not isinstance(approval_source, str) or len(approval_source.strip()) < 20:
        raise ValueError("planning adoption requires explicit substantive user approval provenance")
    required = {"schema_version", "source", "target", "mapping", "reason"}
    if (
        not isinstance(request, dict)
        or set(request) != required
        or type(request["schema_version"]) is not int
        or request["schema_version"] != 1
    ):
        raise ValueError("closed planning adoption request schema_version 1 required")
    source = request["source"]
    fields = {"repository", "session_id", "run_id", "platform", "task_sha256", "report_sha256"}
    if (
        not isinstance(source, dict)
        or set(source) != fields
        or source["platform"] not in {"codex", "claude"}
    ):
        raise ValueError(
            "explicit source repository, platform, session, run, task and report hashes required"
        )
    if any(
        not isinstance(source[k], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", source[k])
        for k in ("session_id", "run_id")
    ) or any(
        not isinstance(source[k], str) or not re.fullmatch(r"[0-9a-f]{64}", source[k])
        for k in ("task_sha256", "report_sha256")
    ):
        raise ValueError("invalid explicit source identity or digest")
    if not isinstance(request["reason"], str) or len(request["reason"].strip()) < 20:
        raise ValueError("substantive planning reuse reason required")
    execution = get_execution(controller.conn, execution_id)
    with worker_lock(execution):
        execution = get_execution(controller.conn, execution_id)
        envelope = require_execution_authority(controller, execution_id, operation="read")
        if request["target"] != {
            "execution_id": execution_id,
            "handoff_hash": execution["handoff_hash"],
        }:
            raise ExecutionConflict("adoption targets another execution or approved handoff")
        if Path(source["repository"]).resolve() != Path(envelope["repository"]).resolve():
            raise ExecutionConflict(
                "planning reuse requires the same explicitly identified repository"
            )
        rows = controller.conn.execute(
            "SELECT child_pid,status FROM execution_attempts WHERE execution_id=?", (execution_id,)
        ).fetchall()
        if any(
            row["status"] not in {"Exited", "Reconciled", "PreSpawnFailed"}
            or process_alive(row["child_pid"])
            for row in rows
        ):
            raise ExecutionConflict("settle native attempts before importing prior planning")
        directory = Path(execution["handoff_path"]).parent
        target = json.loads((directory / "task-package.json").read_bytes())
        completed = run_owned(
            [
                managed_launcher(),
                "planning-proof",
                "--repository",
                str(Path(source["repository"]).resolve()),
                "--platform",
                source["platform"],
                "--session",
                source["session_id"],
                "--run",
                source["run_id"],
            ],
            cwd=envelope["repository"],
            env=execution_environment(),
            timeout=60,
        )
        if completed.returncode or len(completed.stdout) > 16 * 1024 * 1024:
            from harness.execution_logs import redact

            raise ExecutionConflict(
                "protected planning proof refused; revalidate the exact standalone "
                "planning occurrence before retrying: "
                + redact(completed.stdout.decode(errors="replace")[-2048:])
            )
        proof = loads(completed.stdout, max_bytes=16 * 1024 * 1024)
        if (
            proof.get("schema_version"),
            proof.get("kind"),
            proof.get("repository"),
            proof.get("session_id"),
            proof.get("run_id"),
            proof.get("platform"),
        ) != (
            2,
            "planning-workspace-source-proof",
            str(Path(envelope["repository"]).resolve()),
            source["session_id"],
            source["run_id"],
            source["platform"],
        ):
            raise ExecutionConflict("protected planning proof identity differs")
        if (
            proof["task_package"]["sha256"] != source["task_sha256"]
            or proof["planning"]["report"]["sha256"] != source["report_sha256"]
        ):
            raise ExecutionConflict(
                "planning artifacts differ from the explicitly approved source hashes"
            )
        if proof["source"]["head"] != execution["binding"]["revision"]:
            raise ExecutionConflict(
                "planning source HEAD differs from the approved Harness candidate"
            )
        package = proof["task_package"]["content"]
        mapping = request["mapping"]
        if not isinstance(mapping, dict) or set(mapping) != {"criteria", "invariants"}:
            raise ValueError("explicit complete criterion and invariant mappings required")
        for field in ("criteria", "invariants"):
            values = mapping[field]
            if (
                not isinstance(values, dict)
                or set(values) != set(target.get(field, []))
                or any(identifier not in package.get(field, []) for identifier in values.values())
            ):
                raise ExecutionConflict("incomplete or unknown source/target " + field + " mapping")
        receipt = {
            "schema_version": 1,
            "kind": "planning-adoption-v1",
            "execution_id": execution_id,
            "binding": execution["binding"],
            "handoff_hash": execution["handoff_hash"],
            "request": request,
            "request_sha256": approved_sha256,
            "approval_source": approval_source.strip(),
            "protected_source": proof,
            "authority": "REFERENCE_ONLY_CURRENT_HARNESS_ARCHITECT_REVALIDATION_REQUIRED",
            "transferred_approval": False,
            "transferred_evidence": [],
        }
        raw = canonical(receipt).encode()
        with transaction(controller.conn):
            require_execution_authority(controller, execution_id, operation="read")
            prior = next(
                (
                    r
                    for r in adoptions(controller, execution_id)
                    if r["request_sha256"] == approved_sha256
                ),
                None,
            )
            if prior:
                # Revalidate the source even on replay, then retain original provenance.
                return prior
            artifact = ArtifactStore(directory / "operations" / "drafts").capture_generated(
                "planning-adoption.json", raw
            )
            result = {
                "execution_id": execution_id,
                "request_sha256": approved_sha256,
                "receipt": artifact,
                "resource": "artifact:" + artifact["artifact_id"] + ":" + artifact["sha256"],
                "source": source,
                "current_architect_revalidation_required": True,
            }
            controller.workspace(execution["task_id"]).record_event(
                "execution.planning_adopted", result
            )
        return result
