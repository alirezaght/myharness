"""Authenticated recovery inputs; missing operational history never becomes current state."""

import hashlib
import json
from pathlib import Path, PurePosixPath

from harness.execution_contract import canonical, digest, validate_delivery, validate_event
from harness.execution_handoff import atomic_file
from harness.execution_models import ExecutionConflict
from harness.template_classification import _ordinary


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _revisions(package_sha, amendments):
    result, hashes = [], []
    for amendment in [None, *amendments]:
        if amendment is not None:
            hashes.append(amendment["artifact"]["sha256"])
        result.append(
            _sha(
                json.dumps({"original": package_sha, "amendments": hashes}, sort_keys=True).encode()
            )
        )
    return result


def _artifact(directory, raw, kind, source):
    sha = _sha(raw)
    path = directory / sha
    atomic_file(path, raw)
    return {
        "id": sha,
        "sha256": sha,
        "stored_path": str(path),
        "kind": kind,
        "source": source,
        "captured_at": path.stat().st_mtime,
    }


def _paths(values):
    if not isinstance(values, list) or any(
        not isinstance(p, str)
        or not p
        or PurePosixPath(p).is_absolute()
        or ".." in PurePosixPath(p).parts
        or str(PurePosixPath(p)) != p
        for p in values
    ):
        raise ExecutionConflict("recovery historical scope is not confined")
    return values


