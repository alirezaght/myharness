#!/usr/bin/env python3
"""Evidence ledger behavior: real logs, scoped reuse and invalidation."""
import json
import os
import sys
import time
from pathlib import Path

from evidence_helpers import PY, SAME_SESSION, entries, git, ok_cmd, plan_both, slow_check, start_implementation, wait_running

def test_concurrent_runs_get_distinct_ids_and_never_corrupt_state(cli, cli_process, tmp_path, state_file):
    cli("start", "--spec", "Verify concurrent evidence runs retain unique ledger IDs and intact state.")
    procs = [cli_process("evidence", "run", "--area", f"a{i}", "--scope", "svc", "--",
                         "/usr/bin/printf", "%s\\n", f"{i + 1} passed") for i in range(6)]
    for p in procs:
        p.wait(timeout=60)
    found = entries(state_file)
    assert sorted(e["id"] for e in found) == [f"E{i}" for i in range(1, 7)]
    assert all(e["status"] == "PASS" for e in found)


def test_non_utf8_content_change_is_detected(cli, repo):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    (repo / "svc/latin1.txt").write_bytes(b"caf\xe9")
    cli("evidence", "plan", "--area", "svc", "--scope", "svc", "--", *ok_cmd("1 passed"))
    cli("evidence", "run", "--area", "svc")
    (repo / "svc/latin1.txt").write_bytes(b"caf\xe8")
    assert cli("evidence", "require")[0] == 13


def test_start_refuses_a_repository_without_a_target_commit(cli, tmp_path, monkeypatch, state_file):
    import al_store
    fresh = tmp_path / "fresh"
    (fresh / "src").mkdir(parents=True)
    (fresh / "src/a.txt").write_text("1")
    git(fresh, "init", "-q")
    git(fresh, "add", ".")
    monkeypatch.setattr(al_store, "REPO_ROOT", fresh)
    code, out, err = cli("start", "--spec", "Pin the target commit before collecting evidence for this task.")
    assert isinstance(code, str) and "git failed" in code
    assert not state_file("al-test").exists()


def test_committing_the_tested_content_keeps_the_evidence(cli, repo):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    (repo / "svc/app.py").write_text("x = 7\n")
    cli("evidence", "plan", "--area", "svc", "--scope", "svc", "--", *ok_cmd("1 passed"))
    cli("evidence", "run", "--area", "svc")
    git(repo, "commit", "-qam", "tested")
    assert cli("evidence", "require")[0] == 0


def test_a_scope_that_matches_nothing_or_leaves_the_repo_is_refused(cli):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    for bad in ("srcc", "../outside"):
        code, *_ = cli("evidence", "plan", "--area", "scope-check", "--scope", bad, "--", *ok_cmd("1 passed"))
        assert isinstance(code, str) and "invalid --scope" in code, bad


def test_timeout_kills_a_command_that_ignores_sigterm(cli, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    stubborn = [PY, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"]
    started = time.time()
    code, *_ = cli("evidence", "run", "--area", "svc", "--scope", "svc", "--timeout", "0.5", "--", *stubborn)
    assert code == 1 and time.time() - started < 30
    assert entries(state_file)[0]["status"] == "INFRA"


def test_adding_an_env_var_to_the_plan_does_not_reuse_the_old_pass(cli, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    cli("evidence", "plan", "--area", "svc", "--scope", "svc", "--", *ok_cmd("1 passed"))
    cli("evidence", "run", "--area", "svc")
    cli("evidence", "plan", "--area", "svc", "--scope", "svc", "--env", "HOME", "--", *ok_cmd("1 passed"))
    code, out, _ = cli("evidence", "run", "--area", "svc")
    assert code == 0 and "reused" not in out
    assert len(entries(state_file)) == 2
