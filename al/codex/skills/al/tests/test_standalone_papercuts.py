"""Standalone-run papercuts: package schema, overlapping planning admission, rebind input,
supervisor-stamped invocation ids, sharded evidence areas and superseded failures."""
import json
import sys
from pathlib import Path

import pytest

import al_proof_json as J
import al_store
from launcher_context import _reports
from test_planning_authoring import ok, pending as _pending, retain, start_writer
from test_planning_rebind import prepared as _prepared


AL = Path(__file__).resolve().parent.parent


def pending(cli, repo, tmp_path, monkeypatch, run_state):
    import launcher_context
    result = run_state("papercuts-pending", lambda: _pending(cli, repo, tmp_path, monkeypatch))
    monkeypatch.setattr(launcher_context, "admit_planning", lambda *args, **kwargs: None)
    return result


def prepared(cli, repo, tmp_path, monkeypatch, run_state):
    import launcher_context
    result = run_state("papercuts-prepared", lambda: _prepared(cli, repo, tmp_path, monkeypatch))
    monkeypatch.setattr(launcher_context, "admit_planning", lambda *args, **kwargs: None)
    return result



def test_task_package_schema_requires_content_for_a_present_ticket():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((AL / "reference" / "schemas" / "task-package.json").read_text())
    package = {"original_request": "Add the export button", "accepted_spec": "Add an export button to the report page.",
               "acceptance": {"text": "accepted", "source": "chat"}, "constraints": [],
               "criteria": ["AC-01"], "invariants": []}
    validator = jsonschema.Draft202012Validator(schema)
    assert not validator.is_valid({**package, "ticket": {"present": True}})
    assert validator.is_valid({**package, "ticket": {"present": True, "content": "TICKET-1: add export"}})
    assert validator.is_valid({**package, "ticket": {"present": False, "reason": "no ticket"}})


def test_package_without_ticket_content_names_the_field(cli, tmp_path):
    package = {"original_request": "Add the export button", "accepted_spec": "Add an export button to the report page.",
               "acceptance": {"text": "accepted", "source": "chat"}, "constraints": [],
               "ticket": {"present": True}, "criteria": ["AC-01"], "invariants": []}
    path = tmp_path / "package.json"
    path.write_text(json.dumps(package))
    result = cli("start", "--package-file", str(path))
    assert result[0] != 0 and "ticket.content" in "".join(map(str, result))


def test_overlapping_report_is_admitted_after_the_writer_finishes_its_own_files(cli, repo, tmp_path, monkeypatch, run_state):
    sid = pending(cli, repo, tmp_path, monkeypatch, run_state)
    start_writer(cli, tmp_path)
    packet = json.loads(ok(cli, "planning", "packet")[1])
    (repo / "svc/app.py").write_text("written_by_registered_writer = 1\n")
    retain(cli, tmp_path, packet)
    ok(cli, "writer", "finish", "--id", "write-A", "--outcome", "success")
    admitted = json.loads(ok(cli, "planning", "admit")[1])
    assert admitted["status"] == "admitted" and admitted["writer_movement"] == ["svc/app.py"]
    state = al_store.load(sid)
    assert state["proof_admission"]["status"] == "admitted"
    assert state["planning_authorship"]["admitted_writer_movement"] == ["svc/app.py"]


@pytest.mark.parametrize("edit", ["after-finish", "outside-owned", "failed-writer"])
def test_unattributed_movement_still_needs_a_new_packet(cli, repo, tmp_path, monkeypatch, edit, run_state):
    sid = pending(cli, repo, tmp_path, monkeypatch, run_state)
    start_writer(cli, tmp_path)
    packet = json.loads(ok(cli, "planning", "packet")[1])
    (repo / "svc/app.py").write_text("written_by_registered_writer = 1\n")
    if edit == "outside-owned":
        (repo / "svc/unowned.py").write_text("manual = 1\n")
    retain(cli, tmp_path, packet)
    if edit == "failed-writer":
        ok(cli, "writer", "finish", "--id", "write-A", "--outcome", "failed")
        ok(cli, "writer", "reconcile", "--id", "write-A", "--reason", "inspected the partial edit",
           "--artifact", str(tmp_path / "authored-planning-report.json"))
    else:
        ok(cli, "writer", "finish", "--id", "write-A", "--outcome", "success")
    if edit == "after-finish":
        (repo / "svc/app.py").write_text("manual_edit_after_finish = 2\n")
    before = al_store.state_path(sid).read_bytes()
    result = cli("planning", "admit")
    assert result[0] != 0 and "source observation changed" in "".join(map(str, result))
    assert al_store.state_path(sid).read_bytes() == before


@pytest.mark.parametrize("form", ["printed", "sha256-file", "sha256-argument"])
def test_rebind_accepts_the_printed_packet_or_its_hash(cli, repo, tmp_path, monkeypatch, form, run_state):
    previous, _, _, old_report, _ = prepared(cli, repo, tmp_path, monkeypatch, run_state)
    supplied = tmp_path / "supplied-packet.json"
    if form == "printed":
        supplied.write_text(json.dumps(previous))
        argument = str(supplied)
    elif form == "sha256-file":
        supplied.write_text(previous["packet"]["sha256"] + "\n")
        argument = str(supplied)
    else:
        argument = previous["packet"]["sha256"]
    response = cli("planning", "rebind", "--from-packet", argument, "--from-report", str(old_report))
    if response[0] != 0:
        pytest.fail("Actual public planning rebind response: " + str(response))
    result = json.loads(response[1])
    assert J.read(result["receipt"])["original_packet"] == previous["packet"]


