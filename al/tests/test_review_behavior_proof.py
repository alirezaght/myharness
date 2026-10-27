"""Fresh assessment is independent, complete and bound to immutable issuance."""

import copy
import json
from pathlib import Path

import pytest

from al.tests.editorial_receipt_helpers import state, state_path, succeed
from al.tests.review_behavior_helpers import (
    comparison_packet,
    ready_for_review,
    report_for,
    save_review,
)
from al.tests.runtime_run_helpers import launched, public_run
from al.tests.test_task_editorial_revision import editorial, request_for


def overlay(launched, platform, identifier, document):
    request = request_for(state(launched, platform), identifier, document)
    request["changes"] = [{"field": "display_summary", "value": "Updated presentation only."}]
    return json.loads(succeed(editorial(launched, platform, request)).stdout)["document"]["hash"]


# Platform-agnostic flow: the shared scripts are byte-identical across platforms (parity test).
@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_actual_review_requires_fresh_counterexamples_and_preserves_inflight_packets(
    launched, platform
):
    issued = ready_for_review(launched, platform)
    packet = issued["content"]
    assert packet["schema_version"] == 2 and packet["kind"] == "review-packet-v2"
    assert len(packet["behavior"]["observations"]) == 2
    assert packet["behavior"]["gaps"] == []
    assert isinstance(packet["evidence"], list)
    assert all(
        "note" not in entry and "reconciliation" not in entry for entry in packet["evidence"]
    )
    assert (
        not {
            "approved_inputs",
            "comparison_contents",
            "fresh",
            "prior_findings",
            "comparison_artifacts",
            "escapes",
        }
        & packet.keys()
    )
    raw = Path(issued["packet"]["stored_path"]).read_bytes()
    report = report_for(launched, platform, issued, "fresh", "independent-fresh-model-response")
    invalid = []
    for missing in ("counterexample_search", "attempt_id", "verification_authority_hash"):
        value = copy.deepcopy(report)
        value.pop(missing)
        invalid.append(value)
    for result in ("found", "blocked"):
        value = copy.deepcopy(report)
        value["counterexample_search"][0]["result"] = result
        invalid.append(value)
    value = copy.deepcopy(report)
    value["counterexample_search"] = []
    invalid.append(value)
    value = copy.deepcopy(report)
    value["counterexample_search"][0]["obligation_ids"] = ["unexamined-consumer"]
    invalid.append(value)
    value = copy.deepcopy(report)
    value["counterexample_search"][0]["inspected_locations"] = []
    invalid.append(value)
    for prior in (None, []):
        value = copy.deepcopy(report)
        value["prior_resolutions"] = prior
        invalid.append(value)
    value = copy.deepcopy(report)
    value["findings"] = [
        {
            "id": "F-null",
            "source": "AC-01",
            "severity": "NON_BLOCKING",
            "description": "A finding with a forbidden fresh resolution key",
            "resolution": None,
        }
    ]
    invalid.append(value)
    path = state_path(launched, platform)
    for value in invalid:
        before = path.read_bytes()
        rejected = save_review(launched, platform, value)
        assert rejected.returncode != 0, value
        assert path.read_bytes() == before
    document = overlay(launched, platform, "during-fresh", packet["document_hash"])
    assert document != packet["document_hash"]
    succeed(save_review(launched, platform, report))
    assert Path(issued["packet"]["stored_path"]).read_bytes() == raw
    current = state(launched, platform)
    receipt = json.loads(
        Path(current["review"]["editorial_validations"][-1]["stored_path"]).read_bytes()
    )
    assert receipt["packet"] == issued["packet"]
    assert receipt["lineage"]["from_document_hash"] == packet["document_hash"]
    assert receipt["lineage"]["to_document_hash"] == document
    compare = comparison_packet(launched, platform)
    assert "comparison-only outline" in json.dumps(compare["content"])
    assert Path(issued["packet"]["stored_path"]).read_bytes() == raw
    comparison = report_for(launched, platform, compare, "compare", report["invocation_id"])
    before = path.read_bytes()
    assert save_review(launched, platform, comparison).returncode != 0
    assert path.read_bytes() == before
    comparison["invocation_id"] = "separate-comparison-model-response"
    second_document = overlay(launched, platform, "during-comparison", document)
    succeed(save_review(launched, platform, comparison))
    succeed(public_run(launched, platform, "phase", "FINAL_VERIFICATION"))
    succeed(public_run(launched, platform, "approved"))
    final = state(launched, platform)
    assert final["status"] == "APPROVED" and final["phase"] == "DONE"
    assert final["approval"]["task_hash"] == second_document
    assert final["review"]["task_hash"] == packet["document_hash"]
    assert len(final["review"]["editorial_validations"]) == 2
    assert Path(issued["packet"]["stored_path"]).read_bytes() == raw


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_blocked_fresh_proof_remains_a_criterion_blocker_in_comparison(launched, platform):
    issued = ready_for_review(launched, platform)
    report = report_for(launched, platform, issued, "fresh", "blocked-independent-fresh")
    report["status"] = "CHANGES_REQUIRED"
    report["criteria"][0]["status"] = "UNSATISFIED"
    report["findings"] = [
        {
            "id": "F-proof",
            "source": "AC-01",
            "severity": "BLOCKING",
            "description": "The independent assessment cannot establish the required proof.",
        }
    ]
    report["counterexample_search"][0].update(
        result="blocked",
        rationale="The scenario needs an additional independent observation before approval.",
    )
    succeed(save_review(launched, platform, report))
    compare = comparison_packet(launched, platform)
    comparison = report_for(launched, platform, compare, "compare", "blocked-separate-comparison")
    comparison["prior_resolutions"] = [
        {
            "id": "F-proof",
            "source": "AC-01",
            "status": "fixed",
            "rationale": "Claimed fixed without a new independent assessment",
            "locations": ["tests/test_rows.py:11"],
            "evidence": report["criteria"][0]["evidence"],
        }
    ]
    path = state_path(launched, platform)
    before = path.read_bytes()
    assert save_review(launched, platform, comparison).returncode != 0
    assert path.read_bytes() == before
    comparison.update(status="CHANGES_REQUIRED", prior_resolutions=[], findings=report["findings"])
    comparison["criteria"][0]["status"] = "UNSATISFIED"
    succeed(save_review(launched, platform, comparison))
    before = path.read_bytes()
    refused = public_run(launched, platform, "phase", "FINAL_VERIFICATION")
    assert refused.returncode != 0 and "blocked" in refused.stdout + refused.stderr
    assert path.read_bytes() == before
    assert state(launched, platform)["status"] == "RUNNING"
