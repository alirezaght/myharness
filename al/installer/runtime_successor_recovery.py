"""Closed missing-state startup: retained authority, never a predecessor state substitute."""

import contextlib
import os
import secrets
from pathlib import Path

from . import runtime_successor_store as Store
from .registry import write_json
from .runtime_pin_initialization import read_initialization
from .runtime_pin_transaction import (
    publication_lock, read, reserve_locked, runtime_directory, state_path, storage, subject, validate,
)
from .runtime_successor_census import require_quiescent
from .runtime_successor_channel import fingerprint
from .runtime_successor_lineage import git, observe_candidate
from .validator_origin import capture, decode, digest, safe

RECOVERY_FIELDS = {"transition_mode", "preview_sha256", "recovery_lineage_ref",
                   "settlement_ref", "authority_handoff_ref", "expected_predecessor_state_sha256"}
MODE = "fresh-planning-missing-state"


def require_absent(transaction):
    path = safe(state_path(transaction))
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise ValueError("missing-state recovery requires confirmed absent active state")


def source_anchors(previous):
    root = previous["repository_identity"]["root"]
    head = git(root, "rev-parse", "HEAD").decode().strip()
    return {
        "repository_identity": previous["repository_identity"], "target_label": head,
        "target_oid": head, "merge_base_oid": head, "baseline_head_oid": head,
        "review_mode": "task",
        "original_preexisting_changes": sorted(git(root, "status", "--porcelain",
                                                     "--ignore-submodules=all").decode().splitlines()),
        "observed_head_oid": head, "predecessor_attribution": None,
        "verification_obligations": {
            "required": ["Original source baseline and ownership are unavailable",
                         "Fresh service and required-check discovery; no inherited PASS",
                         "Establish accepted requirement completeness before implementation"],
            "predecessor_check_areas": [], "predecessor_open_issues": [],
            "predecessor_gate_manifests": [],
        },
    }


def authority_inputs(previous, handoff_ref):
    raw, info = capture(handoff_ref["path"])
    if info["mode"] != 0o600 or "sha256:" + digest(raw) != handoff_ref["sha256"]:
        raise ValueError("recovery authority handoff changed")
    handoff = decode(raw)
    if (handoff["al_session_id"] != previous["session_id"]
            or handoff["repository"] != previous["repository_identity"]["root"]
            or handoff["host"] != previous["platform"]):
        raise ValueError("recovery handoff protected subject differs")
    return {
        "kind": "missing-state-recovery-inputs-v1", "execution_id": handoff["execution_id"],
        "predecessor": previous, "predecessor_initialization": read_initialization(previous),
        "source_anchors": source_anchors(previous), "authority_handoff_ref": handoff_ref,
    }


def recovery_inputs(repository, platform, session, identifier, authority_ref, handoff_ref):
    from .runtime_successor import _authority, distribution

    _, verified = distribution(platform)
    previous_subject = subject(repository, platform, session)
    with publication_lock(previous_subject) as lock:
        previous = read(lock.path)
        validate(previous, previous_subject)
        if previous["status"] != "ACTIVE":
            raise ValueError("recovery predecessor is not ACTIVE")
        require_absent(previous)
        value = authority_inputs(previous, handoff_ref)
        reference = Store.immutable(Store.directory(identifier) / "recovery-inputs.json", value)
        authorization = _authority(authority_ref, handoff_ref["path"], reference["stored_path"],
                                   inputs_path=reference["stored_path"])
        return {
            **value, "candidate_identity": observe_candidate(repository,
                value["source_anchors"]["baseline_head_oid"], authorization["classified_paths"]),
            "source_inventory": {
                "missing_tracked_files": [os.fsdecode(p) for p in git(repository, "ls-files", "--deleted", "-z").split(b"\0") if p],
                "limitations": ["Deleted uncommitted edits and untracked sources are not recoverable from Git inventory"],
            },
            "recovery_lineage": authorization["recovery_lineage"],
            "settlement": authorization["settlement"],
            "distribution_identity": verified["manifest"]["closure_digest"],
            "predecessor_subject": {"session_id": session, "run_id": previous["run_id"]},
        }


def read_settlement(reference, handoff_ref):
    expected = Path(handoff_ref["path"]).parent / "settlements" / (reference["sha256"] + ".json")
    if reference["stored_path"] != str(expected):
        raise ValueError("recovery settlement escaped its authority")
    raw, info = capture(expected)
    if info["mode"] != 0o600 or digest(raw) != reference["sha256"]:
        raise ValueError("recovery settlement bytes changed")
    return decode(raw)