def test_rebind_refusal_lists_retained_packets(cli, repo, tmp_path, monkeypatch, run_state):
    previous, _, _, old_report, fresh = prepared(cli, repo, tmp_path, monkeypatch, run_state)
    result = cli("planning", "rebind", "--from-packet", "0" * 64, "--from-report", str(old_report))
    text = "".join(map(str, result))
    assert result[0] != 0 and previous["packet"]["sha256"] in text and fresh["packet"]["sha256"] in text
    forged = dict(previous, content=dict(previous["content"], routing={"forged": True}))
    path = tmp_path / "forged.json"
    path.write_text(json.dumps(forged))
    result = cli("planning", "rebind", "--from-packet", str(path), "--from-report", str(old_report))
    assert result[0] != 0 and "content differs" in "".join(map(str, result))


def test_supervisor_stamps_the_architect_invocation_id(cli, repo, tmp_path, monkeypatch, run_state):
    sid = pending(cli, repo, tmp_path, monkeypatch, run_state)
    packet = json.loads(ok(cli, "planning", "packet")[1])
    assert packet["report_template"]["invocation_id"].startswith("FILL:")
    report = _reports().report_for(packet)
    report["invocation_id"] = packet["report_template"]["invocation_id"]
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    refused = cli("planning", "report", "--file", str(path))
    assert refused[0] != 0 and "--invocation-id" in "".join(map(str, refused))
    saved = json.loads(ok(cli, "planning", "report", "--file", str(path), "--invocation-id", "agent-a1b2c3")[1])
    assert json.loads(Path(saved["report"]["stored_path"]).read_bytes())["invocation_id"] == "agent-a1b2c3"
    stamp = al_store.load(sid)["planning_authorship"]["supervisor_invocation"]
    assert stamp["invocation_id"] == "agent-a1b2c3"
    assert stamp["authored_invocation_id"] == report["invocation_id"]
    assert json.loads(Path(stamp["authored_report"]["stored_path"]).read_bytes()) == report
    # The identical stamped resubmission is idempotent; admission uses the stamped report.
    ok(cli, "planning", "report", "--file", str(path), "--invocation-id", "agent-a1b2c3")
    ok(cli, "planning", "admit")


@pytest.fixture
def started(cli, run_state):
    result = run_state("papercuts-started", lambda: cli("start", "--spec", "Plan sharded evidence areas and show superseded failures clearly."))
    assert result[0] == 0, result
    return cli


def test_sharded_area_is_evidenced_by_all_current_shard_passes(started, state_file):
    cli = started
    command = [sys.executable, "-c", "import sys; print(sys.argv[1])", "--shard={shard}/{shards}"]
    refused = cli("evidence", "plan", "--area", "e2e", "--shards", "2", "--kind", "check", "--scope", "svc", "--", *command[:-1], "all")
    assert refused[0] != 0 and "{shard}" in "".join(map(str, refused))
    ok(cli, "evidence", "plan", "--area", "e2e", "--shards", "2", "--kind", "check", "--scope", "svc", "--", *command)
    plan = json.loads(state_file().read_bytes())["evidence"]["plan"]
    assert "e2e" not in plan
    assert plan["e2e.shard-1-of-2"]["argv"][-1] == "--shard=1/2"
    assert plan["e2e.shard-2-of-2"]["argv"][-1] == "--shard=2/2"
    named = cli("evidence", "run", "--area", "e2e")
    assert named[0] != 0 and "evidence run --area e2e.shard-2-of-2" in "".join(map(str, named))
    ok(cli, "evidence", "run", "--area", "e2e.shard-1-of-2")
    assert cli("evidence", "require", "--areas", "e2e")[0] == 13
    assert "1/2 shards VALID" in ok(cli, "evidence", "status")[1]
    ok(cli, "evidence", "run", "--area", "e2e.shard-2-of-2")
    assert cli("evidence", "require", "--areas", "e2e")[0] == 0
    assert "2/2 shards VALID" in ok(cli, "evidence", "status")[1]
    ok(cli, "evidence", "plan", "--area", "after", "--kind", "check", "--needs", "e2e", "--scope", "svc", "--",
       sys.executable, "-c", "pass")
    ok(cli, "evidence", "run", "--area", "after")
    ok(cli, "evidence", "retire", "--area", "e2e", "--reason", "Replaced by the CI shard matrix in this run.")
    ledger = json.loads(state_file().read_bytes())["evidence"]
    assert not any(name.startswith("e2e") for name in ledger["plan"])
    assert {item["area"] for item in ledger["retired"]} == {"e2e.shard-1-of-2", "e2e.shard-2-of-2"}
    assert "shard_sets" not in ledger


def test_failure_followed_by_identical_pass_is_shown_superseded(started, tmp_path):
    cli = started
    marker = tmp_path / "ran-once"
    flaky = [sys.executable, "-c",
             "import os,sys; first=not os.path.exists(sys.argv[1]); open(sys.argv[1],'w').close(); sys.exit(1 if first else 0)",
             str(marker)]
    ok(cli, "evidence", "plan", "--area", "unit", "--kind", "check", "--scope", "svc", "--", *flaky)
    assert cli("evidence", "run", "--area", "unit")[0] != 0
    ok(cli, "evidence", "run", "--area", "unit")
    rows = ok(cli, "evidence", "status")[1].splitlines()
    first = next(row for row in rows if row.startswith("E1"))
    assert "superseded by later PASS E2 (same command and scope)" in first
    assert "superseded" not in next(row for row in rows if row.startswith("E2"))
