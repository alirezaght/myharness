"""Intent commands are compositions of the fine-grained commands with the same results (#28).

A scripted SMALL and STANDARD run drive the same fixture task twice: once with the fine-grained
command sequence and once with `writer finish --evidence auto`, `verify`, `next --do` and the
automatic REVIEW -> FINAL_VERIFICATION edge of `approved`. Both must end with the same state,
evidence, gate results and approval, while the verification segment needs at least 3x fewer
AL commands."""
import json
import sys
from pathlib import Path

import pytest

from evidence_helpers import _review_report
from hook_regression_helpers import manifest_for
from launcher_context import admit_planning, observe_behavior

PY = sys.executable
PRISTINE = {"svc/app.py": "x = 1\n", "web/app.js": "let a = 1\n"}

ROUTES = {
    "SMALL": {"implementers": "al-engineer",
              "writers": [("engineer", "al-engineer", "svc/app.py", "x = 2\n")],
              "areas": [("svc-unit", "focused", "svc"), ("suite", "broad", "svc")]},
    "STANDARD": {"implementers": "al-engineer,al-builder",
                 "writers": [("engineer", "al-engineer", "svc/app.py", "x = 2\n"),
                             ("builder", "al-builder", "web/app.js", "let a = 2\n")],
                 "areas": [("svc-unit", "focused", "svc"), ("web-unit", "focused", "web"),
                           ("suite", "broad", "svc,web")]},
}


class Recorder:
    """Counts the AL commands of the verification segment (first `writer finish` through review start)."""

    def __init__(self, raw, session):
        self.raw, self.session, self.segment, self.counting = raw, session, [], False

    def __call__(self, *args, ok=True):
        result = self.raw("--session", self.session, *args)
        if self.counting:
            self.segment.append(" ".join(args[:2]))
        if ok:
            assert result[0] == 0, f"{args!r} failed ({result[0]!r}): {result[1]}{result[2]}"
        return result

    def plain(self, *args):
        """Commands that submit agent-authored input are identical in both routes and not counted."""
        return self.raw("--session", self.session, *args)


def _directory(tmp_path, name):
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _reset(repo):
    for rel, text in PRISTINE.items():
        (repo / rel).write_text(text)


def _prepare(call, repo, tmp_path, classification, route):
    call("start", "--spec", "Change the fixture service and web values and verify both before review.")
    call("spec-gate", "SPEC_READY")
    call("classify", classification, "--designer", "no", "--implementers", route["implementers"])
    state = json.loads(call("show")[1])
    paths = tuple(w[2] for w in route["writers"])
    # The discovered architecture gate (imports-root) runs at base and candidate; the others are N/A.
    manifest = manifest_for(state, repo, anticipated_paths=paths)
    assert any(g["applicability"] == "APPLICABLE" for g in manifest["gates"])
    gates = tmp_path / (call.session + "-gates.json")
    gates.write_text(json.dumps(manifest))
    call("gates", "plan", "--file", str(gates))
    admit_planning(call.plain, _directory(tmp_path, call.session + "-planning"))
    for area, tier, scope in route["areas"]:
        call("evidence", "plan", "--area", area, "--tier", tier, "--kind", "check", "--scope", scope,
             "--", PY, "-c", f"print('{area} 3 passed')")
    for writer, role, path, _ in route["writers"]:
        call("writer", "start", "--id", writer, "--role", role, "--files", path)
    for _, _, path, text in route["writers"]:
        (repo / path).write_text(text)


def _review(call, tmp_path, issue_packet):
    """The independent review protocol; only the report files are fixture data."""
    review_id = json.loads(call("show")[1])["review"]["id"]
    issue_packet("fresh")
    fresh = tmp_path / (call.session + "-fresh.json")
    fresh.write_text(json.dumps(_review_report(call.plain, "fresh", review_id + "-fresh", False)))
    call("review-fresh", "--file", str(fresh))
    plan = tmp_path / (call.session + "-plan.md")
    plan.write_text("The reviewed plan covers the accepted criterion and its failure paths.\n")
    call("review-compare-start", "--plan", str(plan))
    issue_packet("compare")
    compare = tmp_path / (call.session + "-compare.json")
    compare.write_text(json.dumps(_review_report(call.plain, "compare", review_id + "-compare", False)))
    call("review-result", "--file", str(compare))


def fine_grained(call, repo, tmp_path, classification, route):
    _prepare(call, repo, tmp_path, classification, route)
    call.counting = True
    for writer, *_ in route["writers"]:
        call("writer", "finish", "--id", writer, "--outcome", "success")
    for area, _, _ in route["areas"]:
        call("evidence", "run", "--area", area)
    call("gates", "run", "--stage", "base")
    call("gates", "run", "--stage", "candidate")
    call.counting = False
    observe_behavior(call.plain, directory=_directory(tmp_path, call.session + "-behavior"))
    call.counting = True
    call("phase", "VERIFICATION")
    call("review-start")
    call.counting = False
    _review(call, tmp_path, lambda stage: call("review-packet", "--stage", stage))
    call("phase", "FINAL_VERIFICATION")
    call("approved")


