"""Real packet/report/authority protocol with a fake external model boundary."""

import copy
import json
from pathlib import Path

import pytest

from al.tests.planning_helpers import prepare, report_for, save_report
from al.tests.runtime_run_helpers import launched, public_run


def state_path(launched, platform="codex"):
    return launched[2] / ("." + platform) / "al-runtime/pin-flow.json"


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_actual_packet_report_materialization_and_separate_admission(launched, platform):
    packet, report = prepare(launched, platform)
    saved = save_report(launched, platform, report)
    assert saved.returncode == 0, (saved.stdout[-3000:], saved.stderr[-3000:])
    refs = json.loads(saved.stdout)
    assert (
        json.loads(state_path(launched, platform).read_bytes())["proof_admission"]["status"]
        == "pending"
    )
    retained_report = json.loads(Path(refs["report"]["stored_path"]).read_bytes())
    materialized = json.loads(Path(refs["materialized_obligations"]["stored_path"]).read_bytes())
    expected = copy.deepcopy(report["authored_obligations"])
    expected["authorship"]["report_ref"] = refs["report"]
    assert materialized == expected
    assert retained_report["authored_obligations"]["authorship"]["report_ref"] is None
    assert retained_report == report
    admitted = public_run(
        launched,
        platform,
        "planning",
        "admit",
        "--file",
        refs["materialized_obligations"]["stored_path"],
    )
    assert admitted.returncode == 0, (admitted.stdout[-3000:], admitted.stderr[-3000:])
    state = json.loads(state_path(launched, platform).read_bytes())
    assert state["proof_admission"]["status"] == "admitted"
    authority = json.loads(Path(state["proof_admission"]["authority"]["stored_path"]).read_bytes())
    assert authority["behavior_obligations"] == refs["materialized_obligations"]
    assert (
        authority["requirement_lineage_hash"]
        == packet["content"]["binding"]["requirement_lineage_hash"]
    )
    assert authority["original_task_package"] == state["task_package"]
    assert state["status"] == "RUNNING" and state["phase"] == "PLANNING"
    before = state_path(launched, platform).read_bytes()
    result = public_run(launched, platform, "phase", "IMPLEMENTATION")
    assert result.returncode != 0
    assert "manifest" in (result.stdout + result.stderr).lower()
    assert state_path(launched, platform).read_bytes() == before
    writer = public_run(
        launched,
        platform,
        "writer",
        "start",
        "--id",
        "no-gates",
        "--role",
        "al-engineer",
        "--files",
        "artifact.md",
    )
    assert writer.returncode != 0
    assert "writing phase" in (writer.stdout + writer.stderr).lower()
    assert state_path(launched, platform).read_bytes() == before


@pytest.mark.parametrize(
    "mutation",
    [
        "foreign-run",
        "wrong-model",
        "forged-role",
        "self-reference",
        "missing-scenario",
        "missing-failure",
        "unknown-consumer",
        "missing-criterion",
        "blocked",
        "unknown-field",
    ],
)
def test_report_refusals_preserve_pending_state(launched, mutation):
    _, report = prepare(launched)
    if mutation == "foreign-run":
        report["binding"]["run_id"] = "00000000"
    elif mutation == "wrong-model":
        report["model"] = "unapproved-model"
    elif mutation == "forged-role":
        report["authored_obligations"]["authorship"]["role"] = "al-builder"
    elif mutation == "self-reference":
        report["authored_obligations"]["authorship"]["report_ref"] = report["authored_obligations"][
            "criteria_registry"
        ][0]["artifact"]
    elif mutation == "missing-scenario":
        report["authored_obligations"]["obligations"][0]["scenarios"].pop()
    elif mutation == "missing-failure":
        report["authored_obligations"]["obligations"][0]["scenarios"][1]["failure_case_ids"] = []
    elif mutation == "unknown-consumer":
        report["authored_obligations"]["obligations"][0]["consumer_inventory"]["uncertainties"] = [
            "Unexamined export consumer"
        ]
    elif mutation == "missing-criterion":
        report["authored_obligations"]["criteria_registry"] = []
    elif mutation == "blocked":
        report["specification"]["status"] = "SPEC_BLOCKED"
    else:
        report["claim_approved"] = True
    before = state_path(launched).read_bytes()
    result = save_report(launched, "codex", report)
    assert result.returncode != 0
    assert state_path(launched).read_bytes() == before
    assert json.loads(before)["proof_admission"]["status"] == "pending"


def test_source_changed_packet_is_not_reused_and_old_report_cannot_bind_new_packet(launched):
    packet, report = prepare(launched)
    (launched[2] / "external-change.txt").write_text("changed during planning\n")
    before = state_path(launched).read_bytes()
    refused = save_report(launched, "codex", report)
    assert refused.returncode != 0
    assert "source observation changed" in refused.stdout + refused.stderr
    assert state_path(launched).read_bytes() == before
    current = public_run(launched, "codex", "planning", "packet")
    assert current.returncode == 0, (current.stdout[-2000:], current.stderr[-2000:])
    fresh = json.loads(current.stdout)
    assert fresh["packet"]["sha256"] != packet["packet"]["sha256"]
    assert fresh["content"]["attempt"]["attempt_id"] != packet["content"]["attempt"]["attempt_id"]
    before = state_path(launched).read_bytes()
    stale = save_report(launched, "codex", report)
    assert stale.returncode != 0
    assert state_path(launched).read_bytes() == before
    valid = save_report(launched, "codex", report_for(fresh))
    assert valid.returncode == 0, (valid.stdout[-2000:], valid.stderr[-2000:])


