# ruff: noqa: F811
"""Actual developer provenance and public-dispatch accounting, including competing admissions."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.db import connect
from harness.execution_contract import canonical, digest
from harness.execution_decisions import record_execution_decision
from harness.execution_hosts import al_source
from harness.execution_operations import OperationController
from harness.execution_projection import ingest_event
from harness.execution_store import get_execution
from harness.policy.configuration import install
from harness.policy.resources import prepare
from harness.reliability import store
from harness.reliability.process_context import dispatch_scope
from tests.execution_helpers import binding, event, started
from tests.test_reliability_processes import processes  # noqa: F401
from tests.test_reliability_store import observed  # noqa: F401


@pytest.fixture
def policy_runtime(processes):
    op, root, controller = processes
    started(controller.conn, op.execution_id)
    execution = get_execution(controller.conn, op.execution_id)
    task = {
        "id": "work",
        "title": "Owned work",
        "status": "Running",
        "dependencies": [],
        "files": ["svc"],
        "criteria": ["AC-01"],
        "revision": 1,
        "admission": {"revision": 1, "graph_revision": 1},
    }
    ingest_event(
        controller.conn,
        op.execution_id,
        event(controller.conn, op.execution_id, 2, "task.registered", task),
    )
    child = str(uuid.uuid4())
    records = next(op.registry.native_root.glob("sessions/*/*/*"))
    (records / ("rollout-" + child + ".jsonl")).write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": child,
                    "session_id": child,
                    "cli_version": "0.165.0",
                    "source": {
                        "subagent": {
                            "thread_spawn": {
                                "parent_thread_id": root["thread_id"],
                                "agent_role": "al-engineer",
                            }
                        }
                    },
                },
            }
        )
        + "\n"
    )
    principal = op.registry.enroll(child)
    state = {
        "run_id": execution["al_run_id"],
        "status": "RUNNING",
        "task_package": {"sha256": "a" * 64},
        "amendments": [],
        "writers": {
            "w": {
                "id": "w",
                "task": "work",
                "agent": child,
                "state": "ACTIVE",
                "role": "al-engineer",
                "files": ["svc"],
            }
        },
        "integration": {
            "binding": binding(controller.conn, op.execution_id),
            "tasks": {"work": task},
            "graph_revision": 1,
            "blockers": {},
            "revalidations": {},
            "deliveries": {},
        },
    }
    source = al_source(execution, op.envelope)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(canonical(state))
    op.state = OperationController.state.__get__(op)
    revision = digest({"task": "work", "revision": 1, "graph_revision": 1, "amendments": []})
    op.assignments = {child: {"task": "work", "revision": revision}}
    op.generated = {}
    op.active_report = None
    op.check_channels = []
    op.directory = Path(execution["handoff_path"]).parent / "operations"
    op.directory.mkdir(exist_ok=True)
    runtime = SimpleNamespace(
        operation=op,
        principal=principal,
        root=root,
        controller=controller,
        source=source,
        state=state,
        revision=revision,
    )

    def save():
        source.write_text(canonical(state))

    runtime.save = save

    def authorize(document, action="install"):
        seq = (
            controller.conn.execute(
                "SELECT COUNT(*) FROM execution_events WHERE execution_id=?", (op.execution_id,)
            ).fetchone()[0]
            + 1
        )
        bid = f"policy-{seq}"
        blocker = {
            "id": bid,
            "revision": 1,
            "tasks": ["work"],
            "affected": ["work"],
            "question": "Explicit policy authorization?",
            "context": "Operator requested a fixed structured authorization",
            "alternatives": ["accept", "reject"],
            "recommendation": "accept",
        }
        ingest_event(
            controller.conn,
            op.execution_id,
            event(controller.conn, op.execution_id, seq, "blocker.raised", blocker),
        )
        value = {
            "schema_version": 1,
            "kind": "runtime-policy-authorization",
            "action": action,
            "binding": binding(controller.conn, op.execution_id),
            "document": document,
        }
        result = record_execution_decision(
            controller,
            op.execution_id,
            bid,
            blocker_revision=1,
            request_id=bid,
            verdict="accept",
            decision_text=canonical(value),
            tasks=["work"],
            criteria=["AC-01"],
        )
        ack = {
            "delivery_id": result["id"],
            "blocker_id": bid,
            "state": "Resolved",
            "decision_hash": digest(json.loads(result["delivery"])),
        }
        ingest_event(
            controller.conn,
            op.execution_id,
            event(controller.conn, op.execution_id, seq + 1, "decision.acknowledged", ack),
        )
        state["integration"]["deliveries"][result["id"]] = ack
        save()
        return result["id"]

    runtime.authorize = authorize
    return runtime


def document(
    runtime, *, maximum=1, dimension="mutating_operations", rules=None, policy="base", version=1
):
    return {
        "schema_version": 1,
        "policy_id": policy,
        "version": version,
        "scope_kind": "execution",
        "scope_ref": runtime.operation.execution_id,
        "parents": [],
        "rules": rules or {},
        "limits": [
            {
                "pool_id": policy + "-pool",
                "dimension": dimension,
                "domain": "execution_tree",
                "owner_ref": runtime.operation.execution_id,
                "maximum": maximum,
            }
        ],
    }


def configure(runtime, doc=None, revision=0):
    doc = doc or document(runtime)
    did = runtime.authorize(doc)
    return install(runtime.controller, runtime.operation.execution_id, did, revision, "legacy-0", 0)


def patch(runtime, path="svc/new.txt"):
    return {
        "patch": f"*** Begin Patch\n*** Add File: {path}\n+safe\n*** End Patch",
        "task_id": "work",
        "expected_revision": runtime.revision,
    }


def attempt(runtime):
    op = runtime.operation
    observation = store.begin(op, runtime.principal, "apply_patch")
    with dispatch_scope(op, observation, runtime.principal["thread_id"]):
        return prepare(
            op, runtime.controller, runtime.principal, "apply_patch", patch(runtime), runtime.state
        )


def test_real_dispatch_consumes_once_and_exhaustion_prevents_first_mkdir(policy_runtime):
    r = policy_runtime
    configure(r)
    assert r.operation.dispatch(r.principal, "apply_patch", patch(r))["changed"] == ["svc/new.txt"]
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        r.operation.dispatch(r.principal, "apply_patch", patch(r, "svc/never/secret.txt"))
    assert not (Path(r.operation.envelope["repository"]) / "svc/never").exists()
    pool = r.controller.conn.execute("SELECT * FROM policy_pools").fetchone()
    assert (pool["consumed"], pool["reserved"]) == (1, 0)
    assert r.controller.conn.execute("SELECT state FROM policy_attempts").fetchone()[0] == "settled"


def test_two_concurrent_last_unit_admissions_are_atomic(policy_runtime):
    r = policy_runtime
    configure(r)

    def compete(_):
        try:
            # Distinct connections represent independent authenticated supervisors sharing one pool.
            with closing(connect(r.operation.database)) as conn:
                from harness.controller import HarnessController

                local = SimpleNamespace(**r.__dict__)
                local.controller = HarnessController(conn)
                return attempt(local)
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(compete, range(2)))
    assert sum(not isinstance(v, str) for v in results) == 1
    assert "BUDGET_EXHAUSTED" in results
    row = r.controller.conn.execute("SELECT * FROM policy_pools").fetchone()
    assert row["reserved"] == 1 and row["consumed"] == 0


def test_overlapping_pool_failure_rolls_back_every_reservation(policy_runtime):
    r = policy_runtime
    doc = document(r, maximum=3)
    doc["limits"].append(
        {
            "pool_id": "empty",
            "dimension": "mutating_operations",
            "domain": "execution_tree",
            "owner_ref": r.operation.execution_id,
            "maximum": 0,
        }
    )
    configure(r, doc)
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        attempt(r)
    assert all(
        row[:] == (0, 0)
        for row in r.controller.conn.execute("SELECT consumed,reserved FROM policy_pools")
    )
    assert r.controller.conn.execute("SELECT COUNT(*) FROM policy_attempts").fetchone()[0] == 0


def test_parent_and_child_compete_for_identical_execution_tree_pool(policy_runtime):
    r = policy_runtime
    configure(r)
    root_observation = store.begin(r.operation, r.root, "diagram_operation")
    with dispatch_scope(r.operation, root_observation, r.root["thread_id"]):
        parent = prepare(
            r.operation,
            r.controller,
            r.root,
            "diagram_operation",
            {"operation": "publish", "content": "{}", "expected_graph_revision": 1},
            r.state,
        )
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        attempt(r)
    parent.start()
    parent.owner_finished()
    assert r.controller.conn.execute("SELECT consumed,reserved FROM policy_pools").fetchone()[
        :
    ] == (1, 0)
