# ruff: noqa: F811
"""Started uncertainty remains charged and blocks retirement; stale callbacks cannot settle."""

import pytest

from harness.execution_runtime_recovery import _settled
from harness.execution_store import get_execution
from tests.test_policy_admission import (  # noqa: F401
    attempt,
    configure,
    document,
    observed,
    policy_runtime,
    processes,
)


def test_started_crash_is_never_refunded_replayed_or_retired(policy_runtime):
    r = policy_runtime
    configure(r)
    a = attempt(r)
    a.start()
    a.finish(closed=False, outcome="failed")
    pool = r.controller.conn.execute("SELECT consumed,reserved FROM policy_pools").fetchone()
    assert pool[:] == (1, 0)
    execution = get_execution(r.controller.conn, r.operation.execution_id)
    with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
        _settled(r.controller, execution)
    with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
        a.start()


def test_release_start_race_only_one_transition_can_win(policy_runtime):
    r = policy_runtime
    configure(r)
    a = attempt(r)
    a.finish()
    with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
        a.start()
    assert r.controller.conn.execute("SELECT consumed,reserved FROM policy_pools").fetchone()[
        :
    ] == (0, 0)


def test_old_generation_settlement_refuses_without_historical_write(policy_runtime):
    r = policy_runtime
    configure(r)
    a = attempt(r)
    a.start()
    # Actual persisted generation changes; callback retains its old explicit binding.
    r.controller.conn.execute(
        "UPDATE executions SET authority_generation=1 WHERE id=?", (r.operation.execution_id,)
    )
    with pytest.raises(ValueError, match="stale_epoch"):
        a.finish()
    assert r.controller.conn.execute("SELECT state FROM policy_attempts").fetchone()[0] == "started"


def test_missing_pid_does_not_establish_closure_or_refund(policy_runtime):
    from harness.policy.processes import ProcessHooks

    r = policy_runtime
    configure(r)
    a = attempt(r)
    hooks = ProcessHooks(a)
    hooks.prepare("pending-owned-handle", None)
    a.owner_done = True
    hooks.finish(closed=False, outcome="failed")
    assert r.controller.conn.execute("SELECT consumed,reserved FROM policy_pools").fetchone()[
        :
    ] == (1, 0)
    assert (
        r.controller.conn.execute("SELECT state FROM policy_attempts").fetchone()[0] == "unresolved"
    )
    assert r.controller.conn.execute("SELECT pid,closure FROM policy_processes").fetchone()[:] == (
        None,
        "unconfirmed",
    )


def test_current_owning_supervisor_can_record_after_original_writer_inactive(policy_runtime):
    r = policy_runtime
    configure(r)
    a = attempt(r)
    a.start()
    r.state["writers"]["w"]["state"] = "FINISHED"
    r.save()
    release = r.operation.registry.directory / "capability-releases"
    release.mkdir()
    (release / (r.principal["thread_id"] + ".json")).write_text("{}")
    a.owner_finished()
    assert r.controller.conn.execute("SELECT state FROM policy_attempts").fetchone()[0] == "settled"
    assert r.controller.conn.execute("SELECT consumed,reserved FROM policy_pools").fetchone()[
        :
    ] == (1, 0)


def test_foreign_supervisor_cannot_settle_exact_attempt(policy_runtime):
    from types import SimpleNamespace

    r = policy_runtime
    configure(r)
    a = attempt(r)
    a.start()
    foreign = SimpleNamespace(**r.operation.__dict__)
    foreign._policy_owner = object()
    foreign._policy_supervisor = "foreign"
    original = a.operation
    a.operation = foreign
    try:
        with pytest.raises(ValueError, match="AUTHORITY_STALE"):
            a.finish()
    finally:
        a.operation = original
    assert r.controller.conn.execute("SELECT state FROM policy_attempts").fetchone()[0] == "started"