def intent(call, repo, tmp_path, classification, route):
    _prepare(call, repo, tmp_path, classification, route)
    call.counting = True
    for writer, *_ in route["writers"]:
        call("writer", "finish", "--id", writer, "--outcome", "success", "--evidence", "auto")
    call.counting = False
    observe_behavior(call.plain, directory=_directory(tmp_path, call.session + "-behavior"))
    call.counting = True
    call("verify")
    call.counting = False
    _review(call, tmp_path, lambda stage: call("next", "--do"))
    call("approved")


def outcome(state):
    """Everything the two routes must agree on; ids, times and hashes are route-specific."""
    entries = state["evidence"]["entries"]
    latest = {}
    for entry in entries:
        latest[entry["area"]] = entry["status"]
    gate_runs = {}
    by_id = {e["id"]: e for e in entries}
    for run in state.get("gate_runs", []):
        gate_runs[run["stage"] + ":" + run["gate"]] = by_id[run["evidence"]]["status"]
    approved_areas = sorted({by_id[i]["area"] for i in state["approval"]["evidence"]})
    return {
        "status": state["status"], "phase": state["phase"], "classification": state["classification"],
        "phases": [o["phase"] for o in state["phase_occurrences"]],
        "completed_phases": sorted(set(state["completed_phases"])),
        "writers": {k: (w["state"], w["outcome"]) for k, w in state["writers"].items()},
        "evidence": latest, "planned": sorted(state["evidence"]["plan"]), "gates": gate_runs,
        "approval": {"areas": approved_areas, "with_notes": state["approved_with_notes"],
                     "fresh": state["approval"]["fresh"] is not None,
                     "comparison": state["approval"]["comparison"] is not None},
        "review_iteration": state["review_iteration"], "findings": state["findings"],
    }


@pytest.mark.parametrize("classification", ["SMALL", "STANDARD"])
def test_intent_commands_match_the_fine_grained_sequence_with_3x_fewer_commands(
        cli, repo, tmp_path, classification):
    route = ROUTES[classification]
    results, counts = {}, {}
    for name, script in (("fine", fine_grained), ("intent", intent)):
        _reset(repo)
        session = name + "-" + classification.lower()
        call = Recorder(cli.raw, session)
        script(call, repo, tmp_path, classification, route)
        state = json.loads(call("show")[1])
        assert state["status"] == "APPROVED", state["status"]
        results[name], counts[name] = outcome(state), call.segment
    assert results["intent"] == results["fine"]
    fine, short = len(counts["fine"]), len(counts["intent"])
    assert fine >= 3 * short, (counts["fine"], counts["intent"])


def test_verify_names_the_blocker_and_the_fine_grained_command(cli, repo, tmp_path):
    route = ROUTES["SMALL"]
    call = Recorder(cli.raw, "blocked")
    _prepare(call, repo, tmp_path, "SMALL", route)
    code, out, err = call("verify", ok=False)
    assert code != 0
    assert "verify blocked: writer engineer is ACTIVE" in str(code) + err
    assert "writer finish --id engineer --outcome success --evidence auto" in str(code) + err
    call("writer", "finish", "--id", "engineer", "--outcome", "success")
    failing = tmp_path / "fail.py"
    failing.write_text("raise SystemExit(1)\n")
    call("evidence", "plan", "--area", "suite", "--tier", "broad", "--kind", "check", "--scope", "svc",
         "--", PY, str(failing))
    code, out, err = call("verify", ok=False)
    message = str(code) + err
    assert "verify blocked: evidence did not pass: suite" in message
    assert "evidence run --area" in message
    assert json.loads(call("show")[1])["phase"] == "IMPLEMENTATION"


def test_next_do_refuses_an_input_it_cannot_supply(cli, repo, tmp_path):
    call = Recorder(cli.raw, "next")
    call("start", "--spec", "Refuse to fabricate the specification a planning packet needs.")
    before = json.loads(call("show")[1])
    code, out, err = call("next", "--do", ok=False)
    assert "next --do blocked" in str(code) + err
    assert json.loads(call("show")[1])["evidence"] == before["evidence"]
    code, out, _ = call("next")
    assert "needs input" in out


def test_writer_finish_auto_defers_checks_an_active_sibling_blocks(cli, repo, tmp_path):
    route = ROUTES["STANDARD"]
    call = Recorder(cli.raw, "defer")
    _prepare(call, repo, tmp_path, "STANDARD", route)
    _, out, err = call("writer", "finish", "--id", "engineer", "--outcome", "success", "--evidence", "auto")
    # The checks' commands may share state with the still-active builder: AL refuses them now and
    # the intent command defers them instead of failing the finished writer.
    assert "evidence DEFERRED svc-unit, suite" in out
    assert "check overlaps unresolved writer builder" in err
    assert "next: finish the active writer(s) builder" in out
    state = json.loads(call("show")[1])
    assert state["writers"]["engineer"]["state"] == "FINISHED"
    assert state["evidence"]["entries"] == []
    _, out, _ = call("writer", "finish", "--id", "builder", "--outcome", "success", "--evidence", "auto")
    assert "next: `verify`" in out
    state = json.loads(call("show")[1])
    # Only the areas the builder's paths reach run here; `verify` runs the deferred svc-unit.
    assert {e["area"] for e in state["evidence"]["entries"]} == {"web-unit", "suite"}
