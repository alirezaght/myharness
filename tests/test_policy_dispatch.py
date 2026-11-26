# ruff: noqa: F811
"""Real contained effect, durable process identity, deadlines and cleanup after exhaustion."""

import sys

import pytest

from tests.test_policy_admission import (  # noqa: F401
    configure,
    document,
    observed,
    patch,
    policy_runtime,
    processes,
)


def command(r, code):
    return {
        "argv": [sys.executable, "-c", code],
        "cwd": ".",
        "task_id": "work",
        "expected_revision": r.revision,
        "timeout": 5,
    }


def test_untouched_legacy_timeout_remains_best_effort_without_strict_claims(policy_runtime):
    from pathlib import Path

    r = policy_runtime
    (Path(r.operation.envelope["repository"]) / "svc").mkdir(exist_ok=True)
    request = command(r, "import time;time.sleep(3)") | {"timeout": 1}
    result = r.operation.dispatch(r.principal, "run_command", request)
    record = r.operation.processes._owned(r.principal["thread_id"], result["handle"])
    record["thread"].join(8)
    assert not record["thread"].is_alive()
    collected = r.operation.dispatch(r.principal, "poll_process", {"handle": result["handle"]})
    assert collected["status"] == "Failed" and record["stop_reason"] == "deadline_exceeded"
    row = r.controller.conn.execute("SELECT * FROM policy_processes").fetchone()
    assert row["pid"] > 0 and row["parent_reaped"] and row["boundary_closed"] == 0
    assert (
        r.controller.conn.execute("SELECT COUNT(*) FROM policy_resource_claims").fetchone()[0] == 0
    )
    assert r.controller.conn.execute("SELECT legacy FROM policy_attempts").fetchone()[0] == 1
    assert r.operation.dispatch(r.principal, "apply_patch", patch(r))["changed"] == ["svc/new.txt"]
    # A later strict enrollment cannot mistake observed parent cleanup for closure.
    with pytest.raises(ValueError, match="UNSUPPORTED_ENFORCEMENT_CAPABILITY"):
        configure(r)


def test_enrolled_arbitrary_process_refuses_before_effect_or_budget_charge(policy_runtime):
    from pathlib import Path

    r = policy_runtime
    configure(r)
    with pytest.raises(ValueError, match="UNSUPPORTED_ENFORCEMENT_CAPABILITY"):
        r.operation.dispatch(
            r.principal, "run_command", command(r, "open('svc/no-effect','w').write('bad')")
        )
    assert not (Path(r.operation.envelope["repository"]) / "svc/no-effect").exists()
    assert not r.operation.processes.records
    assert r.controller.conn.execute("SELECT consumed,reserved FROM policy_pools").fetchone()[
        :
    ] == (0, 0)


@pytest.mark.parametrize("dimension", ["operation_deadline_ms", "execution_elapsed_ms"])
def test_explicit_strict_time_refuses_arbitrary_command_before_effect(policy_runtime, dimension):
    from pathlib import Path

    r = policy_runtime
    configure(r, document(r, dimension=dimension, maximum=5000))
    with pytest.raises(ValueError, match="UNSUPPORTED_ENFORCEMENT_CAPABILITY"):
        r.operation.dispatch(
            r.principal, "run_command", command(r, "open('svc/no-effect','w').write('bad')")
        )
    assert not (Path(r.operation.envelope["repository"]) / "svc/no-effect").exists()
    assert not r.operation.processes.records