def test_concurrent_start_and_proven_unstarted_release_have_one_winner(policy_runtime):
    from concurrent.futures import ThreadPoolExecutor

    r = policy_runtime
    configure(r)
    a = attempt(r)

    def invoke(function):
        try:
            function()
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(2) as executor:
        result = list(executor.map(invoke, [a.start, a.release_unstarted]))
    assert sorted(result) == [False, True]
    state = r.controller.conn.execute("SELECT state FROM policy_attempts").fetchone()[0]
    counters = r.controller.conn.execute("SELECT consumed,reserved FROM policy_pools").fetchone()[:]
    assert (state, counters) in [("started", (1, 0)), ("released_proven_not_started", (0, 0))]


def test_post_effect_settlement_fault_retains_charge_and_never_replays(policy_runtime, monkeypatch):
    import hashlib
    import sqlite3
    from pathlib import Path

    from tests.test_policy_admission import patch
    from harness.policy import claims

    r = policy_runtime
    configure(r)
    canary = "settlement-private-fault-4751"
    transition = claims.transition
    faulted = []

    def fail_terminal(conn, attempt_id, state):
        row = conn.execute(
            "SELECT operation,state FROM policy_attempts WHERE id=?", (attempt_id,)
        ).fetchone()
        if (
            row["operation"] == "apply_patch"
            and row["state"] == "started"
            and state in {"closed", "unresolved"}
        ):
            faulted.append(attempt_id)
            raise sqlite3.IntegrityError("settlement-private-fault-4751")
        return transition(conn, attempt_id, state)

    with monkeypatch.context() as patcher:
        patcher.setattr(claims, "transition", fail_terminal)
        with pytest.raises(sqlite3.IntegrityError, match="settlement-private-fault-4751"):
            r.operation.dispatch(r.principal, "apply_patch", patch(r))
    assert len(faulted) == 1
    target = Path(r.operation.envelope["repository"]) / "svc/new.txt"
    assert target.read_text() == "safe\n"
    assert r.controller.conn.execute("SELECT state FROM policy_attempts").fetchone()[0] == "started"
    assert r.controller.conn.execute(
        "SELECT state FROM policy_resource_claims"
    ).fetchone()[0] != "closed"
    assert r.controller.conn.execute("SELECT consumed,reserved FROM policy_pools").fetchone()[
        :
    ] == (1, 0)
    # Nothing retries the invocation after its external effect. Audit facts stay closed.
    facts = " ".join(
        str(tuple(row)) for row in r.controller.conn.execute("SELECT * FROM policy_decisions")
    )
    assert canary not in facts and hashlib.sha256(canary.encode()).hexdigest() not in facts


def test_unresolved_claim_survives_permission_release_policy_removal_and_replacement(
    policy_runtime,
):
    import json
    import uuid
    from pathlib import Path

    from tests.test_policy_admission import patch

    r = policy_runtime
    configure(r, document(r, maximum=20))
    a = attempt(r)
    a.start()
    a.finish(closed=False)
    old = r.principal["thread_id"]
    new = str(uuid.uuid4())
    original = next(r.operation.registry.native_root.glob("sessions/**/rollout-" + old + ".jsonl"))
    metadata = json.loads(original.read_text())
    metadata["payload"].update(id=new, session_id=new)
    metadata["payload"]["source"]["subagent"]["thread_spawn"]["agent_role"] = "al-builder"
    original.with_name("rollout-" + new + ".jsonl").write_text(json.dumps(metadata) + "\n")
    successor = r.operation.registry.enroll(new)
    r.state["writers"]["w"]["state"] = "FINISHED"
    r.state["writers"]["next"] = dict(
        r.state["writers"]["w"], id="next", agent=new, role="al-builder", state="ACTIVE"
    )
    r.operation.assignments[new] = dict(r.operation.assignments[old])
    release = r.operation.registry.directory / "capability-releases"
    release.mkdir()
    (release / (old + ".json")).write_text("{}")
    r.save()
    r.controller.conn.execute("DELETE FROM policy_bindings")
    with pytest.raises(ValueError, match="irrevocably released"):
        r.operation.registry.require_live(r.principal)
    with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
        r.operation.dispatch(successor, "apply_patch", patch(r))
    assert not (Path(r.operation.envelope["repository"]) / "svc/new.txt").exists()
    assert r.operation.dispatch(successor, "apply_patch", patch(r, "svc/independent.txt"))[
        "changed"
    ] == ["svc/independent.txt"]
    assert (
        r.controller.conn.execute(
            "SELECT state FROM policy_resource_claims WHERE attempt_id=?", (a.intent.id,)
        ).fetchone()[0]
        == "unresolved"
    )


