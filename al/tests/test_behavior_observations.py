"""Real successful assertions are required for complete current scenario mappings."""

import copy
import json
from pathlib import Path

import pytest

from al.tests.behavior_observation_helpers import observation, prepare_behavior, publish
from al.tests.editorial_receipt_helpers import state_path, succeed
from al.tests.runtime_run_helpers import launched, public_run


# Platform-agnostic flow: the shared scripts are byte-identical across platforms (parity test).
@pytest.mark.parametrize("platform", ["codex"])
def test_public_observation_covers_real_journeys_and_refuses_incomplete_proof(launched, platform):
    packet = prepare_behavior(launched, platform)
    assert len(packet["gaps"]) == 2
    source = observation(launched, platform, packet, "read-persisted")
    bad_values = []
    for key in ("verification_authority_hash", "candidate_fingerprint"):
        value = copy.deepcopy(source)
        value["binding"][key] = "0" * 64
        bad_values.append(value)
    value = copy.deepcopy(source)
    value["observed_outcomes"].pop()
    bad_values.append(value)
    value = copy.deepcopy(source)
    value["real_components"] = []
    value["simulated_dependencies"] = [
        {
            "component_id": "parser",
            "mechanism": "pretend parser",
            "limitations": "No actual parser executed",
        }
    ]
    bad_values.append(value)
    value = copy.deepcopy(source)
    value["evidence_ids"] = ["E999"]
    bad_values.append(value)
    value = copy.deepcopy(source)
    value["assertion_locations"][0]["source_sha256"] = "0" * 64
    bad_values.append(value)
    value = copy.deepcopy(source)
    value["assertion_locations"] = value["assertion_locations"][1:]
    bad_values.append(value)
    value = copy.deepcopy(source)
    value["binding"]["input_bindings"] = []
    bad_values.append(value)
    value = copy.deepcopy(source)
    value["implementer_conclusion"] = "Approved"
    bad_values.append(value)
    path = state_path(launched, platform)
    for value in bad_values:
        before = path.read_bytes()
        result = publish(launched, platform, value)
        assert result.returncode != 0, value
        assert path.read_bytes() == before
    first = succeed(publish(launched, platform, source))
    assert len(json.loads(first.stdout)["gaps"]) == 1
    before = path.read_bytes()
    duplicate = publish(launched, platform, source)
    assert duplicate.returncode != 0
    assert path.read_bytes() == before
    second = observation(launched, platform, packet, "reject-malformed")
    complete = succeed(publish(launched, platform, second))
    assert json.loads(complete.stdout)["gaps"] == []
    current = json.loads(succeed(public_run(launched, platform, "behavior", "packet")).stdout)
    assert current["gaps"] == [] and len(current["observations"]) == 2
    assert current["verification_authority"] == packet["verification_authority"]
    assert current["candidate_fingerprint"] == packet["candidate_fingerprint"]
    assert {row["content"]["scenario_id"] for row in current["observations"]} == {
        "read-persisted",
        "reject-malformed",
    }
    state = json.loads(path.read_bytes())
    assert state["status"] == "RUNNING" and state["phase"] == "PLANNING"
    assert state["review"] is None
    original = (launched[2] / "tests/test_rows.py").read_bytes()
    (launched[2] / "tests/test_rows.py").write_bytes(original + b"\nVALUE = 2\n")
    stale = json.loads(succeed(public_run(launched, platform, "behavior", "packet")).stdout)
    assert len(stale["gaps"]) == 2 and stale["observations"] == []
    (launched[2] / "tests/test_rows.py").write_bytes(original)
    assert (
        json.loads(succeed(public_run(launched, platform, "behavior", "packet")).stdout)["gaps"]
        == []
    )
    reference = json.loads(first.stdout)["artifact"]
    artifact = Path(reference["stored_path"])
    artifact.write_bytes(artifact.read_bytes() + b"\n")
    before = path.read_bytes()
    corrupt = public_run(launched, platform, "behavior", "packet")
    assert corrupt.returncode != 0
    assert path.read_bytes() == before