@pytest.fixture
def protected_policy_runtime(operations):
    """Actual protected AL origin plus real decision delivery, without native provider claims."""
    import json
    from types import SimpleNamespace

    from harness.execution_contract import canonical, digest
    from harness.execution_decisions import record_execution_decision
    from harness.execution_projection import ingest_event
    from harness.execution_store import get_execution
    from tests.execution_helpers import event

    op, old_principal, controller, initialization, _, execution, _ = operations
    from tests.test_execution_operations import observed_principal

    principal = observed_principal(op, op.registry.root, "orchestrator")
    source = __import__("pathlib").Path(initialization["source"])
    state = json.loads(source.read_bytes())
    task = {
        "id": "work",
        "title": "Policy operator fixture",
        "status": "Idle",
        "revision": 1,
        "dependencies": [],
        "files": ["svc"],
        "criteria": ["AC-01"],
        "progress": None,
        "registration": "fixture",
    }
    state["integration"]["tasks"]["work"] = task
    source.write_text(canonical(state))

    def emit(kind, payload):
        seq = (
            controller.conn.execute(
                "SELECT MAX(sequence) FROM execution_events WHERE execution_id=?",
                (op.execution_id,),
            ).fetchone()[0]
            + 1
        )
        record = event(controller.conn, op.execution_id, seq, kind, payload)
        record["event_id"] = "policy-fixture-" + str(seq)
        record["binding"] = state["integration"]["binding"]
        ingest_event(controller.conn, op.execution_id, record)

    emit("task.registered", task)
    r = SimpleNamespace(
        operation=op,
        principal=principal,
        root=principal,
        controller=controller,
        source=source,
        state=state,
    )

    def authorize(doc, action="install"):
        bid = "policy-blocker-" + str(
            controller.conn.execute("SELECT COUNT(*) FROM execution_decisions").fetchone()[0]
        )
        emit(
            "blocker.raised",
            {
                "id": bid,
                "revision": 1,
                "tasks": ["work"],
                "question": "Approve this fixed runtime policy?",
                "context": "Explicit developer control",
                "alternatives": ["accept", "reject"],
                "recommendation": "accept",
            },
        )
        binding = get_execution(controller.conn, op.execution_id)["binding"] | {
            "handoff_hash": execution["handoff_hash"],
            "al_session_id": execution["al_session_id"],
            "al_run_id": execution["al_run_id"],
        }
        value = {
            "schema_version": 1,
            "kind": "runtime-policy-authorization",
            "action": action,
            "binding": binding,
            "document": doc,
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
        emit("decision.acknowledged", ack)
        current = json.loads(source.read_bytes())
        current["integration"]["deliveries"][result["id"]] = ack
        source.write_text(canonical(current))
        return result["id"]

    r.authorize = authorize
    return r


def seed_protected_failure(r):
    """Prepare the issuer's genuine failure before governed resource enrollment."""
    import os
    import subprocess

    from harness.execution_hosts import al_argv

    args = [
        "agent-failed",
        "al-engineer",
        "--reason",
        "implementation runner error",
        "--retry-safe",
        "yes",
    ]
    command = al_argv(
        r.operation.package["launcher"],
        r.operation.envelope["host"],
        r.operation.envelope["repository"],
        r.operation.envelope["al_session_id"],
        *args,
        existing_run=True,
    )
    result = subprocess.run(
        command,
        cwd=r.operation.envelope["repository"],
        env=dict(os.environ),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(
    sys.platform != "darwin", reason="positive sealed retry requires qualified Darwin control"
)
@pytest.mark.parametrize("background", [False, True])
def test_actual_protected_al_retry_is_one_shared_charge_and_saves_grant(
    protected_policy_runtime, background
):
    import json

    r = protected_policy_runtime
    doc = document(r, maximum=10)
    doc["limits"].append(
        {
            "pool_id": "retry-pool",
            "dimension": "retry_attempts",
            "domain": "execution_tree",
            "owner_ref": r.operation.execution_id,
            "maximum": 1,
        }
    )
    seed_protected_failure(r)
    configure(r, doc)
    request = {
        "arguments": [
            "retry",
            "al-engineer",
            "--reason",
            "Inspected checkpoint and safe runner state.",
        ],
        "background": background,
    }
    result = r.operation.dispatch(r.principal, "al_command", request)
    if background:
        record = r.operation.processes._owned(r.principal["thread_id"], result["handle"])
        record["thread"].join(20)
        assert not record["thread"].is_alive()
        result = r.operation.dispatch(r.principal, "poll_process", {"handle": result["handle"]})
    assert result["returncode"] == 0, result
    state = json.loads(r.source.read_bytes())
    history = state["retry_history"][-1]
    row = r.controller.conn.execute(
        "SELECT * FROM policy_attempts WHERE id=?", (history["policy_attempt_id"],)
    ).fetchone()
    assert row["retry_claim"] == history["policy_grant_id"] and row["state"] == "settled"
    assert r.controller.conn.execute(
        "SELECT consumed,reserved FROM policy_pools WHERE dimension='retry_attempts'"
    ).fetchone()[:] == (1, 0)
    assert (
        r.controller.conn.execute(
            "SELECT consumed FROM policy_pools WHERE dimension='mutating_operations'"
        ).fetchone()[0]
        == 1
    )
    assert state["retries"]["al-engineer"] == 1


# Imported fixture dependencies remain explicit across test modules.
from tests.test_execution_operations import operations  # noqa: E402,F401


@pytest.mark.skipif(
    sys.platform != "darwin", reason="escaped-session proof requires Darwin process boundary"
)
def test_escaped_session_is_not_cleared_by_initial_group_absence(policy_runtime):
    import json
    import os
    import signal
    import time
    from pathlib import Path

    r = policy_runtime
    marker = Path(r.operation.envelope["repository"]) / "svc/escaped-instance.json"
    marker.parent.mkdir(exist_ok=True)
    child = (
        "import os,time,json;open('svc/escaped-instance.json','w').write("
        "json.dumps({'pid':os.getpid(),'sid':os.getsid(0)}));time.sleep(5)"
    )
    parent = (
        f"import subprocess,sys;subprocess.Popen([sys.executable,'-c',{child!r}],"
        "start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL)"
    )
    result = r.operation.dispatch(r.principal, "run_command", command(r, parent))
    record = r.operation.processes._owned(r.principal["thread_id"], result["handle"])
    record["thread"].join(5)
    deadline = time.monotonic() + 3
    escaped = None
    while escaped is None and time.monotonic() < deadline:
        try:
            escaped = json.loads(marker.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.02)
    assert escaped is not None
    try:
        assert escaped["pid"] == escaped["sid"] and escaped["pid"] != record["process"].pid
        os.kill(escaped["pid"], 0)
        row = r.controller.conn.execute("SELECT * FROM policy_processes").fetchone()
        assert row["parent_reaped"] and row["owned_group_absent"] and not row["boundary_closed"]
        assert (
            r.controller.conn.execute("SELECT state FROM policy_attempts").fetchone()[0]
            == "unresolved"
        )
        assert r.controller.conn.execute("SELECT legacy FROM policy_attempts").fetchone()[0] == 1
        with pytest.raises(ValueError, match="UNSUPPORTED_ENFORCEMENT_CAPABILITY"):
            configure(r)
    finally:
        try:
            os.kill(escaped["pid"], signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux PID namespace containment proof"
)
def test_linux_pid_namespace_prevents_session_child_survival(policy_runtime):
    import json
    import time
    from pathlib import Path

    r = policy_runtime
    root = Path(r.operation.envelope["repository"])
    service = root / "svc"
    service.mkdir(exist_ok=True)
    ready = service / "session-child-ready.json"
    pending = service / "session-child-ready.tmp"
    canary = service / "session-child-late-canary"
    ready.unlink(missing_ok=True)
    pending.unlink(missing_ok=True)
    canary.unlink(missing_ok=True)
    child = (
        "import json,os,time\n"
        "pending=" + repr(str(pending)) + "\n"
        "ready=" + repr(str(ready)) + "\n"
        "with open(pending,'w') as stream:\n"
        " json.dump({'pid':os.getpid(),'ppid':os.getppid(),'sid':os.getsid(0)},stream)\n"
        "os.replace(pending,ready)\n"
        "time.sleep(3.5)\n"
        "with open(" + repr(str(canary)) + ",'w') as stream:stream.write('survived')\n"
    )
    parent = (
        "import json,pathlib,subprocess,sys,time\n"
        "p=subprocess.Popen([sys.executable,'-c',"
        + repr(child)
        + "],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL)\n"
        "deadline=time.monotonic()+3\n"
        "ready=pathlib.Path(" + repr(str(ready)) + ")\n"
        "identity=None\n"
        "while time.monotonic()<deadline:\n"
        " try:\n"
        "  candidate=json.loads(ready.read_text())\n"
        "  if (all(type(candidate.get(key)) is int and candidate[key]>0 for key in "
        "('pid','ppid','sid')) and candidate['pid']==candidate['sid'] and "
        "candidate['pid']!=candidate['ppid']):\n"
        "   identity=candidate\n"
        "   break\n"
        " except (FileNotFoundError,json.JSONDecodeError):\n"
        "  pass\n"
        " time.sleep(.01)\n"
        "raise SystemExit(0 if identity is not None else 31)\n"
    )
    result = r.operation.dispatch(r.principal, "run_command", command(r, parent))
    record = r.operation.processes._owned(r.principal["thread_id"], result["handle"])
    try:
        record["thread"].join(8)
        assert not record["thread"].is_alive()
        completed = r.operation.dispatch(r.principal, "poll_process", {"handle": result["handle"]})
        assert completed["status"] == "Exited"
        assert record["process"].returncode == 0
        child_identity = json.loads(ready.read_text())
        assert all(
            type(child_identity[key]) is int and child_identity[key] > 0
            for key in ("pid", "ppid", "sid")
        )
        assert child_identity["pid"] == child_identity["sid"]
        assert child_identity["pid"] != child_identity["ppid"]
        # Observe beyond the child's delayed-write deadline without reading its PID as a host PID.
        deadline = time.monotonic() + 5
        while not canary.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not canary.exists()
        row = r.controller.conn.execute("SELECT * FROM policy_processes").fetchone()
        assert row["parent_reaped"] and row["boundary_closed"] == 0
        assert r.controller.conn.execute("SELECT legacy FROM policy_attempts").fetchone()[0] == 1
    finally:
        if record["thread"].is_alive():
            r.operation.dispatch(r.principal, "cancel_process", {"handle": result["handle"]})
            record["thread"].join(8)


@pytest.mark.skipif(
    sys.platform != "darwin", reason="positive sealed retry requires qualified Darwin control"
)
def test_actual_post_grant_checkpoint_drift_retains_charge_without_recovery(
    protected_policy_runtime, monkeypatch
):
    import json
    from pathlib import Path

    from harness.policy.retry_channel import RetryChannel

    r = protected_policy_runtime
    doc = document(r, maximum=10)
    doc["limits"].append(
        {
            "pool_id": "retry-pool",
            "dimension": "retry_attempts",
            "domain": "execution_tree",
            "owner_ref": r.operation.execution_id,
            "maximum": 5,
        }
    )
    seed_protected_failure(r)
    configure(r, doc)
    before = json.loads(r.source.read_bytes())
    path = Path(before["failure"]["checkpoint"]["stored_path"])
    original = path.read_bytes()
    claim = RetryChannel.claim

    def commit_then_drift(channel, request):
        result = claim(channel, request)
        path.write_bytes(original + b"\n")
        return result

    monkeypatch.setattr(RetryChannel, "claim", commit_then_drift)
    try:
        result = r.operation.dispatch(
            r.principal,
            "al_command",
            {
                "arguments": [
                    "retry",
                    "al-engineer",
                    "--reason",
                    "Inspected safe failure checkpoint.",
                ]
            },
        )
        assert result["returncode"] != 0
        after = json.loads(r.source.read_bytes())
        assert after["failure"] == before["failure"] and after["retries"] == before["retries"]
        assert after.get("retry_history", []) == before.get("retry_history", [])
        assert r.controller.conn.execute(
            "SELECT consumed,reserved FROM policy_pools WHERE dimension='retry_attempts'"
        ).fetchone()[:] == (1, 0)
        row = r.controller.conn.execute(
            "SELECT state,outcome,retry_claim FROM policy_attempts WHERE retry_claim IS NOT NULL"
        ).fetchone()
        assert row["state"] == "settled" and row["outcome"] == "failed" and row["retry_claim"]
        receipt = r.controller.conn.execute(
            "SELECT state,exit_code FROM policy_control_receipts"
        ).fetchone()
        assert receipt["state"] == "closed" and receipt["exit_code"] == result["returncode"]
        claim = r.controller.conn.execute("SELECT * FROM policy_retry_claims").fetchone()
        assert claim["checkpoint_ref"] == before["failure"]["checkpoint"]["id"]
        assert claim["target_role"] == "al-engineer" and claim["al_run_id"] == before["run_id"]
        assert claim["grant_id"] == row["retry_claim"]
        attempt = r.controller.conn.execute(
            "SELECT retry_reference FROM policy_attempts WHERE id=?", (claim["attempt_id"],)
        ).fetchone()
        assert json.loads(attempt[0])[-1] == before["failure"]["checkpoint"]["id"]
        with pytest.raises(ValueError, match="ATTEMPT_NOT_EXECUTABLE"):
            r.operation.dispatch(
                r.principal,
                "al_command",
                {
                    "arguments": [
                        "retry",
                        "al-engineer",
                        "--reason",
                        "Do not replay an uncertain committed grant.",
                    ]
                },
            )
        assert (
            r.controller.conn.execute("SELECT COUNT(*) FROM policy_retry_claims").fetchone()[0] == 1
        )
    finally:
        path.write_bytes(original)
