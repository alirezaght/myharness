"""Contained prepared startup and publication under the genuine issuer's locks."""

import tempfile
from pathlib import Path

from . import runtime_successor_store as Store
from .registry import write_json
from .runtime_pin_initialization import initialize_locked
from .runtime_pin_transaction import (
    commit_run_binding_locked,
    read,
    read_state,
    runtime_directory,
    state_path,
    storage,
)
from .runtime_successor_channel import fingerprint, receive, send
from .runtime_successor_child import run_child
from .runtime_successor_lineage import artifact, observe_candidate, requirement_lineage, revisions
from .validator_origin import canonical, capture, digest


def _protected_paths(prior, successor, authority_directory):
    old = runtime_directory(prior)
    new = runtime_directory(successor)
    protected = [
        storage() / "runs",
        storage() / "initializations",
        storage() / "successors",
        storage() / "successor-fences",
        Path(authority_directory),
    ]

    def retain(path):
        if path == new:
            return
        if new.is_relative_to(path):
            for child in path.iterdir():
                retain(child)
        else:
            protected.append(path)

    retain(old)
    return sorted(set(map(str, protected)))


def _context(plan_ref, plan):
    return {
        "schema_version": 1,
        "request_id": plan["request"]["request_id"],
        "preparation_identity": plan_ref["sha256"],
        "plan_ref": plan_ref,
        "repository": plan["predecessor_transaction"]["repository_identity"]["root"],
        "predecessor": plan["predecessor_transaction"],
        "successor": plan["successor_transaction"],
        "baseline": plan["source_anchors"]["baseline_head_oid"],
        "HEAD": plan["source_anchors"]["observed_head_oid"],
        "start_operation": "managed-successor-start-v1",
    }


def _postconditions(
    successor, lineage, authorized_binding, expected_candidate, classified, anchors
):
    state = read_state(successor)
    if state is None:
        raise ValueError("prepared successor did not publish state")
    from .runtime_successor_store import read_reference

    requirements = read_reference(lineage["requirements_ref"])
    if (
        state.get("proof_admission", {}).get("status") != "pending"
        or state["phase"] != "PLANNING"
        or state["writers"]
        or state["evidence"]["entries"]
        or state["evidence"]["plan"]
        or state["amendments"] != requirements["successor_amendments"]
        or revisions(state["task_package"]["sha256"], state["amendments"])
        != requirements["revision_hashes"]
        or state["integration"]["binding"] != authorized_binding
        or any(
            state["integration"][k]
            for k in ("tasks", "agents", "blockers", "deliveries", "revalidations")
        )
        or state["baseline_head"] != anchors["baseline_head_oid"]
        or state["identity"]["merge_base"] != anchors["merge_base_oid"]
        or state["identity"]["target_oid"] != anchors["target_oid"]
        or state["preexisting_changes"] != anchors["original_preexisting_changes"]
        or observe_candidate(
            anchors["repository_identity"]["root"], anchors["baseline_head_oid"], classified
        )
        != expected_candidate
    ):
        raise ValueError("prepared successor publication differs from issued requirements")
    pointer, _ = capture(runtime_directory(successor) / "current")
    if pointer != (successor["session_id"] + "\n").encode():
        raise ValueError("prepared successor pointer incomplete")
    return state


