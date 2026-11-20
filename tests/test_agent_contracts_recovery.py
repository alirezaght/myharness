# ruff: noqa: F811
"""Actual Harness-bound interrupted evidence preserves its authenticated observation."""

import copy
import json
import os
import signal
import sys
import time
from pathlib import Path

from tests.test_execution_continuity import command, prepare, state
from tests.test_execution_operations import operations  # noqa: F401


def test_interrupted_public_harness_check_reconciliation(operations):
    operation, principal, _, _, _, _, envelope = operations
    prepare(operation, principal)
    target = Path(envelope["repository"]) / "work/probe.txt"
    target.parent.mkdir()
    target.write_text("scoped interruption fixture\n")
    command(
        operation,
        principal,
        "evidence",
        "plan",
        "--area",
        "owned-interruption",
        "--kind",
        "check",
        "--scope",
        "work/probe.txt",
        "--",
        sys.executable,
        "-c",
        "import time;time.sleep(60)",
    )
    run = operation.dispatch(
        principal,
        "al_command",
        {
            "arguments": ["evidence", "run", "--area", "owned-interruption"],
            "background": True,
        },
    )
    assert run.get("handle"), run
    record = operation.processes.records[run["handle"]]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        entries = state(operation)["evidence"]["entries"]
        if record["status"] != "Running":
            raise AssertionError(record["output"])
        if entries and entries[-1]["status"] == "RUNNING" and entries[-1].get("pid"):
            runner = entries[-1]["owner_pid"]
            assert os.getpgid(runner) == record["process"].pid
            os.kill(runner, signal.SIGINT)
            break
        time.sleep(0.05)
    else:
        raise AssertionError("actual evidence child did not start")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        observed = operation.dispatch(principal, "poll_process", {"handle": run["handle"]})
        if observed["status"] != "Running":
            break
        time.sleep(0.05)
    assert observed["status"] != "Running", observed
    original = copy.deepcopy(state(operation)["evidence"]["entries"][-1])
    assert original["status"] == "INFRA" and original["interrupted"]
    companion = copy.deepcopy(
        state(operation)["agent_contracts"]["verification-result:" + original["id"]]
    )
    legacy = {
        r["attempt_id"]
        for r in operations[2].conn.execute("SELECT attempt_id FROM policy_legacy_admissions")
    }
    proof = operation.dispatch(
        principal,
        "write_artifact",
        {
            "name": "reconciliation.json",
            "expected_graph_revision": state(operation)["integration"]["graph_revision"],
            "content": json.dumps(
                {
                    "runner_returncode": observed["returncode"],
                    "cleanup": original.get("cleanup"),
                    "entry_id": original["id"],
                }
            ),
        },
    )
    resource = "artifact:" + proof["artifact_id"] + ":" + proof["sha256"]
    command(
        operation,
        principal,
        "check-reconcile",
        "--id",
        original["id"],
        "--reason",
        "Observed own runner termination and inspected the retained cleanup.",
        "--artifact",
        resource,
    )
    command(operation, principal, "evidence", "status")
    current = state(operation)["evidence"]["entries"][-1]
    ref = current["reconciliation"]["observation"]
    retained = json.loads(Path(ref["stored_path"]).read_bytes())
    assert retained == {"schema_version": 1, "entry": original, "contract": companion}
    assert current["status"] == "INFRA"

    after = {
        r["attempt_id"]
        for r in operations[2].conn.execute("SELECT attempt_id FROM policy_legacy_admissions")
    }
    assert legacy <= after
