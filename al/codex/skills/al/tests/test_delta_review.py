"""Incremental (delta) Architect review in a correction loop (#30)."""
import json
from pathlib import Path

import al_review_delta as Delta
from evidence_helpers import complete_review, plan_and_run_broad, start_implementation


def _packet_bytes(state, stage):
    return Path(state["review"]["packets"][stage]["stored_path"]).read_bytes()


def test_scripted_correction_loop_uses_a_small_delta_that_still_shows_the_regression(cli, tmp_path, state_file, repo):
    start_implementation(cli, tmp_path)
    (repo / "svc" / "app.py").write_text("x = 1\n" + "".join(f"keep_{i} = {i}\n" for i in range(40)))
    assert plan_and_run_broad(cli) == 0
    assert cli("phase", "VERIFICATION")[0] == 0
    complete_review(cli, tmp_path, blocking=True)
    first = json.loads(state_file().read_bytes())
    assert first["review"]["mode"] == "full" and first["review"]["snapshot"]
    full_input = sum(len(_packet_bytes(first, stage)) for stage in ("fresh", "compare"))

    assert cli("correction", "--route", "al-engineer")[0] == 0
    assert cli("phase", "VERIFICATION")[0] == 0
    # complete_review rewrites svc/app.py (the correction) before review-start; the seeded
    # regression rides along in a second file the earlier review never saw changed.
    (repo / "svc" / "fixed").write_text("regression = True\n")
    complete_review(cli, tmp_path, blocking=False)
    second = json.loads(state_file().read_bytes())
    review = second["review"]
    assert review["mode"] == "delta", review.get("full_reasons")
    assert second["review_status"] == "APPROVED"
    fresh = json.loads(_packet_bytes(second, "fresh"))
    compare = json.loads(_packet_bytes(second, "compare"))
    delta_input = len(_packet_bytes(second, "fresh")) + len(_packet_bytes(second, "compare"))
    assert full_input >= 3 * delta_input, (full_input, delta_input)

    for packet in (fresh, compare):
        assert packet["review_mode"] == "delta"
        assert set(packet["delta"]["paths"]) == {"svc/app.py", "svc/fixed"}
        assert "+regression = True" in packet["delta"]["diff"]
        assert "files" not in packet["candidate"] and "diff" not in packet["candidate"]
        assert [f["id"] for f in packet["delta"]["prior_findings"]] == ["F-1"]
        assert packet["delta"]["prior_findings"][0]["status"] == "open"
        source = packet["delta"]["prior_findings"][0]["source"]
        assert source in [c["id"] for c in packet["delta"]["affected"]["criteria"]]
    # The fresh stage stays isolated from the plan and implementer summaries.
    assert not {"approved_inputs", "comparison_contents", "fresh", "prior_findings"} & set(fresh)
    assert compare["approved_inputs"]["contents"] == {}

    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    root = Path(__file__).resolve().parents[1] / "reference/schemas"
    registry = Registry().with_resources((p.name, Resource.from_contents(json.loads(p.read_text()))) for p in root.glob("*.json"))
    validator = Draft202012Validator(json.loads((root / "review-packet.json").read_text()), registry=registry)
    errors = [e.message[:200] for packet in (fresh, compare) for e in validator.iter_errors(packet)]
    assert not errors, errors


def test_full_review_requested_or_first_review_records_reasons(cli, tmp_path, state_file):
    start_implementation(cli, tmp_path)
    assert plan_and_run_broad(cli) == 0
    assert cli("phase", "VERIFICATION")[0] == 0
    complete_review(cli, tmp_path, blocking=True)
    assert cli("correction", "--route", "al-engineer")[0] == 0
    assert cli("phase", "VERIFICATION")[0] == 0
    st = json.loads(state_file().read_bytes())
    mode, reasons, _ = Delta.plan(st, st["review"], st["review"]["candidate"], "full")
    assert mode == "full" and reasons == ["full review requested"]
    mode, reasons, _ = Delta.plan(st, None, st["review"]["candidate"], "delta")
    assert mode == "full" and "no completed earlier review" in reasons[0]
    changed = dict(st, classification="COMPLEX")
    mode, reasons, _ = Delta.plan(changed, st["review"], st["review"]["candidate"], "delta")
    assert mode == "full" and any("plan" in r for r in reasons)


CRITERIA = [{"id": "AC-01", "text": "AC-01"}, {"id": "AC-02", "text": "AC-02"}]
INVARIANTS = [{"id": "INV-1", "text": "ordering"}, {"id": "INV-2", "text": "idempotent"}]
REPORT = {"status": "CHANGES_REQUIRED",
          "criteria": [{"id": "AC-01", "status": "UNSATISFIED", "locations": ["svc/app.py:1"]},
                       {"id": "AC-02", "status": "SATISFIED", "locations": ["web/app.js:1"], "rationale": "ok", "evidence": ["E1"]}],
          "invariants": [{"id": "INV-1", "locations": ["svc/order.py:3"]}, {"id": "INV-2", "locations": ["svc/app.py:2"]}]}


def test_delta_touching_an_approved_invariant_requires_a_full_review():
    history = [{"id": "F-1", "source": "AC-01", "severity": "BLOCKING", "status": "fixed"}]
    blocked, _, _ = Delta.classify(CRITERIA, INVARIANTS, REPORT, history, {"svc/app.py", "svc/order.py"}, "R1")
    assert blocked == ["INV-1", "INV-2"]
    report = dict(REPORT, invariants=[{"id": "INV-1", "locations": ["svc/order.py:3"]}])
    blocked, _, _ = Delta.classify(CRITERIA, INVARIANTS, report, history, {"svc/app.py"}, "R1")
    assert blocked == ["INV-2 (location not recorded in R1)"]


def test_delta_lists_the_affected_invariant_under_correction_and_keeps_unaffected_verdicts():
    history = [{"id": "F-2", "source": "INV-2", "severity": "BLOCKING", "status": "open"}]
    blocked, affected, kept = Delta.classify(CRITERIA, INVARIANTS, REPORT, history, {"svc/app.py"}, "R1")
    assert blocked == []
    assert [i["id"] for i in affected["invariants"]] == ["INV-2"]
    assert [c["id"] for c in affected["criteria"]] == ["AC-01"]
    assert [c["id"] for c in kept] == ["AC-02"]


def test_delta_diff_is_keyed_by_content():
    import base64
    snapshot = {"files": {"svc/app.py": {"sha256": "x", "content_b64": base64.b64encode(b"a = 1\n").decode()}}}
    candidate = {"base": "HEAD", "changed_paths": ["svc/app.py"]}
    root = Delta.al_store.REPO_ROOT
    (root / "svc" / "app.py").write_text("a = 1\n")
    assert Delta.compute(snapshot, candidate)["paths"] == []
    (root / "svc" / "app.py").write_text("a = 2\n")
    result = Delta.compute(snapshot, candidate)
    assert result["paths"] == ["svc/app.py"] and "-a = 1\n+a = 2\n" in result["diff"]