def test_saved_report_is_immutable_and_substantive_amendment_returns_pending(launched):
    _, report = prepare(launched)
    saved = save_report(launched, "codex", report)
    assert saved.returncode == 0, (saved.stdout[-2000:], saved.stderr[-2000:])
    before = state_path(launched).read_bytes()
    duplicate = save_report(launched, "codex", report)
    assert duplicate.returncode != 0
    assert state_path(launched).read_bytes() == before
    admitted = public_run(launched, "codex", "planning", "admit")
    assert admitted.returncode == 0, (admitted.stdout[-2000:], admitted.stderr[-2000:])
    old = json.loads(state_path(launched).read_bytes())
    amendment = launched[2].parent / "amendment.json"
    amendment.write_text(
        json.dumps(
            {
                "id": "new-observable-outcome",
                "text": "Also preserve existing immutable planning reports.",
                "source": "explicit fixture user",
            }
        )
    )
    result = public_run(launched, "codex", "task", "amend", "--file", str(amendment))
    assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])
    state = json.loads(state_path(launched).read_bytes())
    assert state["proof_admission"]["status"] == "pending"
    assert "authority" not in state["proof_admission"]
    assert state["proof_admission"]["invalidation_generation"] == 1
    assert state["runtime_origin"] == old["runtime_origin"]
    assert state["planning_authorship"] == old["planning_authorship"]


def test_unavailable_unsaved_attempt_requires_recorded_replacement(launched):
    packet, report = prepare(launched)
    old = packet["content"]["attempt"]
    marked = public_run(
        launched,
        "codex",
        "model-unavailable",
        old["model"],
        "--reason",
        "External model provider is unavailable in this protocol fixture.",
    )
    assert marked.returncode == 0, (marked.stdout[-2000:], marked.stderr[-2000:])
    before = state_path(launched).read_bytes()
    stale = save_report(launched, "codex", report)
    assert stale.returncode != 0
    assert "unavailable Architect attempt" in stale.stdout + stale.stderr
    assert state_path(launched).read_bytes() == before
    replaced = public_run(launched, "codex", "planning", "packet", "--replace-unavailable-attempt")
    assert replaced.returncode == 0, (replaced.stdout[-2000:], replaced.stderr[-2000:])
    current = json.loads(replaced.stdout)
    attempt = current["content"]["attempt"]
    assert attempt["attempt_id"] != old["attempt_id"]
    assert attempt["model"] != old["model"]
    assert attempt["fallback_event_reference"] is not None
    saved = save_report(launched, "codex", report_for(current))
    assert saved.returncode == 0, (saved.stdout[-2000:], saved.stderr[-2000:])
    before = state_path(launched).read_bytes()
    refused = public_run(launched, "codex", "planning", "packet", "--replace-unavailable-attempt")
    assert refused.returncode != 0
    assert "saved Architect planning report is immutable" in refused.stdout + refused.stderr
    assert state_path(launched).read_bytes() == before
    admitted = public_run(launched, "codex", "planning", "admit")
    assert admitted.returncode == 0, (admitted.stdout[-2000:], admitted.stderr[-2000:])


def test_supplied_materialization_cannot_change_scenario_content(launched):
    _, report = prepare(launched)
    saved = save_report(launched, "codex", report)
    assert saved.returncode == 0, (saved.stdout[-2000:], saved.stderr[-2000:])
    ref = json.loads(saved.stdout)["materialized_obligations"]
    materialized = json.loads(Path(ref["stored_path"]).read_bytes())
    materialized["obligations"][0]["observable_outcome"] = "A different unreviewed behavior."
    path = launched[2].parent / "altered-obligations.json"
    path.write_text(json.dumps(materialized))
    before = state_path(launched).read_bytes()
    result = public_run(launched, "codex", "planning", "admit", "--file", str(path))
    assert result.returncode != 0
    assert "materialized obligation bytes differ" in result.stdout + result.stderr
    assert state_path(launched).read_bytes() == before


@pytest.mark.parametrize("bad_json", ['{"schema_version":1,"schema_version":1}', '{"value":NaN}'])
def test_duplicate_or_nonfinite_model_json_is_rejected_without_state_write(launched, bad_json):
    prepare(launched)
    path = launched[2].parent / "malformed-model-response.json"
    path.write_text(bad_json)
    before = state_path(launched).read_bytes()
    result = public_run(launched, "codex", "planning", "report", "--file", str(path))
    assert result.returncode != 0
    assert state_path(launched).read_bytes() == before
