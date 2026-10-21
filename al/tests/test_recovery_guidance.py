"""Guidance is exact current data, not a phase, failure or ownership waiver."""

import copy
import json

import pytest

from al.tests.editorial_receipt_helpers import latest, prepare_readers, state, state_path, succeed
from al.tests.planning_helpers import report_for, save_report
from al.tests.runtime_run_helpers import launched, public_run


def guidance(launched, platform):
    result = succeed(public_run(launched, platform, "recovery", "next"))
    return json.loads(result.stdout)["descriptors"]


def file_for(launched, value):
    path = launched[2].parent / "recovery.json"
    path.write_text(json.dumps(value))
    return str(path)


def validate(launched, platform, path):
    return public_run(launched, platform, "recovery", "validate", "--file", path)


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_guided_planning_operations_cannot_bypass_inputs_or_replay_stale_binding(
    launched, platform
):
    for arguments in (
        ("start", "--spec", "Verify exact current recovery without bypassing planning authority."),
        ("spec-gate", "SPEC_READY"),
        ("classify", "SMALL", "--designer", "no"),
    ):
        succeed(public_run(launched, platform, *arguments))
    (launched[2] / "setup.json").write_text('{"available": true}\n')
    for area, dependency in (("setup", ()), ("consumer", ("--needs", "setup"))):
        succeed(
            public_run(
                launched,
                platform,
                "evidence",
                "plan",
                "--area",
                area,
                "--kind",
                "check",
                "--scope",
                "setup.json",
                *dependency,
                "--",
                "python3",
                "-I",
                "-m",
                "json.tool",
                "setup.json",
            )
        )
    prerequisite = guidance(launched, platform)[0]
    assert prerequisite["code"] == "PREREQUISITE_RUN_REQUIRED"
    assert prerequisite["recovery"]["command"] == "evidence.run"
    prerequisite_file = file_for(launched, prerequisite)
    succeed(
        public_run(
            launched,
            platform,
            "evidence",
            "run",
            "--area",
            "setup",
            "--recovery-file",
            prerequisite_file,
        )
    )
    assert validate(launched, platform, prerequisite_file).returncode != 0
    assert state(launched, platform)["proof_admission"]["status"] == "pending"
    offered = guidance(launched, platform)[0]
    assert offered["code"] == "PROOF_ADMISSION_PENDING"
    assert offered["recovery"]["command"] == "planning.packet"
    path = file_for(launched, offered)
    before = state_path(launched, platform).read_bytes()
    succeed(validate(launched, platform, path))
    assert state_path(launched, platform).read_bytes() == before
    wrong = public_run(launched, platform, "planning", "admit", "--recovery-file", path)
    assert wrong.returncode != 0
    assert state_path(launched, platform).read_bytes() == before
    issued = succeed(public_run(launched, platform, "planning", "packet", "--recovery-file", path))
    assert validate(launched, platform, path).returncode != 0
    missing_report = guidance(launched, platform)[0]
    assert missing_report["recovery"]["kind"] == "supply_input"
    assert missing_report["recovery"]["command"] is None
    path = file_for(launched, missing_report)
    before = state_path(launched, platform).read_bytes()
    assert (
        public_run(launched, platform, "planning", "packet", "--recovery-file", path).returncode
        != 0
    )
    assert state_path(launched, platform).read_bytes() == before
    report = report_for(json.loads(issued.stdout))
    succeed(save_report(launched, platform, report))
    admit = guidance(launched, platform)[0]
    assert admit["recovery"]["command"] == "planning.admit"
    path = file_for(launched, admit)
    succeed(public_run(launched, platform, "planning", "admit", "--recovery-file", path))
    current = state(launched, platform)
    assert current["proof_admission"]["status"] == "admitted"
    assert current["status"] == "RUNNING" and current["phase"] == "PLANNING"
    assert validate(launched, platform, path).returncode != 0
    before = state_path(launched, platform).read_bytes()
    refused = public_run(launched, platform, "phase", "IMPLEMENTATION")
    assert refused.returncode != 0 and "AL_RECOVERY " in refused.stdout + refused.stderr
    assert state_path(launched, platform).read_bytes() == before


@pytest.mark.parametrize("platform", ["codex", "claude"])
def test_gate_guidance_refuses_forgery_source_drift_and_replayed_failed_attempt(launched, platform):
    prepare_readers(launched, platform, duplicate=False)
    offered = next(
        row for row in guidance(launched, platform) if row["subject"]["id"] == "base:reader"
    )
    assert offered["recovery"]["command"] == "gates.run"
    assert offered["recovery"]["arguments"] == {
        "stage": "base",
        "id": "reader",
        "resume": True,
        "retry_failed": False,
        "timeout": 300.0,
    }
    path = file_for(launched, offered)
    before = state_path(launched, platform).read_bytes()
    for mutation in ("foreign-run", "force-command", "scope-override"):
        bad = copy.deepcopy(offered)
        if mutation == "foreign-run":
            bad["binding"]["run_id"] = "00000000"
        elif mutation == "force-command":
            bad["recovery"]["command"] = "force.approve"
        else:
            bad["recovery"]["arguments"]["scope"] = ["unrelated"]
        assert validate(launched, platform, file_for(launched, bad)).returncode != 0
        assert state_path(launched, platform).read_bytes() == before
    path = file_for(launched, offered)
    source = launched[2] / "rows.json"
    raw = source.read_bytes()
    source.write_text('{"changed": true}\n')
    assert validate(launched, platform, path).returncode != 0
    assert state_path(launched, platform).read_bytes() == before
    source.write_bytes(raw)
    succeed(validate(launched, platform, path))
    wrong = public_run(
        launched,
        platform,
        "gates",
        "run",
        "--stage",
        "candidate",
        "--id",
        "reader",
        "--resume",
        "--recovery-file",
        path,
    )
    assert wrong.returncode != 0
    assert state_path(launched, platform).read_bytes() == before
    succeed(
        public_run(
            launched,
            platform,
            "gates",
            "run",
            "--stage",
            "base",
            "--id",
            "reader",
            "--resume",
            "--recovery-file",
            path,
        )
    )
    assert latest(state(launched, platform), "reader")["status"] == "PASS"
    assert validate(launched, platform, path).returncode != 0
    source.write_text('{"bad":\n')
    failed = public_run(
        launched, platform, "gates", "run", "--stage", "candidate", "--id", "reader"
    )
    assert failed.returncode != 0
    first = latest(state(launched, platform), "reader")
    assert first["status"] == "FAIL"
    retry = next(
        row for row in guidance(launched, platform) if row["subject"]["id"] == "candidate:reader"
    )
    assert retry["code"] == "GATE_FAILED"
    assert retry["recovery"]["arguments"]["retry_failed"] is True
    path = file_for(launched, retry)
    result = public_run(
        launched,
        platform,
        "gates",
        "run",
        "--stage",
        "candidate",
        "--id",
        "reader",
        "--resume",
        "--retry-failed",
        "--recovery-file",
        path,
    )
    assert result.returncode != 0
    last = latest(state(launched, platform), "reader")
    assert last["status"] == "FAIL" and last["id"] != first["id"]
    assert first["reservation"] != last["reservation"]
    assert validate(launched, platform, path).returncode != 0
    assert state(launched, platform)["status"] == "RUNNING"
