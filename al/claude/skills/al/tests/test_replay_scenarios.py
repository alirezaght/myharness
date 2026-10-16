"""Pin the requested replay scenarios and execute harmless guard searches."""
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPLAY = Path(__file__).resolve().parents[1] / "reference" / "replay"
sys.path.insert(0, str(SCRIPTS))
import al_shell

REQUIRED = {
    "premature-approval", "stale-approval", "red-repro", "writer-overlap",
    "illegal-transition", "baseline-gate-regression", "rollout-omission",
    "criterion-links", "finding-links", "repeated-mr-fixes",
    "proposal-only-assent", "recovery-resume", "harmless-searches",
}


def test_replay_fixtures_cover_each_user_requested_regression():
    cases = [json.loads(path.read_text()) for path in REPLAY.glob("*.json")]
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    assert REQUIRED <= set(ids)
    assert all(case.get("provenance", "").startswith("simulation of") for case in cases)
    assert all(case.get("actions") for case in cases)


def test_harmless_search_fixture_passes_both_guard_policies():
    case = json.loads((REPLAY / "harmless-searches.json").read_text())
    assert "unavailable" in case["baseline_comparison"]
    assert case["commands"]
    for policy, commands in case["commands"].items():
        for command in commands:
            assert al_shell.check(command, policy) == [], (policy, command)
