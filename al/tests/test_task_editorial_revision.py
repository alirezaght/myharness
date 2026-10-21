"""Presentation changes never replace normative text or manufacture authority."""

import copy
import json
from pathlib import Path

import pytest

from al.tests.planning_helpers import prepare, report_for, save_report
from al.tests.runtime_run_helpers import launched, public_run


def read_state(launched, platform):
    path = launched[2] / ("." + platform) / "al-runtime/pin-flow.json"
    return path, json.loads(path.read_bytes())


def request_for(state, identifier="editorial-one", base=None):
    authority = state["proof_admission"]["authority"]
    value = json.loads(Path(authority["stored_path"]).read_bytes())
    return {
        "schema_version": 1,
        "kind": "task-editorial-revision-v1",
        "id": identifier,
        "base_document_hash": base or value["requirement_lineage_hash"],
        "verification_authority_hash": authority["sha256"],
        "changes": [
            {"field": "task_title", "value": "Readable task title"},
            {"field": "display_summary", "value": ""},
            {"field": "criterion_label", "id": "AC-01", "value": "Admission criterion"},
            {"field": "scenario_label", "id": "admission-success", "value": "Success"},
        ],
        "source": "Explicit developer presentation request",
    }


def editorial(launched, platform, request):
    file = launched[2].parent / "editorial.json"
    file.write_text(json.dumps(request))
    return public_run(launched, platform, "task", "editorial", "--file", str(file))


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_closed_editorial_flow_preserves_authority_and_substantive_invalidation(launched, platform):
    _, report = prepare(launched, platform)
    saved = save_report(launched, platform, report)
    assert saved.returncode == 0, saved.stderr[-2000:]
    admitted = public_run(launched, platform, "planning", "admit")
    assert admitted.returncode == 0, admitted.stderr[-2000:]
    path, original = read_state(launched, platform)
    request = request_for(original)
    invalid = []
    for field in ("expected_outcome", "criteria", "requirement", "wording_only"):
        value = copy.deepcopy(request)
        value["changes"] = [{"field": field, "value": "Changed normative behavior"}]
        invalid.append(value)
    for field in ("base_document_hash", "verification_authority_hash"):
        value = copy.deepcopy(request)
        value[field] = "0" * 64
        invalid.append(value)
    for field in ("criterion_label", "scenario_label"):
        value = copy.deepcopy(request)
        value["changes"] = [{"field": field, "id": "unknown", "value": "Label"}]
        invalid.append(value)
    value = copy.deepcopy(request)
    value["changes"].append(value["changes"][0])
    invalid.append(value)
    value = copy.deepcopy(request)
    value["wording_only"] = True
    invalid.append(value)
    for value in invalid:
        before = path.read_bytes()
        refused = editorial(launched, platform, value)
        assert refused.returncode != 0, value
        assert path.read_bytes() == before
    changed = editorial(launched, platform, request)
    assert changed.returncode == 0, (changed.stdout[-2000:], changed.stderr[-2000:])
    first = json.loads(changed.stdout)
    _, state = read_state(launched, platform)
    overlay = json.loads(Path(first["overlay"]["stored_path"]).read_bytes())
    assert overlay["request"] == request
    assert overlay["authority"] == original["proof_admission"]["authority"]
    assert first["document"]["hash"] != request["base_document_hash"]
    assert first["document"]["requirement_lineage_hash"] == request["base_document_hash"]
    assert state["editorial_history"] == [first["overlay"]]
    for key in original:
        if key != "updated_at":
            assert state[key] == original[key], key
    before = path.read_bytes()
    duplicate = editorial(launched, platform, request)
    assert duplicate.returncode != 0
    assert path.read_bytes() == before
    second_request = request_for(state, "editorial-two", first["document"]["hash"])
    second = editorial(launched, platform, second_request)
    assert second.returncode == 0, second.stderr[-2000:]
    _, twice = read_state(launched, platform)
    assert twice["editorial_history"][:1] == state["editorial_history"]
    assert twice["proof_admission"] == original["proof_admission"]
    assert twice["amendments"] == original["amendments"]
    assert public_run(launched, platform, "show").returncode == 0
    amendment = launched[2].parent / "substantive.json"
    amendment.write_text(
        json.dumps(
            {
                "id": "new-scope",
                "text": "Add a genuinely new behavioral outcome.",
                "source": "developer",
            }
        )
    )
    amended = public_run(launched, platform, "task", "amend", "--file", str(amendment))
    assert amended.returncode == 0, amended.stderr[-2000:]
    _, pending = read_state(launched, platform)
    assert pending["editorial_history"] == twice["editorial_history"]
    assert pending["proof_admission"]["status"] == "pending"
    assert pending["proof_admission"]["invalidation_generation"] == 1
    assert pending["evidence"]["invalidated_at"] > twice["evidence"].get("invalidated_at", 0)
    before = path.read_bytes()
    refused = editorial(
        launched,
        platform,
        request_for(twice, "after-substantive", json.loads(second.stdout)["document"]["hash"]),
    )
    assert refused.returncode != 0
    assert path.read_bytes() == before
    assert public_run(launched, platform, "show").returncode == 0
    ready = public_run(launched, platform, "spec-gate", "SPEC_READY")
    assert ready.returncode == 0, ready.stderr[-2000:]
    packet = public_run(launched, platform, "planning", "packet")
    assert packet.returncode == 0, packet.stderr[-2000:]
    saved = save_report(launched, platform, report_for(json.loads(packet.stdout)))
    assert saved.returncode == 0, saved.stderr[-2000:]
    admitted = public_run(launched, platform, "planning", "admit")
    assert admitted.returncode == 0, admitted.stderr[-2000:]
    _, renewed = read_state(launched, platform)
    assert renewed["editorial_history"] == twice["editorial_history"]
    assert renewed["proof_admission"]["status"] == "admitted"
    assert renewed["proof_admission"]["authority"] != original["proof_admission"]["authority"]


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_pending_run_cannot_publish_editorial_authority(launched, platform):
    prepare(launched, platform)
    path, state = read_state(launched, platform)
    request = {
        "schema_version": 1,
        "kind": "task-editorial-revision-v1",
        "id": "pending",
        "base_document_hash": state["proof_admission"]["requirement_lineage_hash"],
        "verification_authority_hash": "0" * 64,
        "changes": [{"field": "task_title", "value": "Title"}],
        "source": "developer",
    }
    before = path.read_bytes()
    result = editorial(launched, platform, request)
    assert result.returncode != 0
    assert path.read_bytes() == before
