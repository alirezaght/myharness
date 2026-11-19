"""Recovery guidance follows actual prerequisites without granting proof or credit."""
import json

from evidence_helpers import plan_na_gates
from launcher_context import _reports
from test_rework import load, record_red
from test_rework_recovery import decision, exhaust


def succeeded(result):
    assert result[0] == 0, result
    return result[1]


def pending_recovery(cli, tmp_path):
    raw = cli.raw
    for args in (
        ("start", "--spec", "Preserve scoped implementation while repairing planning provenance."),
        ("spec-gate", "SPEC_READY"),
        ("classify", "STANDARD", "--designer", "no", "--implementers", "al-engineer"),
    ):
        succeeded(raw(*args))
    plan_na_gates(raw, tmp_path)
    succeeded(raw("phase", "IMPLEMENTATION"))
    succeeded(raw("phase", "FINALIZATION"))
    record_red(raw)
    exhaust(raw)
    return raw


def offered(raw):
    return json.loads(succeeded(raw("recovery", "next")))["descriptors"]


def test_pending_proof_precedes_rr_and_actual_planning_progression_preserves_guards(cli, state_file, tmp_path):
    raw = pending_recovery(cli, tmp_path)
    initial = load(state_file)
    assert initial["proof_admission"]["status"] == "pending"
    assert initial["rework_recovery"]["pending"]
    assert initial["phase"] == "REWORK_REASSESSMENT"
    before = state_file().read_bytes()
    first = offered(raw)[0]
    assert first["code"] == "PROOF_ADMISSION_PENDING"
    requirement = first["recovery"]["required_input"]
    assert requirement["kind"] == "planning-phase-context"
    assert "REWORK_REASSESSMENT" in requirement["description"]
    assert "SPEC_READY" not in requirement["description"]
    assert state_file().read_bytes() == before

    # A valid Architect recovery decision still cannot grant credits without proof.
    path, _ = decision(raw, tmp_path)
    retained_recovery = load(state_file)["rework_recovery"]
    before = state_file().read_bytes()
    refused = raw("rework-recovery", "decide", "--file", str(path))
    assert refused[0] != 0
    assert "proof admission pending" in str(refused)
    assert '"code":"PROOF_ADMISSION_PENDING"' in str(refused)
    assert '"code":"REWORK_REASSESSMENT_REQUIRED"' not in str(refused)
    assert state_file().read_bytes() == before

    # Use the existing guarded continuation; guidance must not invent this edge.
    succeeded(raw("phase", "FINALIZATION"))
    succeeded(raw("phase", "VERIFICATION"))
    assert load(state_file)["rework"] == initial["rework"]
    before = state_file().read_bytes()
    refused = raw("review-start")
    assert refused[0] != 0 and "proof admission pending" in str(refused)
    assert '"code":"PROOF_ADMISSION_PENDING"' in str(refused)
    assert state_file().read_bytes() == before
    packet_action = offered(raw)[0]
    assert packet_action["recovery"]["command"] == "planning.packet"
    descriptor = tmp_path / "before-planning-recovery.json"
    descriptor.write_text(json.dumps(packet_action))
    packet = json.loads(succeeded(raw("planning", "packet")))
    report_action = offered(raw)[0]
    assert report_action["recovery"]["required_input"]["kind"] == "architect-planning-report-v1"
    report = tmp_path / "actual-model-response.json"
    report.write_text(json.dumps(_reports().report_for(packet)))
    result = json.loads(succeeded(raw("planning", "report", "--file", str(report))))
    assert offered(raw)[0]["recovery"]["command"] == "planning.admit"
    succeeded(raw("planning", "admit", "--file", result["materialized_obligations"]["stored_path"]))
    current = load(state_file)
    assert current["proof_admission"]["status"] == "admitted"
    assert current["rework"] == initial["rework"]
    assert current["rework_recovery"] == retained_recovery
    assert current["review_status"] is None
    assert offered(raw)[0]["code"] == "REWORK_REASSESSMENT_REQUIRED"
    before = state_file().read_bytes()
    assert raw("recovery", "validate", "--file", str(descriptor))[0] != 0
    assert raw("approved", "--reason", "No independent review occurred")[0] != 0
    assert state_file().read_bytes() == before


def test_live_writer_is_named_before_pending_planning_or_recovery(cli, state_file, tmp_path):
    raw = pending_recovery(cli, tmp_path)
    succeeded(raw("writer", "start", "--id", "scoped", "--role", "al-engineer", "--files", "svc/app.py"))
    before = state_file().read_bytes()
    result = offered(raw)
    assert result[0]["code"] == "WRITER_UNRESOLVED"
    assert result[0]["subject"] == {"kind": "writer", "id": "scoped"}
    assert state_file().read_bytes() == before
    succeeded(raw("writer", "finish", "--id", "scoped", "--outcome", "success"))
    assert offered(raw)[0]["code"] == "PROOF_ADMISSION_PENDING"


def test_guidance_names_missing_specification_only_when_it_is_actually_missing(cli, state_file):
    raw = cli.raw
    succeeded(raw("start", "--spec", "Identify the actual prerequisite before suggesting recovery."))
    first = offered(raw)[0]
    assert first["code"] == "PROOF_ADMISSION_PENDING"
    assert first["recovery"]["required_input"]["kind"] == "planning-specification"
    succeeded(raw("spec-gate", "SPEC_READY"))
    succeeded(raw("classify", "SMALL", "--designer", "no"))
    before = state_file().read_bytes()
    assert offered(raw)[0]["recovery"]["command"] == "planning.packet"
    assert state_file().read_bytes() == before
