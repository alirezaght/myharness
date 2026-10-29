"""Journaled successor selection; fencing is irreversible and recovery rolls forward."""

import contextlib
import socket
from pathlib import Path

from . import runtime_successor_store as Store
from .registry import write_json
from .runtime_pin import _directory
from .runtime_pin_transaction import publication_lock, read, state_path, storage
from .runtime_successor import _authority, _frame, _session_lock, distribution
from .runtime_successor_census import require_quiescent
from .runtime_successor_channel import fingerprint, receive, send
from .runtime_successor_lineage import observe_candidate
from .validator_origin import canonical, capture, digest


def active_path(subject):
    return storage() / "successor-active" / (fingerprint(subject) + ".json")


def require_active(subject):
    path = active_path(subject)
    raw, mode = capture(path)
    value = __import__("json").loads(raw)
    if (
        mode["mode"] != 0o600
        or path.stat().st_nlink != 1
        or value["kind"] != "active-successor-epoch-v1"
        or value["successor_subject"] != subject
    ):
        raise ValueError("invalid active successor selector")
    directory = Store.directory(value["request_id"])
    journal = read(directory / "activation-journal.json")
    activation = Store.read_reference(journal["activation_ref"])
    # Selector bytes are independently bound by the durable SQLite epoch identity.
    expected = activation["selector"]
    if value != expected or activation["selector_ref"] != {
        "stored_path": str(path),
        "sha256": digest(raw),
    }:
        raise ValueError("active successor selector differs from protected transition")
    plan = read(directory / "plan.json")
    old = Store.read_reference(plan["predecessor_state_ref"])
    _authority(
        plan["request"]["authority_snapshot_ref"],
        old["integration"]["handoff"],
        directory / "context.json",
        activation_path=directory / "activation-plan.json",
    )
    return value