def start_locked(
    coordinator,
    request,
    directory,
    journal_path,
    journal,
    prior_lock,
    successor_lock,
    prior,
    successor,
    state,
    plan_ref,
    anchors,
    verified,
):
    from .runtime_start_isolation import validate_descriptor
    from .runtime_successor import _authority, _frame, _policy, _session_lock

    plan = Store.read_reference(plan_ref)
    context = _context(plan_ref, plan)
    context_ref = Store.immutable(directory / "context.json", context)
    # This fixed endpoint verifies accepted scoped amendments under the old authority.
    authorization = _authority(
        request["authority_snapshot_ref"],
        state["integration"]["handoff"],
        context_ref["stored_path"],
    )
    requirements_ref = requirement_lineage(
        prior, state, successor, directory, authorization["scoped_acceptance"]
    )
    protected = _protected_paths(prior, successor, plan["authority_directory"])
    # Scratch is created once per attempt; it is never a registry or product write grant.
    with tempfile.TemporaryDirectory(prefix="al-prepared-start-") as temporary:
        scratch = Path(temporary).resolve(strict=True)
        notification = {
            "plan_ref": plan_ref,
            "scratch": str(scratch),
            "storage": str(storage()),
            "predecessor_state_ref": plan["predecessor_state_ref"],
            "protected_paths": protected,
            "lineage_path": str(directory / "lineage.json"),
            "requirements_path": requirements_ref["stored_path"],
        }
        send(
            coordinator, {"version": 1, "sequence": 1, "phase": "reserved", "payload": notification}
        )
        response = _frame(receive(coordinator), 2, "operation", "resources")
        if set(response) != {"resources", "isolation_descriptor"}:
            raise ValueError("prepared resource descriptor refused")
        resources = response["resources"]
        resources_ref = Store.immutable(directory / "resources.json", resources)
        authority = _authority(
            request["authority_snapshot_ref"],
            state["integration"]["handoff"],
            context_ref["stored_path"],
            resources_ref["stored_path"],
        )
        classified = authority["classified_paths"]
        observed = observe_candidate(
            anchors["repository_identity"]["root"], anchors["baseline_head_oid"], classified
        )
        if observed != request["expected_candidate_identity"]:
            raise ValueError("candidate changed before prepared capture")
        lineage = {
            "schema_version": 1,
            "kind": "successor-source-lineage-v1",
            "request_id": request["request_id"],
            "preparation_sha256": plan_ref["sha256"],
            "requirements_ref": requirements_ref,
            "predecessor_state_ref": plan["predecessor_state_ref"],
            "predecessor_binding_ref": plan["predecessor_binding_ref"],
            "source_anchors": anchors,
            "settled_candidate_identity": observed,
            "successor_transaction": plan["successor_transaction"],
        }
        lineage_ref = Store.immutable(directory / "lineage.json", lineage)
        Store.immutable(directory / "lineage-reference.json", lineage_ref)
        approved = [
            resources["handoff_ref"]["path"],
            resources["package_ref"]["path"],
            lineage_ref["stored_path"],
            requirements_ref["stored_path"],
        ]
        descriptor = validate_descriptor(
            response["isolation_descriptor"],
            successor,
            scratch=scratch,
            protected=protected,
            classified=classified,
            approved_files=approved,
        )
        expected_policy = _policy(verified, successor["platform"])
        argv = [
            "start",
            "--package-file",
            resources["package_ref"]["path"],
            "--handoff",
            resources["handoff_ref"]["path"],
            "--target",
            anchors["target_oid"],
            "--review-mode",
            anchors["review_mode"],
        ]

        def validate_start(payload):
            if payload != {"transaction_sha256": fingerprint(successor)}:
                raise ValueError("prepared start reservation digest differs")
            successor_lock.validate(successor_lock.value)
            if read(successor_lock.path) != successor:
                raise ValueError("prepared reservation moved")
            return {
                "transaction": successor,
                "argv": argv,
                "lineage_ref": lineage_ref,
                "authorized_start_binding": authority["authorized_start_binding"],
            }

        def initialize(supplied):
            nonlocal successor
            required = {
                "schema_version",
                "kind",
                "repository_identity",
                "session_id",
                "run_id",
                "original_task_package",
                "requirement_lineage_hash",
                "runtime_pin",
                "verifier_policy",
                "planning_occurrence",
                "runtime_layout",
                "runtime_directory",
            }
            if not isinstance(supplied, dict) or set(supplied) != required:
                raise ValueError("prepared initialization shape differs")
            requirements = Store.read_reference(requirements_ref)
            expected = {
                "schema_version": 2,
                "kind": "proof-admission-initialization-v2",
                "repository_identity": successor["repository_identity"],
                "session_id": successor["session_id"],
                "run_id": successor["run_id"],
                "requirement_lineage_hash": requirements["requirement_lineage_hash"],
                "runtime_pin": successor["pin"],
                "verifier_policy": expected_policy,
                "planning_occurrence": 0,
                "runtime_layout": successor["runtime_layout"],
                "runtime_directory": successor["runtime_directory"],
            }
            if any(supplied[k] != value for k, value in expected.items()):
                raise ValueError("prepared initialization identity differs")
            ref = supplied["original_task_package"]
            raw = artifact(
                ref, runtime_directory(successor) / successor["session_id"] / "artifacts"
            )
            if digest(raw) != requirements["original_package"]["sha256"]:
                raise ValueError("prepared initialization package differs")
            approved_ref = dict(requirements["successor_package"], kind="task-package")
            supplied = dict(supplied, original_task_package=approved_ref)
            successor, initial = initialize_locked(successor_lock, successor, supplied)
            journal.update(state="INITIALIZED", initialization=successor["initialization"])
            write_json(journal_path, journal)
            return {"transaction": successor, "initialization": initial}

        if read_state(successor) is None:
            run_child(
                coordinator,
                successor,
                verified,
                argv,
                descriptor,
                lineage_ref,
                validate_start,
                initialize,
                directory,
                journal_path,
                journal,
            )
        else:
            if not journal.get("cleanup", {}).get("complete"):
                raise ValueError("partial prepared start lacks settled owned child proof")
        # The child has released its session lock and its group is positively settled.
        with _session_lock(successor):
            _authority(
                request["authority_snapshot_ref"],
                state["integration"]["handoff"],
                context_ref["stored_path"],
                resources_ref["stored_path"],
            )
            _postconditions(
                successor,
                lineage,
                authority["authorized_start_binding"],
                observed,
                classified,
                anchors,
            )
            commit_run_binding_locked(successor_lock, successor)
            current = read(successor_lock.path)
            receipt = {
                "schema_version": 1,
                "kind": "prepared-successor-v1",
                "state": "PREPARED",
                "request_id": request["request_id"],
                "request_sha256": fingerprint(request),
                "preparation_sha256": plan_ref["sha256"],
                "predecessor_binding_ref": plan["predecessor_binding_ref"],
                "predecessor_state_ref": plan["predecessor_state_ref"],
                "lineage_ref": lineage_ref,
                "successor_subject": successor_lock.value,
                "successor_run_id": current["run_id"],
                "successor_transaction": current,
                "successor_state_ref": Store.archive_bytes(
                    request["request_id"], "successor-state.json", capture(state_path(current))[0]
                ),
                "successor_handoff_ref": resources["handoff_ref"],
                "prepared_classification_ref": resources["classification_ref"],
                "candidate_identity": observed,
                "distribution_identity": verified["manifest"]["closure_digest"],
                "native_resumed": False,
                "product_authority": False,
            }
            receipt_ref = Store.immutable(directory / "receipt.json", receipt)
            journal.update(state="PREPARED", receipt_ref=receipt_ref)
            write_json(journal_path, journal)
        send(coordinator, {"version": 1, "sequence": 2, "phase": "prepared", "payload": receipt})
        acknowledgement = _frame(receive(coordinator), 3, "operation", "acknowledge")
        if acknowledgement != {"receipt_sha256": digest(canonical(receipt))}:
            raise ValueError("prepared receipt acknowledgement differs")
        journal.update(acknowledged=True)
        write_json(journal_path, journal)
        return {
            "state": "PREPARED",
            "receipt_ref": receipt_ref,
            "product_authority": False,
            "native_resumed": False,
        }
