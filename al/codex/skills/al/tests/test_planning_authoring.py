"""Planning authorship is retained without admitting current-source authority."""

import copy
import json
from pathlib import Path

import pytest

import al_planning as planning
import al_store
import launcher_context
from evidence_helpers import plan_na_gates
from launcher_context import _reports


def ok(cli, *args):
    result = cli(*args)
    assert result[0] == 0, result
    return result


def pending(cli, repo, tmp_path, monkeypatch):
    ok(cli, "start", "--spec", "Retain source-bound planning authorship while scoped implementation runs.")
    sid = json.loads(ok(cli, "show")[1])["session_id"]
    monkeypatch.setattr(launcher_context, "admit_planning", lambda *args, **kwargs: None)
    ok(cli, "spec-gate", "SPEC_READY")
    ok(cli, "classify", "STANDARD", "--designer", "no", "--implementers", "al-engineer")
    plan_na_gates(cli, tmp_path, ("svc/app.py",))
    return sid


def start_writer(cli, tmp_path):
    ok(cli, "phase", "IMPLEMENTATION")
    ok(cli, "writer", "start", "--id", "write-A", "--role", "al-engineer", "--files", "svc/app.py")


def retain(cli, tmp_path, packet):
    report = _reports().report_for(packet)
    path = tmp_path / "authored-planning-report.json"
    path.write_text(json.dumps(report))
    result = ok(cli, "planning", "report", "--file", str(path))
    return report, path, json.loads(result[1])


def test_packet_and_report_authoring_allow_an_active_ordinary_writer(cli, repo, tmp_path, monkeypatch):
    sid = pending(cli, repo, tmp_path, monkeypatch)
    start_writer(cli, tmp_path)
    before = al_store.load(sid)
    packet = json.loads(ok(cli, "planning", "packet")[1])
    assert packet["authoring"]["authority"] == "none"
    assert packet["authoring"]["current_admission"] == "pending"
    assert packet["authoring"]["source_currency"] == "issued-observation-only"
    assert packet["authoring"]["active_writer_ids"] == ["write-A"]
    assert packet["report_template"]["authored_obligations"]["implementation_context"] == {
        "status": "partially_built",
        "observation_ref": packet["content"]["source_observation"],
    }
    report, _, saved = retain(cli, tmp_path, packet)
    after = al_store.load(sid)
    assert after["writers"] == before["writers"]
    assert after.get("integration") == before.get("integration")
    assert after["proof_admission"]["status"] == "pending"
    assert "authority" not in after["proof_admission"]
    assert json.loads(Path(saved["report"]["stored_path"]).read_bytes()) == report
    assert saved["authoring"]["current_admission"] == "pending"
    assert cli("planning", "admit")[0] != 0
    ok(cli, "writer", "finish", "--id", "write-A", "--outcome", "success")
    ok(cli, "planning", "admit")
    assert al_store.load(sid)["proof_admission"]["status"] == "admitted"


def test_source_movement_retains_issued_report_but_cannot_admit_it(cli, repo, tmp_path, monkeypatch):
    sid = pending(cli, repo, tmp_path, monkeypatch)
    packet = json.loads(ok(cli, "planning", "packet")[1])
    start_writer(cli, tmp_path)
    (repo / "svc/app.py").write_text("changed_after_planning = 42\n")
    report, path, saved = retain(cli, tmp_path, packet)
    immutable = Path(saved["report"]["stored_path"]).read_bytes()
    assert saved["authoring"]["source_currency"] == "issued-observation-only"
    assert json.loads(immutable) == report
    assert report["binding"] == packet["content"]["binding"]
    # An identical resubmit is an idempotent success that changes nothing.
    before = al_store.state_path(sid).read_bytes()
    again = json.loads(ok(cli, "planning", "report", "--file", str(path))[1])
    assert again["report"] == saved["report"]
    assert al_store.state_path(sid).read_bytes() == before
    assert cli("planning", "admit")[0] != 0
    assert al_store.state_path(sid).read_bytes() == before
    ok(cli, "writer", "finish", "--id", "write-A", "--outcome", "success")
    before = al_store.state_path(sid).read_bytes()
    result = cli("planning", "admit")
    assert result[0] != 0 and "source observation changed" in "".join(map(str, result))
    assert al_store.state_path(sid).read_bytes() == before
    assert Path(saved["report"]["stored_path"]).read_bytes() == immutable
    fresh = json.loads(ok(cli, "planning", "packet")[1])
    assert fresh["packet"]["sha256"] != packet["packet"]["sha256"]
    assert fresh["content"]["attempt"]["attempt_id"] != packet["content"]["attempt"]["attempt_id"]
    state = al_store.load(sid)
    assert state["planning_history"][-1]["report"] == saved["report"]
    assert "report" not in state["planning_authorship"]
    assert state["proof_admission"]["status"] == "pending"


@pytest.mark.parametrize("gap", ["unresolved-writer", "running-check", "interrupted-check", "uncertain-cleanup"])
def test_authoring_preserves_unsettled_work_refusals(cli, repo, tmp_path, monkeypatch, gap):
    sid = pending(cli, repo, tmp_path, monkeypatch)
    start_writer(cli, tmp_path)
    state = copy.deepcopy(al_store.load(sid))
    if gap == "unresolved-writer":
        state["writers"]["write-A"]["state"] = "UNRESOLVED"
    else:
        entry = {"id": "E999", "status": "PASS"}
        if gap == "running-check":
            entry["status"] = "RUNNING"
        elif gap == "interrupted-check":
            entry["interrupted"] = True
        else:
            entry["cleanup"] = {"complete": False}
        state["evidence"]["entries"].append(entry)
    with pytest.raises(SystemExit, match="UNRESOLVED|check E999 unresolved|owned cleanup unresolved"):
        planning.ready(state, authoring=True)


def test_packet_publication_still_refuses_source_movement(cli, repo, tmp_path, monkeypatch):
    import al_task_attribution

    sid = pending(cli, repo, tmp_path, monkeypatch)
    start_writer(cli, tmp_path)
    original = al_task_attribution.observe
    calls = []

    def moved(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            (repo / "svc/app.py").write_text("moved_during_packet_publication = 1\n")
        return original(*args, **kwargs)

    monkeypatch.setattr(al_task_attribution, "observe", moved)
    before = al_store.state_path(sid).read_bytes()
    result = cli("planning", "packet")
    assert result[0] != 0 and "source moved before packet publication" in "".join(map(str, result))
    assert len(calls) == 2
    assert al_store.state_path(sid).read_bytes() == before
    assert al_store.load(sid)["writers"]["write-A"]["state"] == "ACTIVE"
