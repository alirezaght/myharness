"""Remote gate provenance is checked against a local stub, never a live service."""
import json
import os
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import al_evidence
import al_ledger
import al_remote_gates
import al_store
from al_gate_process import Owner


def make_gate(cli, state_file, tmp_path, monkeypatch, **changes):
    cli("start", "--spec", "Only exact remote GitLab job provenance can satisfy this gate.")
    st = json.loads(state_file().read_text())
    executable = tmp_path / "glab-stub"
    executable.write_text("#!/usr/bin/env python3\nimport json,os\nprint(json.dumps({"
                          "'id':int(os.environ.get('JOB_ID','9')),'name':os.environ.get('JOB_NAME','verify'),"
                          "'pipeline':{'id':int(os.environ.get('PIPELINE_ID','11')),'project_id':int(os.environ.get('PIPE_PROJECT','42'))},"
                          "'commit':{'id':os.environ.get('JOB_COMMIT')},'status':os.environ.get('JOB_STATUS','success')}))\n")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("JOB_COMMIT", st["identity"]["merge_base"])
    gate = {"argv": [str(executable)], "remote": {"provider": "gitlab", "project_id": 42, "job_name": "verify"}}
    gate.update(changes)
    entry = al_evidence._create(st["session_id"], st["run_id"], area="remote", kind="check",
                                tier="focused", argv=gate["argv"], cwd=".", scope=["."], env_names=[])
    return st, gate, entry, tmp_path / "remote.log"


def execute_owned(st, gate, stage, entry, log):
    result = None
    with Owner(log) as owner:
        result = al_remote_gates.execute_remote(st, gate, stage, log, 5, st["session_id"], entry, owner)

        def persist(reason):
            current = al_store.load(st["session_id"])
            recorded = next(item for item in current["evidence"]["entries"] if item["id"] == entry["id"])
            assert recorded["reservation"] == entry["reservation"] and recorded["status"] == "RUNNING"
            status, code = ("INFRA", 130) if reason else result[:2]
            recorded.update(status=status, exit=code, ended_at=time.time(), log=str(log),
                            log_sha256=al_ledger.file_sha(log), fp_after=recorded["fp_before"])
            if reason:
                recorded["interrupted"] = True
            al_store.save(st["session_id"], current)

        with al_store.locked(st["session_id"]):
            owner.finish(persist)
    current = al_store.load(st["session_id"])
    recorded = next(item for item in current["evidence"]["entries"] if item["id"] == entry["id"])
    assert recorded["status"] == result[0] and recorded["exit"] == result[1]
    assert recorded["log_sha256"] == al_ledger.file_sha(log)
    return result


def test_remote_gate_accepts_only_exact_job_pipeline_and_revision(cli, state_file, tmp_path, monkeypatch):
    st, gate, entry, log = make_gate(cli, state_file, tmp_path, monkeypatch)
    status, code, provenance = execute_owned(st, gate, "base", entry, log)
    assert (status, code) == ("PASS", 0)
    assert provenance["project_id"] == 42 and provenance["pipeline_id"] == 11
    assert provenance["commit"] == st["identity"]["merge_base"]


def test_remote_gate_mismatch_is_infra_and_a_failed_job_is_a_failure(cli, state_file, tmp_path, monkeypatch):
    st, gate, entry, log = make_gate(cli, state_file, tmp_path, monkeypatch)
    monkeypatch.setenv("JOB_NAME", "different")
    status, code, result = execute_owned(st, gate, "base", entry, log)
    assert status == "INFRA" and result["reason"] == "remote job provenance mismatch"
    monkeypatch.delenv("JOB_NAME")
    st, gate, entry, log = make_gate(cli, state_file, tmp_path, monkeypatch)
    monkeypatch.setenv("JOB_STATUS", "failed")
    status, code, provenance = execute_owned(st, gate, "base", entry, log)
    assert (status, code) == ("FAIL", 1) and provenance["status"] == "failed"


def test_remote_candidate_cannot_claim_uncommitted_work(cli, state_file, repo, tmp_path, monkeypatch):
    st, gate, entry, log = make_gate(cli, state_file, tmp_path, monkeypatch)
    (repo / "uncommitted.py").write_text("candidate change\n")
    status, code, provenance = execute_owned(st, gate, "candidate", entry, log)
    assert status == "INFRA" and provenance["reason"] == "uncommitted candidate"
