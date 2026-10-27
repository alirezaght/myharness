"""A real protected standalone plan can be reused only as a bound review input."""

import copy
import json
from pathlib import Path

import pytest

from al.tests.editorial_receipt_helpers import prepare_readers, state_path
from harness.approval import ApprovalGate
from harness.execution import start_execution
from harness.execution_adoption import adopt_planning, adoptions
from harness.execution_artifacts import ArtifactStore
from harness.execution_contract import digest
from harness.execution_models import ExecutionConflict
from harness.execution_store import get_execution
from harness.spec_store import SpecStore
from tests.complete_task import settle


@pytest.fixture
def adoption(controller, monkeypatch, launched, tmp_path):
    import harness.execution_adoption as module

    for key, value in launched[4].items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(launched[2])
    source = prepare_readers(launched, "codex", duplicate=False)
    task = controller.create_task(
        "Reuse reviewed planning while preserving approved behavior", "trivial"
    )
    settle(controller, task.id)
    controller.advance(task.id)
    specs = tmp_path / "specs" / task.id
    ApprovalGate(controller, task.id, SpecStore(specs), launched[2]).approve()
    result = start_execution(controller, task.id, host="codex", specs=str(specs), launch=False)
    execution = get_execution(controller.conn, result["execution"]["id"])
    report = source["planning_authorship"]["report"]
    request = {
        "schema_version": 1,
        "source": {
            "repository": str(launched[2]),
            "platform": "codex",
            "session_id": source["session_id"],
            "run_id": source["run_id"],
            "task_sha256": source["task_package"]["sha256"],
            "report_sha256": report["sha256"],
        },
        "target": {"execution_id": execution["id"], "handoff_hash": execution["handoff_hash"]},
        "mapping": {"criteria": {"AC-01": "AC-01"}, "invariants": {}},
        "reason": "Retain the reviewed source plan while the current Architect revalidates it.",
    }
    monkeypatch.setattr(module, "managed_launcher", lambda: str(launched[3]))
    return execution, request, state_path(launched, "codex")


def test_protected_adoption_keeps_both_workflows_and_requires_current_revalidation(
    controller,
    adoption,
):
    execution, request, source = adoption
    before = source.read_bytes()
    result = adopt_planning(
        controller,
        execution["id"],
        request,
        digest(request),
        approval_source="User approved this exact hash-bound planning transfer.",
    )
    assert result["current_architect_revalidation_required"] is True
    assert adoptions(controller, execution["id"]) == [result]
    store = ArtifactStore(Path(execution["handoff_path"]).parent / "operations/drafts")
    receipt = json.loads(
        store.resolve(result["receipt"]["artifact_id"], result["receipt"]["sha256"]).read_bytes()
    )
    assert receipt["transferred_approval"] is False and receipt["transferred_evidence"] == []
    assert (
        receipt["protected_source"]["planning"]["report"]["sha256"]
        == request["source"]["report_sha256"]
    )
    assert get_execution(controller.conn, execution["id"]) == execution
    assert source.read_bytes() == before
    assert (
        adopt_planning(
            controller,
            execution["id"],
            request,
            digest(request),
            approval_source="Replay the same explicit developer planning reuse approval.",
        )
        == result
    )


def test_stale_or_wrong_planning_never_creates_a_transfer(controller, adoption):
    execution, request, source = adoption
    for damaged in ("hash", "run", "mapping", "approval"):
        value = copy.deepcopy(request)
        if damaged == "hash":
            value["source"]["report_sha256"] = "0" * 64
        elif damaged == "run":
            value["source"]["run_id"] = "00000000"
        elif damaged == "mapping":
            value["mapping"]["criteria"] = {}
        with pytest.raises((ExecutionConflict, ValueError)):
            adopt_planning(
                controller,
                execution["id"],
                value,
                "sha256:" + "0" * 64 if damaged == "approval" else digest(value),
                approval_source="User approved a specifically bound source plan only.",
            )
        assert not adoptions(controller, execution["id"])
    (Path(request["source"]["repository"]) / "rows.json").write_text('{"changed":true}')
    with pytest.raises(ExecutionConflict, match="protected planning proof refused"):
        adopt_planning(
            controller,
            execution["id"],
            request,
            digest(request),
            approval_source="User approved the earlier exact source planning artifact.",
        )
    assert not adoptions(controller, execution["id"])
