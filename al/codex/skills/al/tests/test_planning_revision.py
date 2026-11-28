"""`planning revise`: a genuine current Architect revision of admitted obligations (#154)."""

import copy
import json
from pathlib import Path

import pytest

import al_proof_json as J
import al_store
from launcher_context import observe_behavior
from test_planning_template import _filled


def ok(cli, *args):
    result = cli(*args)
    assert result[0] == 0, result
    return result


def load(state_file):
    return json.loads(state_file().read_bytes())


def refused(cli, state_file, *args):
    """The command refuses and leaves the saved state bytes unchanged."""
    before = state_file().read_bytes()
    code, out, err = cli(*args)
    assert code != 0, (out, err)
    assert state_file().read_bytes() == before
    return str(code) + out + err


def revise(cli, reason="The admitted scenarios over-specify validation seams.", *extra):
    return ok(cli, "planning", "revise", "--reason", reason, *extra)[1]


def packet(cli):
    return json.loads(ok(cli, "planning", "packet")[1])


def report(cli, tmp_path, value, invocation="architect-revision"):
    path = tmp_path / ("report-" + invocation + ".json")
    path.write_text(json.dumps(value))
    return json.loads(ok(cli, "planning", "report", "--file", str(path), "--invocation-id", invocation)[1])


def readmit(cli, tmp_path, invocation="architect-revision"):
    issued = packet(cli)
    saved = report(cli, tmp_path, _filled(issued), invocation)
    ok(cli, "planning", "admit", "--file", saved["materialized_obligations"]["stored_path"])
    return issued, saved


def walk(cli, *phases):
    for phase in phases:
        ok(cli, "phase", phase)


def test_admitted_run_revises_through_the_ordinary_planning_route(cli, state_file, tmp_path, run_state):
    run_state("finalizing")
    before = load(state_file)
    admitted = copy.deepcopy(before["proof_admission"])
    artifacts = {key: Path(admitted[key]["stored_path"]).read_bytes() for key in ("report", "obligations")}
    # The admitted report cannot be replaced; the refusal names the supported revision route.
    message = refused(cli, state_file, "planning", "report", "--file", admitted["report"]["stored_path"])
    assert "planning revise --reason" in message and "fresh verification-only run" in message

    out = revise(cli, "The admitted scenarios over-specify validation seams.", "--invocation-id", "architect-7")
    st = load(state_file)
    archive = J.read(st["planning_revisions"][-1])
    assert archive["proof_admission"] == admitted
    assert archive["phase"] == "FINALIZATION" and archive["requested_by"] == "architect-7"
    assert archive["reason"] == "The admitted scenarios over-specify validation seams."
    proof = st["proof_admission"]
    assert proof["status"] == "pending" and not {"authority", "report", "obligations"} & proof.keys()
    assert proof["invalidation_generation"] == admitted["invalidation_generation"] + 1
    assert st["phase"] == "PLANNING" and st["agents"] == ["al-architect"]
    assert proof["planning_occurrence"] == len(st["phase_occurrences"]) - 1 > admitted["planning_occurrence"]
    assert archive["planning_occurrence"] == proof["planning_occurrence"]
    # History, evidence, counters and the original artifacts are untouched.
    for key in ("evidence", "gate_manifests", "completed_phases", "review_history", "review_iteration",
                "correction_iteration", "planning_authorship", "rework"):
        assert st.get(key) == before.get(key), key
    assert {key: Path(admitted[key]["stored_path"]).read_bytes() for key in artifacts} == artifacts
    assert "planning obligations revised; revalidate proof" in st["implementation_readiness"]["obligations"]
    assert archive["proof_admission"]["obligations"]["sha256"][:12] in out and "planning packet" in out

    issued = packet(cli)
    behavior = issued["report_template"]["authored_obligations"]
    assert behavior["revision"] == 2 and behavior["supersedes"] == admitted["obligations"]
    assert issued["content"]["binding"]["planning_occurrence"] == proof["planning_occurrence"]
    # The lineage is runtime-fixed: a changed value is refused by path, an omitted one restored.
    for field, value in (("revision", 1), ("supersedes", None)):
        changed = _filled(issued)
        changed["authored_obligations"][field] = value
        path = tmp_path / "changed.json"
        path.write_text(json.dumps(changed))
        message = refused(cli, state_file, "planning", "report", "--file", str(path))
        assert "authored_obligations." + field in message
    omitted = _filled(issued)
    del omitted["authored_obligations"]["revision"], omitted["authored_obligations"]["supersedes"]
    saved = report(cli, tmp_path, omitted)
    stored = json.loads(Path(saved["materialized_obligations"]["stored_path"]).read_bytes())
    assert stored["revision"] == 2 and stored["supersedes"] == admitted["obligations"]
    ok(cli, "planning", "admit", "--file", saved["materialized_obligations"]["stored_path"])
    st = load(state_file)
    assert st["proof_admission"]["status"] == "admitted"
    assert st["proof_admission"]["authority"] != admitted["authority"]
    assert st["planning_history"][-1]["report"] == admitted["report"]
    assert {key: Path(admitted[key]["stored_path"]).read_bytes() for key in artifacts} == artifacts

    # The revised run walks the ordinary edges again and has no structural or workflow gap.
    walk(cli, "IMPLEMENTATION", "FINALIZATION", "VERIFICATION")
    from al_transitions import structural_gaps, workflow_gaps
    current = al_store.load(load(state_file)["session_id"])
    assert structural_gaps(current) == [] and workflow_gaps(current) == []


