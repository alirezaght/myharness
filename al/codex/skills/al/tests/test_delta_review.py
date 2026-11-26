"""Incremental (delta) Architect review in a correction loop (#30)."""
import json
from pathlib import Path

import al_review_delta as Delta
from evidence_helpers import complete_review, plan_and_run_broad, start_implementation


def _packet_bytes(state, stage):
    return Path(state["review"]["packets"][stage]["stored_path"]).read_bytes()


def _preserve_packet_profile(directory, state, mode):
    import hashlib

    destination = directory / "packet-byte-profile"
    destination.mkdir(exist_ok=True)
    profile_path = destination / "profile.json"
    profile = json.loads(profile_path.read_text()) if profile_path.exists() else {}
    for stage in ("fresh", "compare"):
        raw = _packet_bytes(state, stage)
        reference = state["review"]["packets"][stage]
        assert hashlib.sha256(raw).hexdigest() == reference["sha256"]
        name = mode + "-" + stage
        (destination / (name + ".json")).write_bytes(raw)
        content = json.loads(raw)
        compact = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        delivered = {"packet": reference, "content": content}
        profile[name] = {
            "review_id": content["review_id"], "sha256": reference["sha256"],
            "stored_bytes": len(raw), "compact_stored_bytes": len(compact),
            "cli_pretty_bytes": len(json.dumps(delivered, indent=2).encode()),
            "cli_compact_bytes": len(json.dumps(delivered, sort_keys=True, separators=(",", ":")).encode()),
            "field_value_bytes": {
                key: {"default": len(json.dumps(value, sort_keys=True).encode()),
                      "compact": len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())}
                for key, value in content.items()},
        }
    profile_path.write_text(json.dumps(profile, sort_keys=True, indent=2) + "\n")


def _check_packet_transport(cli, state, state_file, directory, issued):
    import hashlib

    review = state["review"]
    for stage in ("fresh", "compare"):
        reference = review["packets"][stage]
        raw = _packet_bytes(state, stage)
        content = json.loads(raw)
        output = issued[(review["id"], stage)]
        delivered = json.loads(output)
        assert delivered == {"packet": reference, "content": content}
        assert hashlib.sha256(raw).hexdigest() == reference["sha256"]
        if review["mode"] == "delta":
            assert raw == json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
            assert output == json.dumps(delivered, sort_keys=True, separators=(",", ":")) + "\n"
        else:
            assert raw == json.dumps(content, sort_keys=True).encode()
            assert output == json.dumps(delivered, indent=2) + "\n"
        destination = directory / (review["id"] + "-" + stage + "-packet.json")
        code, cached, err = cli("review-packet", "--stage", stage, "--output", str(destination))
        assert code == 0, (cached, err)
        assert json.loads(cached) == delivered
        assert destination.read_bytes() == raw == _packet_bytes(state, stage)
        assert json.loads(state_file().read_bytes())["review"]["packets"][stage] == reference
        cached_value = json.loads(cached)
        if review["mode"] == "delta":
            assert cached == output
        else:
            assert cached == json.dumps(cached_value, indent=2) + "\n"


def test_scripted_correction_loop_uses_a_small_delta_that_still_shows_the_regression(cli, tmp_path, state_file, repo, run_state):
    run_state("implemented")
    issued = {}
    original_cli = cli

    def capture(*args):
        result = original_cli(*args)
        if args[:1] == ("review-packet",) and result[0] == 0:
            value = json.loads(result[1])["content"]
            issued[(value["review_id"], value["stage"])] = result[1]
        return result

    cli = capture
    (repo / "svc" / "app.py").write_text("x = 1\n" + "".join(f"keep_{i} = {i}\n" for i in range(40)))
    assert plan_and_run_broad(cli) == 0
    assert cli("phase", "VERIFICATION")[0] == 0
    complete_review(cli, tmp_path, blocking=True)
    first = json.loads(state_file().read_bytes())
    assert first["review"]["mode"] == "full" and first["review"]["snapshot"]
    full_input = sum(len(_packet_bytes(first, stage)) for stage in ("fresh", "compare"))
    _preserve_packet_profile(tmp_path, first, "full")
    _check_packet_transport(cli, first, state_file, tmp_path, issued)

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
    _preserve_packet_profile(tmp_path, second, "delta")
    _check_packet_transport(cli, second, state_file, tmp_path, issued)
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
    assert "handoff_records" in compare["delta"] and "handoff_records" not in fresh["delta"]

    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    root = Path(__file__).resolve().parents[1] / "reference/schemas"
    registry = Registry().with_resources((p.name, Resource.from_contents(json.loads(p.read_text()))) for p in root.glob("*.json"))
    validator = Draft202012Validator(json.loads((root / "review-packet.json").read_text()), registry=registry)
    errors = [e.message[:200] for packet in (fresh, compare) for e in validator.iter_errors(packet)]
    assert not errors, errors


def test_legacy_spaced_delta_artifact_renders_compactly_without_rewriting(tmp_path):
    import hashlib
    import al_artifacts as Artifacts
    import al_review as Review

    content = {"review_mode": "delta", "literal": "Keep spaces: a, b: c", "stage": "fresh"}
    raw = json.dumps(content, sort_keys=True).encode()
    path = tmp_path / "legacy-delta-packet.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    digest = hashlib.sha256(raw).hexdigest()
    reference = {"id": digest, "sha256": digest, "kind": "review-packet",
                 "stored_path": str(path), "source": "controlled format fixture", "captured_at": 0}
    before = dict(reference)
    decoded = Artifacts.read_json(reference)
    rendered = Review._render_packet(reference, decoded)
    assert json.loads(rendered) == {"packet": reference, "content": content}
    assert rendered == json.dumps({"packet": reference, "content": content},
                                  sort_keys=True, separators=(",", ":"))
    assert raw != json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    assert Artifacts.read(reference) == path.read_bytes() == raw
    assert reference == before


def test_full_review_requested_or_first_review_records_reasons(cli, tmp_path, state_file, run_state):
    run_state("implemented")
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
