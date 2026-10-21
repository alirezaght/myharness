"""Mandatory pending proof is an independently anchored new-run default."""

import json
from pathlib import Path
import sys

import pytest

from al.tests.runtime_run_helpers import invoke, launched, public_run, run_record


def begin(launched, platform):
    result = public_run(
        launched, platform, "start", "--spec", "Establish observable proof before implementation."
    )
    assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])
    return launched[2] / ("." + platform) / "al-runtime/pin-flow.json"


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_every_public_new_start_has_independent_pending_initialization(launched, platform):
    state_path = begin(launched, platform)
    state = json.loads(state_path.read_bytes())
    admission = state["proof_admission"]
    path, record = run_record(launched)
    reference = record["initialization"]
    assert admission["status"] == "pending"
    assert admission["initialization"] == state["runtime_origin"]["initialization"] == reference
    assert Path(reference["stored_path"]).is_relative_to(path.parent.parent / "initializations")
    initial = json.loads(Path(reference["stored_path"]).read_bytes())
    assert initial["original_task_package"] == state["task_package"]
    assert initial["runtime_pin"] == record["pin"]
    assert initial["run_id"] == state["run_id"]
    assert initial["planning_occurrence"] == admission["planning_occurrence"] == 0
    assert initial["verifier_policy"] == admission["verifier_policy"]
    assert admission["invalidation_generation"] == 0


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_pending_run_can_plan_but_cannot_implement_or_admit_writer(launched, platform):
    state_path = begin(launched, platform)
    for args in [
        ("spec-gate", "SPEC_READY"),
        ("classify", "SMALL", "--designer", "no", "--reason", "Bounded observable repair."),
    ]:
        result = public_run(launched, platform, *args)
        assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])
    before = state_path.read_bytes()
    for args in [
        ("phase", "IMPLEMENTATION"),
        ("writer", "start", "--id", "attempt", "--role", "al-engineer", "--files", "app.py"),
        ("review-start",),
        ("approved",),
    ]:
        result = public_run(launched, platform, *args)
        assert result.returncode != 0
        assert "proof admission pending" in result.stdout + result.stderr
        assert state_path.read_bytes() == before


@pytest.mark.parametrize(
    "mutation", ["missing", "downgraded", "forged-admitted", "lineage", "initialization-reference"]
)
def test_state_cannot_disable_mandatory_proof(launched, mutation):
    state_path = begin(launched, "codex")
    state = json.loads(state_path.read_bytes())
    if mutation == "missing":
        del state["proof_admission"]
    elif mutation == "downgraded":
        state["proof_admission"]["schema_version"] = 0
    elif mutation == "forged-admitted":
        state["proof_admission"]["status"] = "admitted"
    elif mutation == "lineage":
        state["proof_admission"]["requirement_lineage_hash"] = "0" * 64
    else:
        state["proof_admission"]["initialization"]["stored_path"] = str(
            state_path.parent / "forged"
        )
    state_path.write_text(json.dumps(state))
    before = state_path.read_bytes()
    result = public_run(launched, "codex", "show", "--json")
    assert result.returncode != 0
    assert state_path.read_bytes() == before


def test_unpinned_candidate_start_cannot_choose_legacy(launched):
    source, _, repo, _, environment = launched
    result = invoke(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            source / "codex/skills/al/scripts/al_state.py",
            "--session",
            "direct",
            "start",
            "--spec",
            "Do not manufacture legacy origin.",
        ],
        repo,
        environment,
    )
    assert result.returncode != 0
    assert "protected managed alctl run launcher" in result.stdout + result.stderr
    assert not (repo / ".codex/al-runtime/direct.json").exists()


def test_substantive_amendment_retains_origin_and_returns_current_pending(launched):
    state_path = begin(launched, "codex")
    old = json.loads(state_path.read_bytes())
    request = launched[2] / "amendment.json"
    request.write_text(
        json.dumps(
            {
                "id": "additional-requirement",
                "text": "Also preserve every existing observable behavior.",
                "source": "explicit fixture user amendment",
            }
        )
    )
    result = public_run(launched, "codex", "task", "amend", "--file", str(request))
    assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])
    shown = public_run(launched, "codex", "show", "--json")
    assert shown.returncode == 0, (shown.stdout[-2000:], shown.stderr[-2000:])
    state = json.loads(shown.stdout)
    assert state["runtime_origin"] == old["runtime_origin"]
    assert state["proof_admission"]["initialization"] == old["proof_admission"]["initialization"]
    assert state["proof_admission"]["invalidation_generation"] == 1
    assert state["proof_admission"]["status"] == "pending"
    assert (
        state["proof_admission"]["requirement_lineage_hash"]
        != old["proof_admission"]["requirement_lineage_hash"]
    )