def _activate_successor(repository, platform, identifier, coordinator_fd):
    distribution(platform)
    coordinator = socket.socket(fileno=coordinator_fd)
    coordinator.settimeout(300)
    directory = Store.directory(identifier)
    request = _frame(receive(coordinator), 1, "operation", "activate")
    if (
        set(request)
        != {
            "request_id",
            "preparation_sha256",
            "expected_epoch_id",
            "expected_authority_generation",
        }
        or request["request_id"] != identifier
    ):
        raise ValueError("invalid successor activation request")
    plan_raw, _ = capture(directory / "plan.json")
    plan = __import__("json").loads(plan_raw)
    preparation_journal = read(directory / "journal.json")
    if preparation_journal.get("receipt_ref", {}).get("stored_path") != str(
        directory / "receipt.json"
    ):
        raise ValueError("protected preparation receipt is unavailable")
    receipt = Store.read_reference(preparation_journal["receipt_ref"])
    if digest(plan_raw) != receipt["preparation_sha256"]:
        raise ValueError("protected preparation plan changed")
    original = plan["request"]
    if (
        request["preparation_sha256"] != receipt["preparation_sha256"]
        or request["expected_epoch_id"] != original["expected_epoch_id"]
        or request["expected_authority_generation"] != original["expected_authority_generation"]
        or str(Path(repository).resolve(strict=True)) != original_repository(plan)
        or platform != plan["predecessor_transaction"]["platform"]
    ):
        raise ValueError("stale successor activation identity")
    subjects = [
        receipt["successor_subject"],
        {
            k: plan["predecessor_transaction"][k]
            for k in ("repository_identity", "platform", "session_id")
        },
    ]
    with contextlib.ExitStack() as locks:
        capabilities = {
            fingerprint(s): locks.enter_context(publication_lock(s))
            for s in sorted(subjects, key=fingerprint)
        }
        prior = plan["predecessor_transaction"]
        successor = receipt["successor_transaction"]
        prior_lock = capabilities[fingerprint(subjects[1])]
        successor_lock = capabilities[fingerprint(subjects[0])]
        for transaction in sorted([prior, successor], key=lambda t: str(state_path(t))):
            locks.enter_context(_session_lock(transaction))
        require_quiescent(repository)
        journal_path = directory / "activation-journal.json"
        current_prior = read(prior_lock.path)
        if read(successor_lock.path) != successor:
            raise ValueError("successor protected binding changed")
        if digest(capture(state_path(successor))[0]) != receipt["successor_state_ref"]["sha256"]:
            raise ValueError("prepared successor state changed before activation")
        old = Store.read_reference(plan["predecessor_state_ref"])
        if current_prior == prior:
            authorization = _authority(
                original["authority_snapshot_ref"],
                old["integration"]["handoff"],
                directory / "context.json",
                directory / "resources.json",
            )
            candidate = observe_candidate(
                repository,
                plan["source_anchors"]["baseline_head_oid"],
                authorization["classified_paths"],
            )
            if (
                candidate != receipt["candidate_identity"]
                or capture(state_path(prior))[0]
                != capture(Path(plan["predecessor_state_ref"]["stored_path"]))[0]
            ):
                raise ValueError("predecessor inputs changed before activation")
        else:
            expected_tombstone = read(directory / "predecessor-tombstone.json")
            if current_prior != expected_tombstone:
                raise ValueError("predecessor fence is foreign or altered")
        clock_path = directory / "activation-clock.json"
        if clock_path.exists():
            clock = read(clock_path)
        else:
            import time

            clock = {"activated_at": time.time()}
            Store.immutable(clock_path, clock)
        selector = {
            "activated_at": clock["activated_at"],
            "predecessor_child_ids": sorted(old["integration"]["agents"]),
            "schema_version": 1,
            "kind": "active-successor-epoch-v1",
            "execution_id": original["execution_id"],
            "request_id": identifier,
            "storage": str(storage()),
            "epoch_id": "epoch-" + successor["run_id"],
            "authority_generation": original["expected_authority_generation"] + 1,
            "successor_subject": receipt["successor_subject"],
            "successor_transaction": successor,
            "preparation_sha256": receipt["preparation_sha256"],
        }
        destination = active_path(receipt["successor_subject"])
        selector_ref = {
            "stored_path": str(destination),
            "sha256": digest(canonical(selector) + b"\n"),
        }
        activation = {
            "kind": "successor-activation-plan-v1",
            "request_id": identifier,
            "receipt": receipt,
            "selector": selector,
            "selector_ref": selector_ref,
        }
        activation_ref = Store.immutable(directory / "activation-plan.json", activation)
        tombstone = {
            "schema_version": 1,
            "kind": "superseded-run-binding-v1",
            "status": "SUPERSEDED",
            "repository_identity": prior["repository_identity"],
            "platform": platform,
            "session_id": prior["session_id"],
            "run_id": prior["run_id"],
            "predecessor_binding_ref": plan["predecessor_binding_ref"],
            "successor_subject": receipt["successor_subject"],
            "activation_ref": activation_ref,
        }
        Store.immutable(directory / "predecessor-tombstone.json", tombstone)
        write_json(journal_path, {"state": "FENCING", "activation_ref": activation_ref})
        if current_prior == prior:
            write_json(prior_lock.path, tombstone)
        write_json(journal_path, {"state": "PREDECESSOR_FENCED", "activation_ref": activation_ref})
        send(coordinator, {"version": 1, "sequence": 1, "phase": "fenced", "payload": activation})
        selected = _frame(receive(coordinator), 2, "operation", "database-selected")
        if selected != {"activation_sha256": activation_ref["sha256"]}:
            raise ValueError("activation database acknowledgement differs")
        selected_authority = _authority(
            original["authority_snapshot_ref"],
            old["integration"]["handoff"],
            directory / "context.json",
            activation_path=directory / "activation-plan.json",
        )
        if (
            observe_candidate(
                repository,
                plan["source_anchors"]["baseline_head_oid"],
                selected_authority["classified_paths"],
            )
            != receipt["candidate_identity"]
        ):
            raise ValueError("candidate changed during successor selection")
        write_json(journal_path, {"state": "DB_SELECTED", "activation_ref": activation_ref})
        _directory(destination.parent)
        Store.immutable(destination, selector)
        write_json(journal_path, {"state": "ACTIVE", "activation_ref": activation_ref})
        send(
            coordinator,
            {
                "version": 1,
                "sequence": 2,
                "phase": "active",
                "payload": {
                    "state": "ACTIVE",
                    "epoch_id": selector["epoch_id"],
                    "authority_generation": selector["authority_generation"],
                    "active_selector_ref": selector_ref,
                    "native_resumed": False,
                },
            },
        )
        return {"state": "ACTIVE", "native_resumed": False}


def original_repository(plan):
    return plan["predecessor_transaction"]["repository_identity"]["root"]


def activate_successor(repository, platform, identifier, coordinator_fd):
    try:
        return _activate_successor(repository, platform, identifier, coordinator_fd)
    except BaseException as error:
        path = Store.directory(identifier) / "activation-journal.json"
        journal = read(path) if path.exists() else {"state": "REFUSED"}
        journal.update(error_type=type(error).__name__, safe_diagnostic=str(error)[:512])
        write_json(path, journal)
        raise
