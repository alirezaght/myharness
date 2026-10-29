# ruff: noqa: F811
"""Immutable accepted requirement ordering and comparison anchors."""

import sys

from al.installer.runtime_successor_lineage import revisions


def test_ordered_requirement_hashes_preserve_original_package():
    original = "a" * 64
    first = {"artifact": {"sha256": "b" * 64}}
    second = {"artifact": {"sha256": "c" * 64}}
    empty = revisions(original, [])
    one = revisions(original, [first])
    two = revisions(original, [first, second])
    assert one[:1] == empty and two[:2] == one and len(two) == 3
    assert two[-1] != revisions(original, [second, first])[-1]


import json
import os
import subprocess
from pathlib import Path

import pytest

from al.tests.runtime_run_helpers import launched  # noqa: F401
from al.tests.test_runtime_successor import prepare
from harness.execution_decisions import record_execution_decision
from harness.execution_hosts import al_argv
from harness.execution_worker import replay_source
from tests.conftest import conn, controller, db_path, isolated_home  # noqa: F401
from tests.test_template_classification_integration import operations, registered  # noqa: F401


def command(data, *args):
    _, _, _, _, package, execution, envelope = data
    result = subprocess.run(
        al_argv(
            package["launcher"],
            "codex",
            envelope["repository"],
            execution["al_session_id"],
            *args,
            existing_run=True,
        ),
        cwd=envelope["repository"],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (result.stdout[-1500:], result.stderr[-1500:])
    return result


def report(data, operation, payload, directory):
    path = directory / (operation + ".json")
    path.write_text(json.dumps(payload))
    command(data, "integration", operation, "--file", str(path))


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS prepared-start containment")
def test_real_accepted_amendments_survive_without_old_task_authority(registered, tmp_path, count):
    data = registered[0]
    _, _, controller, initialization, _, execution, _ = data
    report(
        data,
        "task-register",
        {
            "id": "A",
            "title": "Scoped authored work",
            "criteria": ["AC-01"],
            "files": ["app"],
            "dependencies": [],
        },
        tmp_path,
    )
    for index in range(count):
        blocker = "design-" + str(index)
        report(
            data,
            "blocker",
            {
                "id": blocker,
                "kind": "developer_engineering_decision",
                "tasks": ["A"],
                "question": "Choose the scoped engineering approach",
                "context": "Synthetic explicit developer engineering decision",
                "alternatives": ["first", "second"],
                "recommendation": "first",
            },
            tmp_path,
        )
        replay_source(controller.conn, execution["id"], "test-owner")
        decision = record_execution_decision(
            controller,
            execution["id"],
            blocker,
            blocker_revision=1,
            request_id="amend-" + str(index),
            verdict="alternative",
            decision_text="Use the explicit alternative for this scoped synthetic acceptance criterion.",
            tasks=["A"],
            criteria=["AC-01"],
            amendment=True,
        )
        report(data, "decision", json.loads(decision["delivery"]), tmp_path)
        replay_source(controller.conn, execution["id"], "test-owner")
    before = Path(initialization["source"]).read_bytes()
    old = json.loads(before)
    assert any(row["state"] == "Required" for row in old["integration"]["revalidations"].values())
    receipt, _ = prepare(registered)
    new = json.loads(Path(receipt["successor_state_ref"]["stored_path"]).read_bytes())
    assert revisions(new["task_package"]["sha256"], new["amendments"]) == revisions(
        old["task_package"]["sha256"], old["amendments"]
    )
    assert [a["artifact"]["sha256"] for a in new["amendments"]] == [
        a["artifact"]["sha256"] for a in old["amendments"]
    ]
    assert new["integration"]["tasks"] == {} and new["integration"]["deliveries"] == {}
    assert (
        new["integration"]["revalidations"] == {} and new["proof_admission"]["status"] == "pending"
    )
    assert Path(initialization["source"]).read_bytes() == before


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS prepared-start containment")
def test_original_owned_contribution_survives_successor(registered, tmp_path):
    import sys

    data = registered[0]
    _, _, _, initialization, _, _, envelope = data
    root = Path(envelope["repository"])
    command(data, "spec-gate", "SPEC_READY")
    command(data, "classify", "STANDARD", "--designer", "no", "--implementers", "al-engineer")
    report(
        data,
        "task-register",
        {
            "id": "A",
            "title": "Scoped source work",
            "criteria": ["AC-01"],
            "files": ["app"],
            "dependencies": [],
        },
        tmp_path,
    )
    command(data, "phase", "IMPLEMENTATION")
    report(
        data,
        "agent-register",
        {
            "id": "producer",
            "name": "Observed synthetic producer",
            "role": "al-engineer",
            "task": "A",
        },
        tmp_path,
    )
    report(data, "agent-update", {"id": "producer", "status": "Running"}, tmp_path)
    command(data, "integration", "task-start", "--task", "A")
    command(
        data,
        "writer",
        "start",
        "--id",
        "owned-source",
        "--role",
        "al-engineer",
        "--files",
        "app/owned.py",
        "--task",
        "A",
        "--agent",
        "producer",
    )
    (root / "app/owned.py").write_text("owned = 42\n")
    command(data, "writer", "finish", "--id", "owned-source", "--outcome", "success")
    report(data, "agent-update", {"id": "producer", "status": "Completed"}, tmp_path)
    receipt, _ = prepare(registered)
    state_path = receipt["successor_state_ref"]["stored_path"]
    scripts = Path(initialization["pin_root"]) / "payload/codex/skills/al/scripts"
    predecessor = receipt["predecessor_state_ref"]["stored_path"]
    program = (
        "import sys,json;from pathlib import Path;sys.path.insert(0,"
        + repr(str(scripts))
        + ");import al_inherited_lineage as L;st=json.loads(Path("
        + repr(state_path)
        + ").read_bytes());old=json.loads(Path("
        + repr(predecessor)
        + ").read_bytes());result=L.compose([old,st],st);"
        + 'assert "app/owned.py" in result["task_owned"],result;'
        + 'assert "app/owned.py" not in result["unchanged_preexisting"];'
        + 'print("owned lineage verified")'
    )

    def verify():
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", program], cwd=root, capture_output=True, timeout=30
        )
        assert result.returncode == 0, result.stderr[-1500:]
        assert result.stdout == b"owned lineage verified\n"

    verify()
    # Only this newly created pytest repository may have its fixture index/HEAD changed.
    assert root.resolve() == (tmp_path / "repo").resolve()
    shared_checkout = Path(__file__).resolve().parents[2]
    assert root.is_relative_to(tmp_path) and root != shared_checkout
    assert not shared_checkout.is_relative_to(root)
    for arguments in (("add", "app/owned.py"), ("commit", "-qm", "synthetic owned output")):
        result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True)
        assert result.returncode == 0, result.stderr
    verify()
