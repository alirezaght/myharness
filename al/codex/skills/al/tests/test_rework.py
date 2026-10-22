#!/usr/bin/env python3
"""Pre-review rework: the fast path for confirmed local defects, the Architect
route for real decisions, the per-batch budget, and old state files."""
import json
import sys

import pytest
from evidence_helpers import complete_review, plan_and_run_broad, start_implementation

FAST = ["--cause-demonstrated", "yes", "--local", "yes", "--contract-change", "no",
        "--scope-ambiguous", "no", "--safety-decision", "no", "--spec-conflict", "no",
        ]
# Red until svc/fixed exists: the loop that demonstrates the defect, then proves the fix.
REPRO = [sys.executable, "-c", "import os, sys; ok = os.path.exists('svc/fixed'); "
         "print('1 passed' if ok else '1 failed'); sys.exit(0 if ok else 1)"]


def record_red(cli):
    code, *_ = cli("evidence", "run", "--area", "repro", "--tier", "focused", "--scope", "svc",
                   "--", *REPRO)
    assert code == 1  # the run itself failed, as a repro must


def fast(cli):
    state = json.loads(cli("show")[1])
    red = next(e for e in reversed(state["evidence"]["entries"]) if e["area"] == "repro" and e["status"] == "FAIL")
    return [*FAST, "--repro", red["id"]]


def flags(cli, **overrides):
    out = fast(cli)
    for key, value in overrides.items():
        out[out.index("--" + key.replace("_", "-")) + 1] = value
    return out


def load(state_file):
    return json.loads(state_file().read_text())


def started(cli, tmp_path):
    start_implementation(cli, tmp_path)
    cli("phase", "FINALIZATION")
    record_red(cli)


def test_confirmed_local_core_defect_goes_straight_to_the_engineer(cli, state_file, tmp_path):
    started(cli, tmp_path)
    code, out, _ = cli("rework", "--source", "finalization", "--findings", "2", *fast(cli))
    assert code == 0
    assert "→ AL-Engineer · fast path" in out
    assert "AL-Architect" not in out
    st = load(state_file)
    assert st["phase"] == "REWORK" and st["agents"] == ["al-engineer"]
    assert "PSEUDOCODE" not in st["phase_timings"]
    assert st["phase_timings"]["PLANNING"]["ended"] is not None  # planning not re-opened


@pytest.mark.parametrize("trigger", [
    {"cause_demonstrated": "no"}, {"local": "no"}, {"contract_change": "yes"},
    {"scope_ambiguous": "yes"}, {"safety_decision": "yes"}, {"spec_conflict": "yes"},
])
def test_a_real_decision_still_reaches_the_architect(cli, state_file, trigger, tmp_path):
    started(cli, tmp_path)
    code, out, _ = cli("rework", "--source", "core-flaw", *flags(cli, **trigger))
    assert code == 12
    assert "AL-Architect first" in out
    assert load(state_file)["agents"] == ["al-architect", "al-engineer"]


def test_rework_is_bounded_per_batch_and_counted(cli, state_file, tmp_path):
    started(cli, tmp_path)
    assert cli("rework", "--source", "finalization", *fast(cli))[0] == 0
    cli("phase", "FINALIZATION")
    assert cli("rework", "--source", "finalization", *fast(cli))[0] == 0
    cli("phase", "FINALIZATION")
    before = state_file().read_bytes()
    code, _, err = cli("rework", "--source", "finalization", *fast(cli))
    assert code == 10 and "rework budget exhausted" in err
    assert state_file().read_bytes() == before
    st = load(state_file)
    assert st["status"] == "RUNNING" and st["phase"] == "FINALIZATION"
    assert st["rework"]["total"] == 2
    assert st["correction_iteration"] == 0  # review corrections stay separate