def prepare_recovery(repository, platform, identifier, coordinator, request, pin, verified):
    from .runtime_successor import _authority
    from .runtime_successor_start import start_locked

    directory = Store.directory(identifier)
    request_ref = Store.immutable(directory / "request.json", request)
    journal_path = directory / "journal.json"
    if journal_path.exists():
        journal = read(journal_path)
        if journal["request_ref"] != request_ref:
            raise ValueError("recovery request identity reused with changed content")
        successor_subject = journal["successor_subject"]
    else:
        successor_subject = subject(repository, platform, "recovery-" + secrets.token_hex(16))
        journal = {"schema_version": 1, "kind": "successor-preparation-journal-v1",
                   "state": "PREPARING", "request_ref": request_ref,
                   "successor_subject": successor_subject}
        write_json(journal_path, journal)
    previous_subject = subject(repository, platform, request["predecessor_subject"]["session_id"])
    try:
        with contextlib.ExitStack() as locks:
            claims = {fingerprint(s): locks.enter_context(publication_lock(s))
                      for s in sorted([previous_subject, successor_subject], key=fingerprint)}
            prior_lock, next_lock = claims[fingerprint(previous_subject)], claims[fingerprint(successor_subject)]
            previous = read(prior_lock.path)
            validate(previous, previous_subject)
            if previous["status"] != "ACTIVE" or previous["run_id"] != request["predecessor_subject"]["run_id"]:
                raise ValueError("recovery predecessor identity differs")
            require_absent(previous)
            require_quiescent(repository)
            values = authority_inputs(previous, request["authority_handoff_ref"])
            inputs_ref = Store.immutable(directory / "recovery-inputs.json", values)
            authorization = _authority(request["authority_snapshot_ref"],
                request["authority_handoff_ref"]["path"], inputs_ref["stored_path"],
                inputs_path=inputs_ref["stored_path"])
            supplied_raw, supplied_info = capture(request["recovery_lineage_ref"]["stored_path"])
            supplied = decode(supplied_raw)
            if (supplied_info["mode"] != 0o600
                    or digest(supplied_raw) != request["recovery_lineage_ref"]["sha256"]
                    or supplied != authorization["recovery_lineage"]
                    or authorization["settlement"] != read_settlement(request["settlement_ref"], request["authority_handoff_ref"])
                    or supplied["revision_hashes"][-1] != request["approved_requirement_hash"]):
                raise ValueError("recovery accepted lineage or settlement changed")
            lineage_ref = Store.immutable(directory / "recovery-lineage.json", supplied)
            successor = reserve_locked(next_lock, pin, private_session=True,
                                       harness_binding=authorization["harness_binding"])
            if successor["pin"] != pin:
                raise ValueError("recovery reserved runtime distribution differs")
            ignored = runtime_directory(successor) / ".gitignore"
            if not ignored.exists():
                fd = os.open(ignored, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(b"*\n"); stream.flush(); os.fsync(stream.fileno())
            elif capture(ignored)[0] != b"*\n":
                raise ValueError("recovery ignore policy differs")
            binding_ref = Store.archive_bytes(identifier, "predecessor-binding.json", capture(prior_lock.path)[0])
            plan = {
                "schema_version": 1, "kind": "missing-state-successor-start-plan-v1",
                "request": request, "storage": str(storage()),
                "authority_directory": str(Path(request["authority_handoff_ref"]["path"]).parent),
                "authority_handoff_ref": request["authority_handoff_ref"],
                "predecessor_transaction": previous,
                "successor_transaction": dict(successor, initialization=None, status="RESERVED"),
                "predecessor_binding_ref": binding_ref, "recovery_lineage_ref": lineage_ref,
                "source_anchors": values["source_anchors"],
                "predecessor_initialization": values["predecessor_initialization"],
            }
            plan_ref = Store.immutable(directory / "plan.json", plan)
            Store.fence(successor_subject, identifier, plan_ref["sha256"])
            return start_locked(coordinator, request, directory, journal_path, journal,
                prior_lock, next_lock, previous, successor, None, plan_ref,
                values["source_anchors"], verified)
    except BaseException as error:
        journal.update(state="PREPARATION_FAILED", safe_diagnostic=str(error)[:512],
                       error_type=type(error).__name__)
        write_json(journal_path, journal)
        raise
    finally:
        coordinator.close()
