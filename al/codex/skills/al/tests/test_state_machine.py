#!/usr/bin/env python3
"""State-machine walk (T4) plus the mechanical guard exits (I3): 3, 4, 5, 6, 7,
8, 9 must keep their meaning."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import al_phases  # noqa: E402
from evidence_helpers import complete_review, plan_and_run_broad, plan_na_gates, start_implementation


def load_state(tmp_path):
    sid = (tmp_path / "current").read_text().strip()
    return json.loads((tmp_path / f"{sid}.json").read_text())


def test_full_walk_to_the_correction_limit(cli, tmp_path, state_file):
    code, *_ = cli("start", "--spec", "Build the requested implementation and verify its observable behavior, including invalid input and failure cases.")
    assert code == 0

    code, *_ = cli("spec-gate", "SPEC_READY")
    assert code == 0

    code, out, err = cli(
        "classify", "COMPLEX", "--designer", "yes",
        "--implementers", "al-engineer,al-builder",
    )
    assert code == 0
    st = load_state(tmp_path)
    expected_path = al_phases.PATH_TEMPLATE["COMPLEX"].format(impl="AL-Engineer + AL-Builder")
    assert st["path"] == expected_path
    assert st["skipped_phases"] == []

    plan_na_gates(cli, tmp_path)

    code, *_ = cli("phase", "PSEUDOCODE")
    assert code == 0

    code, out, err = cli(
        "phase", "IMPLEMENTATION", "--agents", "al-engineer,al-builder",
        "--mode", "sequential", "--reason", "no shared/unstable interface",
    )
    assert code == 0
    assert "Parallel execution skipped" in out

    code, *_ = cli("phase", "FINALIZATION")
    assert code == 0
    code, *_ = cli("phase", "VERIFICATION")
    assert code == 0
    assert plan_and_run_broad(cli) == 0

    complete_review(cli, tmp_path)

    for i in range(1, 7):
        code, *_ = cli("correction", "--route", "al-engineer")
        assert code == 0, f"correction iteration {i} should still be within budget"
        assert cli("phase", "FINALIZATION")[0] == 0
        assert cli("phase", "VERIFICATION")[0] == 0
        complete_review(cli, tmp_path)

    before = state_file().read_bytes()
    code, out, err = cli("correction", "--route", "al-engineer")
    assert isinstance(code, str) and "correction budget exhausted" in code
    assert state_file().read_bytes() == before
    st = load_state(tmp_path)
    assert st["status"] == "RUNNING" and st["phase"] == "REVIEW"


def test_spec_gate_blocked_exits_8(cli):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    code, out, err = cli("spec-gate", "SPEC_BLOCKED", "--reason", "two ACs contradict")
    assert code == 8


def test_correction_without_blocking_findings_is_refused(cli, tmp_path):
    start_implementation(cli, tmp_path)
    cli("phase", "FINALIZATION")
    cli("phase", "VERIFICATION")
    assert plan_and_run_broad(cli) == 0
    complete_review(cli, tmp_path, blocking=False)
    code, out, err = cli("correction", "--route", "al-engineer")
    assert isinstance(code, str) and "completed blocking review" in code


def test_retry_second_attempt_exits_7(cli):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    code, *_ = cli("agent-failed", "al-engineer", "--reason", "transient test runner failure",
                   "--retry-safe", "yes")
    assert code == 0
    code, *_ = cli("retry", "al-engineer", "--reason", "retry transient test runner failure")
    assert code == 0
    code, out, err = cli("retry", "al-engineer", "--reason", "still flaky")
    assert code == 7


def test_model_unavailable_luna_blocks_builder_exits_4(cli):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    code, out, err = cli("model-unavailable", "luna", "--reason", "not entitled")
    assert code == 4


def test_ownership_overlapping_paths_exits_5(cli):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    code, out, err = cli("ownership", "--engineer", "a/b", "--builder", "a")
    assert code == 5


def test_ownership_shared_state_command_exits_5(cli):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    code, out, err = cli(
        "ownership", "--engineer", "src/core", "--builder", "src/support",
        "--builder-commands", "pnpm install",
    )
    assert code == 5


def test_snapshot_missing_required_label_exits_6(cli):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    code, out, err = cli("snapshot", "pre-review", "--require", "missing")
    assert code == 6