def test_reserved_claim_fences_same_principal_until_proven_no_start(policy_runtime):
    from tests.test_policy_admission import patch

    r = policy_runtime
    configure(r, document(r, maximum=20))
    a = attempt(r)
    with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
        r.operation.dispatch(r.principal, "apply_patch", patch(r))
    a.release_unstarted()
    assert r.operation.dispatch(r.principal, "apply_patch", patch(r))["changed"] == ["svc/new.txt"]


def test_legacy_closed_handler_admission_serializes_with_later_enrollment(policy_runtime):
    from tests.test_policy_admission import patch

    r = policy_runtime
    a = attempt(r)
    assert a.legacy
    assert (
        r.controller.conn.execute("SELECT COUNT(*) FROM policy_legacy_admissions").fetchone()[0]
        == 1
    )
    with pytest.raises(ValueError, match="UNSUPPORTED_ENFORCEMENT_CAPABILITY"):
        configure(r)
    a.release_unstarted()
    assert (
        r.controller.conn.execute("SELECT COUNT(*) FROM policy_legacy_admissions").fetchone()[0]
        == 0
    )
    configure(r)
    with pytest.raises(ValueError):
        a.start()
    assert r.operation.dispatch(r.principal, "apply_patch", patch(r))["changed"] == ["svc/new.txt"]


def test_legacy_closed_handler_normal_return_does_not_poison_enrollment(policy_runtime):
    from tests.test_policy_admission import patch

    r = policy_runtime
    r.operation.dispatch(r.principal, "apply_patch", patch(r))
    assert (
        r.controller.conn.execute("SELECT COUNT(*) FROM policy_resource_claims").fetchone()[0] == 0
    )
    assert (
        r.controller.conn.execute("SELECT COUNT(*) FROM policy_legacy_admissions").fetchone()[0]
        == 0
    )
    configure(r)


def test_open_process_cannot_hide_behind_terminal_accounting(policy_runtime):
    from harness.policy.processes import ProcessHooks
    from tests.test_policy_admission import patch

    r = policy_runtime
    configure(r, document(r, maximum=20))
    a = attempt(r)
    hooks = ProcessHooks(a)
    hooks.prepare("exact-pending-handle", None)
    # A corrupted/incorrect accounting label is not execution-boundary proof.
    r.controller.conn.execute(
        "UPDATE policy_attempts SET state='settled' WHERE id=?", (a.intent.id,)
    )
    r.controller.conn.execute(
        "UPDATE policy_resource_claims SET state='closed' WHERE attempt_id=?", (a.intent.id,)
    )
    with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
        r.operation.dispatch(r.principal, "apply_patch", patch(r))


def test_pending_without_permit_is_closed_only_by_fenced_release(policy_runtime):
    r = policy_runtime
    configure(r)
    a = attempt(r)
    r.controller.conn.execute(
        "INSERT INTO policy_processes(id,attempt_id,handle,instance_ref,launch_state) "
        "VALUES ('not-launched',?,'not-launched','exact-no-launch-instance','pending')",
        (a.intent.id,),
    )
    a.release_unstarted()
    assert (
        r.controller.conn.execute("SELECT boundary_closed FROM policy_processes").fetchone()[0] == 1
    )
    with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
        a.start()


