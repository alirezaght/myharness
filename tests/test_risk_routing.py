"""Clarification and inspected implementation risk drive the lightest useful route."""

import hashlib

import pytest

from harness.classification import Complexity, classify_request
from harness.complexity_assessment import current, record
from harness.state_models import QuestionSource
from harness.task_risk import assess


def assessment(root):
    path = root / "logic.py"
    path.write_text("def retry_limit(): return 2\n")
    return {
        "schema_version": 1,
        "scope": "local",
        "existing_pattern": True,
        "behavior_clear": True,
        "hard_parts": [],
        "pseudocode_benefit": "",
        "evidence": [
            {
                "path": "logic.py",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "reason": "The existing retry policy uses one established configuration seam.",
            }
        ],
        "reason": "Change a bounded existing retry setting and retain its existing semantics.",
    }


@pytest.mark.parametrize(
    "text",
    [
        "Fix the empty-list crash in the distributed queue",
        "Fix a typo in the migration error message",
        "Update the architecture documentation wording",
        "Add one validation branch to the existing state machine",
    ],
)
def test_subject_keywords_do_not_assert_complexity(text):
    assert classify_request(text) is not Complexity.COMPLEX


def test_residual_risk_distinguishes_existing_flow_from_new_design(tmp_path):
    value = assessment(tmp_path)
    assert assess(value, tmp_path) == "SMALL"
    value["hard_parts"] = [
        {
            "kind": "state-machine",
            "new_design": False,
            "description": (
                "Use the existing transition guard without changing coupled state invariants."
            ),
        }
    ]
    assert assess(value, tmp_path) == "SMALL"
    value.update(scope="cross_component", existing_pattern=False)
    assert assess(value, tmp_path) == "STANDARD"
    value["hard_parts"] = [
        {
            "kind": "concurrency",
            "new_design": True,
            "description": (
                "Define atomic retry ownership across simultaneous workers and stale leases."
            ),
        }
    ]
    assert assess(value, tmp_path) == "STANDARD"  # No useful pseudocode step established.
    value["pseudocode_benefit"] = (
        "Enumerate lease races and atomic owner transitions before implementing the new protocol."
    )
    assert assess(value, tmp_path) == "COMPLEX"


def test_uncertainty_requires_clarification_and_changed_evidence_refuses(tmp_path):
    value = assessment(tmp_path)
    value["behavior_clear"] = False
    with pytest.raises(ValueError, match="clarify"):
        assess(value, tmp_path)
    value["behavior_clear"] = True
    (tmp_path / "logic.py").write_text("changed\n")
    with pytest.raises(ValueError, match="changed"):
        assess(value, tmp_path)


def test_reassessment_records_reason_preserves_overrides_and_detects_stale_inputs(
    controller, tmp_path
):
    value = assessment(tmp_path)
    task = controller.create_task("Adjust the existing retry policy in the distributed service")
    assert task.complexity is Complexity.STANDARD
    task = record(controller, task.id, value, tmp_path)
    assert task.complexity is Complexity.SIMPLE and task.complexity_source == "proposed"
    assert current(controller, task.id)["current"]
    ws = controller.workspace(task.id)
    q = ws.ask(
        "edge_cases", "What should happen when retries are exhausted?", QuestionSource.INTERVIEW
    )
    assert not current(controller, task.id)["current"]
    before = len(ws.events())
    with pytest.raises(ValueError, match="resolve"):
        record(controller, task.id, value, tmp_path)
    assert len(ws.events()) == before
    ws.answer(q.id, "Retain the existing failure behavior.")
    record(controller, task.id, value, tmp_path)
    controller.set_complexity(task.id, Complexity.COMPLEX)
    assert record(controller, task.id, value, tmp_path).complexity is Complexity.COMPLEX
    assert current(controller, task.id)["developer_override"]
    (tmp_path / "logic.py").write_text("changed source\n")
    assert not current(controller, task.id)["current"]


def test_current_risk_and_explicit_implementation_choice_reach_the_immutable_handoff(
    controller, sample_repo, monkeypatch
):
    from pathlib import Path

    from harness.approval import ApprovalGate
    from harness.execution import start_execution
    from harness.spec_store import SpecStore
    from tests.complete_task import settle

    monkeypatch.chdir(sample_repo)
    value = assessment(sample_repo)
    task = controller.create_task("Adjust the existing local retry seam", Complexity.TRIVIAL)
    settle(controller, task.id)
    record(controller, task.id, value, sample_repo)
    controller.advance(task.id)
    ApprovalGate(
        controller, task.id, SpecStore(sample_repo / "docs/specs" / task.id), sample_repo
    ).approve()
    result = start_execution(controller, task.id, launch=False)
    from harness.execution_store import get_execution

    saved = get_execution(controller.conn, result["execution"]["id"])
    envelope = __import__("json").loads(Path(saved["handoff_path"]).read_bytes())
    assert envelope["task_package"]["implementation_risk"]["assessment"] == value
    assert envelope["task_package"]["routing_override"]["classification"] == "SMALL"