def build_recovery_lineage(controller, execution, envelope, protected_inputs):
    """Reproduce only hash-bound package and acknowledged, ordered amendment bytes."""
    from harness.execution_epochs import get_epoch

    initial = protected_inputs["predecessor_initialization"]
    prior = protected_inputs["predecessor_transaction"]
    if (
        prior["session_id"] != execution["al_session_id"]
        or prior["run_id"] != execution["al_run_id"]
        or initial["run_id"] != prior["run_id"]
        or initial["session_id"] != prior["session_id"]
        or initial["repository_identity"] != prior["repository_identity"]
        or prior["repository_identity"]["root"] != envelope["repository"]
    ):
        raise ExecutionConflict("recovery initialization identity differs")
    package_raw = canonical(envelope["task_package"]).encode()
    if _sha(package_raw) != initial["original_task_package"]["sha256"]:
        raise ExecutionConflict("retained approved package does not match protected initialization")
    base = Path(execution["handoff_path"]).parent / "recovery-lineage-artifacts"
    original = _artifact(base, package_raw, "task-package", "verified original Harness handoff")
    rows = list(
        controller.conn.execute(
            "SELECT e.*,p.authority_generation FROM execution_events e JOIN execution_epochs p "
            "ON p.execution_id=e.execution_id AND p.epoch_id=e.epoch_id "
            "WHERE e.execution_id=? ORDER BY p.authority_generation,e.sequence",
            (execution["id"],),
        )
    )
    tasks, acknowledgments, creations, scopes, history = {}, {}, {}, {}, []
    for row in rows:
        event = json.loads(row["record"])
        validate_event(event)
        epoch = get_epoch(controller.conn, execution["id"], row["epoch_id"])
        identity = epoch["identity"]
        expected = execution["binding"] | {
            "al_session_id": identity["al_session_id"],
            "al_run_id": identity["al_run_id"],
            "handoff_hash": identity["handoff_hash"],
        }
        if (
            event["binding"] != expected
            or event["event_id"] != row["event_id"]
            or event["sequence"] != row["sequence"]
            or event["kind"] != row["kind"]
        ):
            raise ExecutionConflict("recovery event identity differs from its historical epoch")
        ref = {
            "epoch_id": row["epoch_id"],
            "event_id": row["event_id"],
            "sequence": row["sequence"],
            "sha256": digest(event),
        }
        payload, kind = event["payload"], event["kind"]
        key = (row["epoch_id"], payload.get("id"))
        if kind == "task.registered":
            tasks[key] = {"files": _paths(payload["files"]), "criteria": payload["criteria"]}
        elif kind in {"task.updated", "ownership.changed"}:
            tid = payload.get("id", payload.get("task"))
            key = (row["epoch_id"], tid)
            if key in tasks:
                for field in ("files", "criteria"):
                    if field in payload:
                        tasks[key][field] = (
                            _paths(payload[field]) if field == "files" else payload[field]
                        )
        elif kind == "amendment.created":
            if payload["id"] in creations:
                raise ExecutionConflict("duplicate historical amendment creation")
            creations[payload["id"]] = (payload, ref)
            scopes[payload["id"]] = {
                tid: tasks.get((row["epoch_id"], tid)) for tid in payload["tasks"]
            }
        elif kind == "decision.acknowledged":
            if payload["delivery_id"] in acknowledgments:
                raise ExecutionConflict("duplicate historical decision acknowledgment")
            acknowledgments[payload["delivery_id"]] = (payload, ref)
        elif kind in {"blocker.raised", "test.result", "writer.updated", "issue.updated"}:
            history.append({"kind": kind, "event_ref": ref})
    accepted, pending, rejected, historical_scopes = [], [], [], []
    decisions = {
        row["id"]: dict(row)
        for row in controller.conn.execute(
            "SELECT * FROM execution_decisions WHERE execution_id=?", (execution["id"],)
        )
    }
    for identifier, (ack, ack_ref) in acknowledgments.items():
        row = decisions.get(identifier)
        if not row or not row["delivery"]:
            raise ExecutionConflict("recovery acknowledgment lacks its retained delivery")
        delivery = json.loads(row["delivery"])
        validate_delivery(delivery)
        epoch = get_epoch(controller.conn, execution["id"], row["epoch_id"])
        identity = epoch["identity"]
        expected_binding = execution["binding"] | {
            "al_session_id": identity["al_session_id"],
            "al_run_id": identity["al_run_id"],
            "handoff_hash": identity["handoff_hash"],
        }
        registry_raw, _ = _ordinary(row["registry_path"], 4 * 1024 * 1024)
        if (
            digest(delivery) != ack["decision_hash"]
            or delivery["binding"] != expected_binding
            or delivery["registry"]["id"] != row["registry_id"]
            or delivery["registry"]["path"] != row["registry_path"]
            or Path(row["registry_path"]).parent != Path(envelope["repository"]) / "docs/decisions"
            or ack["state"] not in {"Resolved", "RevalidationRequired", "Rejected"}
            or row["registry_bytes"].encode() != registry_raw
            or digest(registry_raw) != row["registry_hash"]
            or delivery["registry"]["sha256"] != row["registry_hash"]
            or delivery["id"] != identifier
            or delivery["blocker_id"] != ack["blocker_id"]
            or ack_ref["epoch_id"] != row["epoch_id"]
        ):
            raise ExecutionConflict("recovery accepted delivery provenance differs")
        if ack["state"] == "Rejected":
            rejected.append({"id": identifier, "acknowledgment_ref": ack_ref})
            continue
        if delivery["verdict"] == "reject":
            raise ExecutionConflict("rejected delivery cannot authorize recovery requirements")
        if not delivery["amendment"]:
            continue
        creation = creations.get(identifier)
        task_scopes = scopes.get(identifier, {})
        if not creation or any(value is None for value in task_scopes.values()):
            pending.append({"id": identifier, "reason": "accepted amendment scope is unavailable"})
            continue
        if set(task_scopes) != set(delivery["tasks"]) or set(delivery["criteria"]) != {
            criterion for scope in task_scopes.values() for criterion in scope["criteria"]
        }:
            raise ExecutionConflict("recovery amendment affected scope differs from source tasks")
        amendment = {
            "id": identifier,
            "text": delivery["text"],
            "source": "developer Decision Registry " + delivery["registry"]["id"],
            "affected_criteria": delivery["criteria"],
            "affected_tasks": sorted(delivery["tasks"]),
            "base_revision": delivery["base_revision"],
            "delivery_hash": digest(delivery),
            "registry": delivery["registry"],
            "scoped": True,
        }
        raw = json.dumps(amendment, sort_keys=True).encode()
        if amendment["base_revision"] != _revisions(original["sha256"], accepted)[-1]:
            pending.append(
                {"id": identifier, "reason": "accepted requirement prefix is unavailable"}
            )
            continue
        if (
            _sha(raw) != creation[0].get("amendment")
            or creation[0]["tasks"] != amendment["affected_tasks"]
            or creation[1]["epoch_id"] != ack_ref["epoch_id"]
            or creation[1]["sequence"] >= ack_ref["sequence"]
        ):
            raise ExecutionConflict("recovery amendment order or accepted revision differs")
        amendment["artifact"] = _artifact(base, raw, "scoped-amendment", row["registry_path"])
        if creation[0]["revision"] != _revisions(original["sha256"], [*accepted, amendment])[-1]:
            raise ExecutionConflict("recovery resulting requirement revision differs")
        accepted.append(amendment)
        historical_scopes.append(
            {
                "id": identifier,
                "tasks": task_scopes,
                "event_ref": creation[1],
                "acknowledgment_ref": ack_ref,
            }
        )
    for identifier, row in decisions.items():
        if identifier not in acknowledgments:
            target = rejected if row["state"] == "Rejected" else pending
            target.append(
                {
                    "id": identifier,
                    "state": row["state"],
                    "reason": "no retained validated acknowledgment",
                }
            )
    obligations = [
        "Mutable predecessor history is unavailable; "
        "independently establish accepted requirement completeness",
        "Prior source ownership is unknown; investigate actual changes before implementation",
    ]
    obligations += ["Resolve historical decision provenance: " + item["id"] for item in pending]
    return {
        "schema_version": 1,
        "kind": "missing-state-recovery-lineage-v1",
        "execution_id": execution["id"],
        "predecessor_epoch_id": execution["epoch_id"],
        "predecessor_run_id": prior["run_id"],
        "original_package_ref": original,
        "original_package_sha256": original["sha256"],
        "amendments": accepted,
        "revision_hashes": _revisions(original["sha256"], accepted),
        "historical_scopes": historical_scopes,
        "pending_decisions": pending,
        "rejected_decisions": rejected,
        "historical_refs": history,
        "source_anchors": protected_inputs["source_anchors"],
        "history_completeness": "incomplete",
        "obligations": obligations,
    }