def test_report_lineage_is_validated_against_the_recorded_revision(cli, state_file, tmp_path, run_state):
    import al_planning as planning
    run_state("implemented")
    admitted = load(state_file)["proof_admission"]
    revise(cli)
    issued = packet(cli)
    report(cli, tmp_path, _filled(issued))
    st = al_store.load(load(state_file)["session_id"])
    current, contents, attempt = planning.packet(st, current_source=False)
    saved = J.read(current["report"])
    planning.validate_report(st, saved, current, contents, attempt, None)
    for revision, supersedes in ((1, None), (2, None), (3, admitted["obligations"]), (1, admitted["obligations"])):
        stale = copy.deepcopy(saved)
        stale["authored_obligations"].update(revision=revision, supersedes=supersedes)
        with pytest.raises(SystemExit, match="continue the recorded planning revision"):
            planning.validate_report(st, stale, current, contents, attempt, None)


def test_observations_under_the_old_authority_no_longer_count(cli, state_file, tmp_path, run_state):
    from al_behavior_observations import behavior_gaps
    run_state("verified")
    observe_behavior(cli)
    sid = load(state_file)["session_id"]
    assert behavior_gaps(al_store.load(sid)) == []
    recorded = load(state_file)["behavior_observations"]
    out = revise(cli)
    assert "no longer counting: " + str(len(recorded)) + " behavior observation" in out
    readmit(cli, tmp_path)
    st = al_store.load(sid)
    assert st["behavior_observations"] == recorded
    gaps = behavior_gaps(st)
    assert gaps and all(gap.startswith("missing current behavior scenario: ") for gap in gaps)
    walk(cli, "IMPLEMENTATION", "VERIFICATION")
    observe_behavior(cli)
    assert behavior_gaps(al_store.load(sid)) == []


def test_recovery_after_revision_leads_to_a_current_planning_packet(cli, state_file, tmp_path, run_state):
    run_state("implemented")
    retained = load(state_file)["planning_authorship"]
    revise(cli)
    code, out, err = cli("recovery", "next")
    assert code == 0, (out, err)
    planning = [d for d in json.loads(out)["descriptors"] if d["subject"]["kind"] == "planning"]
    assert [d["code"] for d in planning] == ["PROOF_ADMISSION_PENDING"], out
    recovery = planning[0]["recovery"]
    assert (recovery["kind"], recovery["command"]) == ("public_operation", "planning.packet"), recovery
    ok(cli, "next", "--do")
    st = load(state_file)
    assert st["planning_authorship"]["packet"] != retained["packet"]
    assert st["planning_history"][-1] == retained
    assert st["proof_admission"]["status"] == "pending"