def test_a_new_correction_batch_opens_a_new_budget_and_says_so(cli, state_file, tmp_path, repo):
    started(cli, tmp_path)
    assert cli("rework", "--source", "finalization", *fast(cli))[0] == 0
    assert cli("phase", "FINALIZATION")[0] == 0
    assert cli("rework", "--source", "finalization", *fast(cli))[0] == 0
    assert cli("phase", "FINALIZATION")[0] == 0
    (repo / "svc" / "fixed").write_text("fixed\n")
    assert cli("evidence", "run", "--area", "repro", "--tier", "focused", "--scope", "svc", "--", *REPRO)[0] == 0
    assert cli("phase", "VERIFICATION")[0] == 0
    assert plan_and_run_broad(cli) == 0
    complete_review(cli, tmp_path)
    cli("correction", "--route", "al-engineer")
    cli("phase", "FINALIZATION")
    (repo / "svc" / "fixed").unlink()
    code, *_ = cli("evidence", "run", "--area", "repro", "--tier", "focused", "--scope", "svc", "--", *REPRO)
    assert code == 1
    fresh_repro = load(state_file)["evidence"]["entries"][-1]["id"]
    code, out, _ = cli("rework", "--source", "finalization", *flags(cli, repro=fresh_repro))
    assert code == 0
    assert "new batch (correction 1) — previous batch used 2/2" in out
    assert load(state_file)["rework"]["total"] == 3


def test_status_line_shows_the_rework_counter(cli, tmp_path):
    started(cli, tmp_path)
    cli("rework", "--source", "finalization", *fast(cli))
    _, out, _ = cli("show", "--line")
    assert "Rework 1/2" in out


def test_zero_findings_is_refused(cli, tmp_path):
    started(cli, tmp_path)
    code, *_ = cli("rework", "--source", "finalization", "--findings", "0", *fast(cli))
    assert isinstance(code, str) and "--findings" in code


OLD_STATE = {
    "workflow": "AL", "status": "RUNNING", "task": "old task", "task_title": "old task",
    "started_at": 1.0, "phase": "FINALIZATION", "agent": "AL-Builder", "agents": ["al-builder"],
    "model": "sonnet", "effort": "high", "review_iteration": 0, "correction_iteration": 0,
    "max_correction_iterations": 6, "review_status": None,
    "findings": {"blocking": 0, "nonblocking": 0, "core": 0, "tests": 0, "both": 0},
    "spec_status": "SPEC_READY", "classification": "STANDARD", "path": "Verify the candidate task preserves repository state while recording evidence for its implementation.",
    "skipped_phases": ["PSEUDOCODE"], "optional_phases": ["FINALIZATION"],
    "completed_phases": ["PLANNING", "IMPLEMENTATION"], "pending_phases": [],
    "phase_timings": {"FINALIZATION": {"started": 1.0, "ended": None}},
    "model_tier": {}, "unavailable_models": [], "preexisting_changes": [],
}


def test_a_state_file_from_an_older_al_fails_closed_without_losing_history(cli, state_file, repo):
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**OLD_STATE, "session_id": path.stem}))
    code, *_ = cli("show", "--line")
    assert isinstance(code, str) and legacy_refusal(code)
    code, *_ = cli("evidence", "run", "--area", "repro", "--tier", "focused", "--scope", "svc",
                   "--", *REPRO)
    assert isinstance(code, str) and legacy_refusal(code)
    code, *_ = cli("rework", "--source", "finalization", *without_repro())
    assert isinstance(code, str) and legacy_refusal(code)
    cli("start", "--spec", "Start a new supported run while retaining the legacy state file.")
    assert path.exists() and state_file().name != path.name


def legacy_refusal(code):
    # The protected launcher refuses unbound legacy runs before al_state's schema check.
    return "unknown/legacy state schema" in code or "use the original issuer for legacy runs" in code


def without_repro():
    return list(FAST)