def dependency_fixture(r):
    import time
    from pathlib import Path

    from harness.execution_contract import digest

    target = Path(r.operation.envelope["repository"]) / "producer/result.txt"
    target.parent.mkdir()
    target.write_text("authored unverified\n")
    requirement = "fixture-requirements"
    r.state["implementation_readiness"] = {"binding": {"requirement_hash": requirement}}
    r.state["evidence"] = {"entries": []}
    receipt = {
        "task_id": "producer",
        "task_revision": 1,
        "graph_revision": 1,
        "requirement_hash": requirement,
        "candidate_identity": {"scope_fingerprint": "fixture-candidate", "dependency_receipts": {}},
        "output_refs_and_hashes": [
            {
                "kind": "source",
                "path": "producer/result.txt",
                "sha256": digest(target.read_bytes()),
                "mode": 0o644,
            }
        ],
        "producer_invocation_ids": ["observed-producer"],
        "writer_ids": ["producer-writer"],
    }
    receipt["receipt_hash"] = digest(receipt)
    r.state["integration"]["tasks"]["producer"] = {
        "id": "producer",
        "revision": 1,
        "status": "Idle",
        "dependencies": [],
        "files": ["producer"],
        "authored_output": receipt,
    }
    r.state["integration"]["tasks"]["work"]["dependencies"] = ["producer"]
    r.state["integration"]["agents"] = {
        "observed-producer": {
            "id": "observed-producer",
            "status": "Completed",
            "ended_at": time.time(),
            "yield_required": False,
            "task": "producer",
        }
    }
    r.state["writers"]["producer-writer"] = {
        "id": "producer-writer",
        "task": "producer",
        "agent": "observed-producer",
        "role": "al-engineer",
        "state": "FINISHED",
        "outcome": "success",
        "files": ["producer"],
    }
    r.save()
    return target


def test_dependency_read_claim_precedes_verification_and_blocks_racing_write(
    policy_runtime, monkeypatch
):
    from contextlib import contextmanager

    from harness import execution_files
    from harness.policy import claims
    from tests.test_policy_admission import patch

    r = policy_runtime
    dependency_fixture(r)
    configure(r, document(r, maximum=20))
    original = execution_files.parent_descriptor
    checked = []

    @contextmanager
    def inspect(repository, path, **kwargs):
        if path == "producer/result.txt":
            proposed = [
                claims.claim(
                    r.operation._policy_repository,
                    path,
                    "write",
                    execution=r.operation.execution_id,
                )
            ]
            with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
                claims.require_clear(r.controller.conn, proposed)
            checked.append(True)
        with original(repository, path, **kwargs) as descriptor:
            yield descriptor

    monkeypatch.setattr(execution_files, "parent_descriptor", inspect)
    r.operation.dispatch(r.principal, "apply_patch", patch(r))
    assert checked
    assert r.state["integration"]["tasks"]["producer"]["status"] == "Idle"
    claims.require_clear(
        r.controller.conn,
        [
            claims.claim(
                r.operation._policy_repository,
                "producer/result.txt",
                "write",
                execution=r.operation.execution_id,
            )
        ],
    )


def test_dependency_receipt_drift_after_reservation_refuses_start(policy_runtime):
    from harness.execution_contract import digest

    r = policy_runtime
    dependency_fixture(r)
    configure(r, document(r, maximum=20))
    a = attempt(r)
    receipt = r.state["integration"]["tasks"]["producer"]["authored_output"]
    receipt["candidate_identity"]["scope_fingerprint"] = "different-current-candidate"
    receipt["receipt_hash"] = digest(
        {key: value for key, value in receipt.items() if key != "receipt_hash"}
    )
    r.save()
    with pytest.raises(ValueError, match="AUTHORITY_STALE"):
        a.start()
    a.release_unstarted()