def test_revising_twice_continues_the_lineage(cli, state_file, tmp_path, run_state):
    run_state("implemented")
    first = load(state_file)["proof_admission"]
    revise(cli, "First Architect revision.")
    readmit(cli, tmp_path, "architect-first")
    second = load(state_file)["proof_admission"]
    revise(cli, "Second Architect revision.")
    issued, saved = readmit(cli, tmp_path, "architect-second")
    st = load(state_file)
    archives = [J.read(ref) for ref in st["planning_revisions"]]
    assert [a["proof_admission"] for a in archives] == [first, second]
    assert [a["reason"] for a in archives] == ["First Architect revision.", "Second Architect revision."]
    assert st["proof_admission"]["invalidation_generation"] == first["invalidation_generation"] + 2
    stored = json.loads(Path(saved["materialized_obligations"]["stored_path"]).read_bytes())
    assert stored["revision"] == 3 and stored["supersedes"] == second["obligations"]
    assert len({first["authority"]["sha256"], second["authority"]["sha256"],
                st["proof_admission"]["authority"]["sha256"]}) == 3


def test_refusals_leave_the_state_unchanged(cli, state_file, tmp_path, run_state):
    run_state("implemented")
    assert "--reason" in refused(cli, state_file, "planning", "revise", "--reason", "   ")
    assert "limited to 2000" in refused(cli, state_file, "planning", "revise", "--reason", "x" * 2001)
    ok(cli, "writer", "start", "--id", "live", "--role", "al-engineer", "--files", "svc/app.py")
    assert "writer live is ACTIVE" in refused(cli, state_file, "planning", "revise", "--reason", "Revise.")
    ok(cli, "writer", "finish", "--id", "live", "--outcome", "success")
    clean = load(state_file)
    for change, expected in (({"approval": {"reason": "forged"}}, "has an approval"),
                             ({"review_iteration": 1}, "review has started and its packets are bound"),
                             ({"phase": "PLAN_CONFLICT"}, "phase PLAN_CONFLICT cannot reopen planning"),
                             ({"gate_recovery": {"forged": True}}, "a gate recovery is active")):
        state_file().write_text(json.dumps({**clean, **change}))
        assert expected in refused(cli, state_file, "planning", "revise", "--reason", "Revise."), change
    if clean["evidence"]["entries"]:
        # An owned process whose cleanup is not confirmed keeps planning closed.
        uncertain = json.loads(json.dumps(clean))
        entry = uncertain["evidence"]["entries"][0]
        entry["cleanup"] = {**(entry.get("cleanup") or {}), "complete": False}
        state_file().write_text(json.dumps(uncertain))
        assert "owned cleanup unresolved: " + entry["id"] in refused(
            cli, state_file, "planning", "revise", "--reason", "Revise.")
    state_file().write_text(json.dumps(clean))
    revise(cli)
    # Pending admission: there is nothing admitted to revise.
    assert "not admitted" in refused(cli, state_file, "planning", "revise", "--reason", "Again.")


def test_revision_is_refused_after_review_started(cli, state_file, run_state):
    run_state("reviewed")
    message = refused(cli, state_file, "planning", "revise", "--reason", "Too late for this run.")
    assert "review has started and its packets are bound" in message and "fresh verification-only run" in message


def test_revision_is_refused_while_rework_recovery_is_pending(cli, state_file, run_state):
    run_state("exhausted")
    message = refused(cli, state_file, "planning", "revise", "--reason", "Revise during recovery.")
    assert "rework recovery is pending or stopped" in message


def test_rebind_draft_from_the_archived_report_carries_the_revision_lineage(cli, state_file, run_state):
    run_state("implemented")
    admitted = load(state_file)["proof_admission"]
    revise(cli)
    packet(cli)
    code, out, err = cli("planning", "rebind", "--from-packet", admitted["packet"]["stored_path"],
                         "--from-report", admitted["report"]["stored_path"])
    assert code == 0, (code, out, err)
    draft = json.loads(out)["report_template"]["authored_obligations"]
    assert draft["revision"] == 2 and draft["supersedes"] == admitted["obligations"]
    assert load(state_file)["proof_admission"]["status"] == "pending"