def test_a_demonstrated_cause_without_a_recorded_repro_is_refused(cli, state_file, tmp_path):
    started(cli, tmp_path)
    code, _, err = cli("rework", "--source", "core-flaw", *without_repro())
    assert code == 14 and "--repro" in err
    assert load(state_file)["rework"]["used"] == 0  # no budget spent on a refused claim


@pytest.mark.parametrize("repro, reason", [("missing", "no such ledger entry"),
                                           ("green", "not FAIL")])
def test_a_repro_must_be_a_recorded_red_run(cli, repo, repro, reason, tmp_path):
    started(cli, tmp_path)
    (repo / "svc" / "fixed").write_text("")
    cli("evidence", "run", "--area", "repro", "--tier", "focused", "--scope", "svc", "--", *REPRO)
    state = json.loads(cli("show")[1])
    selected = state["evidence"]["entries"][-1]["id"] if repro == "green" else "E999999"
    code, _, err = cli("rework", "--source", "core-flaw", *without_repro(), "--repro", selected)
    assert code == 14 and reason in err


def test_a_repro_with_an_edited_log_is_refused(cli, state_file, tmp_path):
    started(cli, tmp_path)
    log = next(e["log"] for e in reversed(load(state_file)["evidence"]["entries"]) if e["area"] == "repro")
    with open(log, "a") as f:
        f.write("tampered\n")
    code, _, err = cli("rework", "--source", "core-flaw", *fast(cli))
    assert code == 14 and "LOG_ALTERED" in err


def test_an_undemonstrated_cause_needs_no_repro_and_goes_to_the_architect(cli, tmp_path):
    started(cli, tmp_path)
    flags_ = without_repro()
    flags_[flags_.index("--cause-demonstrated") + 1] = "no"
    assert cli("rework", "--source", "core-flaw", *flags_)[0] == 12


def test_review_waits_until_the_repro_command_goes_green(cli, repo, tmp_path):
    started(cli, tmp_path)
    assert cli("rework", "--source", "finalization", *fast(cli))[0] == 0
    assert cli("phase", "VERIFICATION")[0] == 0
    assert plan_and_run_broad(cli) == 0
    code, out, _ = cli("evidence", "require")
    repro = fast(cli)[-1]
    assert code == 13 and "repro " + repro in out and "STILL_RED" in out
    refusal, _, _ = cli("review-start")
    assert isinstance(refusal, str) and "repro " + repro in refusal and "STILL_RED" in refusal
    (repo / "svc" / "fixed").write_text("")
    assert cli("evidence", "run", "--area", "repro", "--tier", "focused", "--scope", "svc",
               "--", *REPRO)[0] == 0
    assert cli("evidence", "run", "--area", "suite")[0] == 0
    assert cli("evidence", "require")[0] == 0
    assert cli("gates", "run", "--stage", "candidate")[0] == 0
    assert cli("review-start")[0] == 0


def test_a_green_repro_whose_prerequisite_went_stale_still_blocks_review(cli, repo, tmp_path):
    started(cli, tmp_path)
    cli("evidence", "plan", "--area", "unit", "--tier", "focused", "--scope", "web",
        "--", sys.executable, "-c", "print('1 passed')")
    assert cli("evidence", "run", "--area", "unit")[0] == 0
    gated = ["evidence", "run", "--area", "repro", "--tier", "focused", "--scope", "svc",
             "--needs", "unit", "--", *REPRO]
    assert cli(*gated)[0] == 1
    red = json.loads(cli("show")[1])["evidence"]["entries"][-1]["id"]
    assert cli("rework", "--source", "finalization", *without_repro(), "--repro", red)[0] == 0
    (repo / "svc" / "fixed").write_text("")
    assert cli(*gated)[0] == 0
    assert plan_and_run_broad(cli) == 0
    assert cli("evidence", "require")[0] == 0
    (repo / "web" / "app.js").write_text("let a = 2\n")  # the needed focused check goes stale
    code, out, _ = cli("evidence", "require")
    assert code == 13 and "PREREQ_INVALID" in out