def test_legacy_admission_and_enrollment_have_one_serialized_boundary(policy_runtime):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import closing
    from types import SimpleNamespace

    from harness.controller import HarnessController
    from harness.db import connect
    from harness.policy.configuration import install

    r = policy_runtime
    decision = r.authorize(document(r, maximum=20))
    barrier = threading.Barrier(2)

    def legacy():
        with closing(connect(r.operation.database)) as conn:
            local = SimpleNamespace(**r.__dict__)
            local.controller = HarnessController(conn)
            barrier.wait()
            try:
                return attempt(local)
            except ValueError as error:
                return str(error)

    def enrollment():
        with closing(connect(r.operation.database)) as conn:
            barrier.wait()
            try:
                return install(
                    HarnessController(conn), r.operation.execution_id, decision, 0, "legacy-0", 0
                )
            except ValueError as error:
                return str(error)

    with ThreadPoolExecutor(2) as executor:
        a = executor.submit(legacy)
        b = executor.submit(enrollment)
        admitted, enrolled = a.result(), b.result()
    if isinstance(enrolled, dict):
        assert isinstance(admitted, str) or not admitted.legacy
    else:
        assert enrolled == "UNSUPPORTED_ENFORCEMENT_CAPABILITY"
        assert not isinstance(admitted, str) and admitted.legacy
    if not isinstance(admitted, str):
        admitted.release_unstarted()


def test_same_database_other_execution_cannot_bypass_open_resource_claim(
    policy_runtime, monkeypatch
):
    import copy
    import json
    import threading
    import uuid
    from pathlib import Path

    from harness.execution_contract import canonical
    from harness.execution_handoff import read_handoff
    from harness.execution_hosts import al_source
    from harness.execution_principals import PrincipalRegistry
    from harness.execution_projection import ingest_event
    from tests.execution_helpers import approved_execution, claimed, event
    from tests.test_policy_admission import patch

    r = policy_runtime
    configure(r, document(r, maximum=20))
    a = attempt(r)
    a.start()
    a.finish(closed=False)
    _, eid = approved_execution(
        r.controller,
        Path(r.operation.envelope["repository"]),
        monkeypatch,
        host="codex",
        legacy=True,
    )
    claimed(r.controller.conn, eid)
    record = event(r.controller.conn, eid, 1, "run.started", {"status": "RUNNING"})
    record["event_id"] = "cross-execution-start"
    ingest_event(r.controller.conn, eid, record)
    execution = get_execution(r.controller.conn, eid)
    other = copy.copy(r.operation)
    other.execution_id = eid
    from harness.execution_operations import OperationController

    other.state = OperationController.state.__get__(other)
    other.envelope = read_handoff(execution["handoff_path"])
    other.lock = threading.RLock()
    other.initialization = dict(
        other.initialization, source=str(al_source(execution, other.envelope))
    )
    other.registry = PrincipalRegistry(
        Path(execution["handoff_path"]).parent / "principals-other",
        r.operation.registry.native_root,
        execution["binding"],
        "safe-policy",
    )
    root, child = str(uuid.uuid4()), str(uuid.uuid4())
    directory = next(other.registry.native_root.glob("sessions/*/*/*"))
    for identifier, source in (
        (root, "exec"),
        (
            child,
            {"subagent": {"thread_spawn": {"parent_thread_id": root, "agent_role": "al-engineer"}}},
        ),
    ):
        (directory / ("rollout-" + identifier + ".jsonl")).write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": identifier,
                        "session_id": identifier,
                        "cli_version": "0.165.0",
                        "source": source,
                    },
                }
            )
            + "\n"
        )
    other.registry.observe_root_start(root)
    principal = other.registry.enroll(child)
    state = copy.deepcopy(r.state)
    state["run_id"] = execution["al_run_id"]
    state["integration"]["binding"] = execution["binding"] | {
        "handoff_hash": execution["handoff_hash"],
        "al_session_id": execution["al_session_id"],
        "al_run_id": execution["al_run_id"],
    }
    state["writers"]["w"]["agent"] = child
    path = Path(other.initialization["source"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(state))
    other.assignments = {child: {"task": "work", "revision": r.revision}}
    del other._policy_owner
    del other._policy_supervisor
    with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
        other.dispatch(principal, "apply_patch", patch(r))
    assert other.dispatch(principal, "apply_patch", patch(r, "svc/independent-other.txt"))[
        "changed"
    ] == ["svc/independent-other.txt"]
