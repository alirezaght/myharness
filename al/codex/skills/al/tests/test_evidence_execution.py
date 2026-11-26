#!/usr/bin/env python3
"""Evidence ledger behavior: real logs, scoped reuse and invalidation."""
import json
import os
import sys
import time
from pathlib import Path

from evidence_helpers import PY, SAME_SESSION, entries, git, ok_cmd, plan_both, slow_check, start_implementation, wait_running

def test_run_records_argv_cwd_exit_log_counts_and_fingerprint(cli, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    code, out, _ = cli("evidence", "run", "--area", "svc", "--scope", "svc", "--cwd", "svc",
                       "--", *ok_cmd("3 passed, 1 skipped in 0.1s"))
    assert code == 0
    e = entries(state_file)[0]
    assert (e["status"], e["exit"], e["cwd"]) == ("PASS", 0, "svc")
    assert e["argv"][0] == PY
    assert e["counts"] == {"passed": 3, "skipped": 1}
    assert "3 passed, 1 skipped" in Path(e["log"]).read_text()
    assert e["fp_before"] == e["fp_after"] and e["fp_after"]


def test_running_check_reports_execution_after_reservation_without_caller_values(
        cli, run_state, state_file, monkeypatch, capsys, repo):
    import al_evidence_storage

    run_state("implemented")
    observed = []
    original_launch = al_evidence_storage.PreparedStorage.launch

    def launch(storage, *args, **kwargs):
        process = original_launch(storage, *args, **kwargs)
        original_wait = process.wait

        def wait(*args, **kwargs):
            # Observe the actual terminal before delegating to the genuine owned child.
            observed.append(capsys.readouterr().err)
            return original_wait(*args, **kwargs)

        process.wait = wait
        return process

    monkeypatch.setattr(al_evidence_storage.PreparedStorage, "launch", launch)
    private = "private-progress-canary"
    code, out, err = cli("evidence", "run", "--area", private, "--scope", "svc",
                         "--", PY, "-c", "print('3 passed') # " + private)
    assert code == 0, (out, err)
    assert observed
    waiting = observed[0]
    assert "running registered check" in waiting
    assert waiting.rfind("guarded run state saved") < waiting.rfind("running registered check")
    assert private not in waiting and str(repo) not in waiting
    matches = [entry for entry in entries(state_file) if entry['area'] == private]
    assert len(matches) == 1
    entry = matches[0]
    assert entry["status"] == "PASS" and entry["exit"] == 0
    assert entry["counts"] == {"passed": 3}
    assert entry["cleanup"]["complete"] and entry["cleanup"]["leader_reaped"]


def test_unchanged_inputs_reuse_the_run(cli, repo, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    plan_both(cli)
    cli("evidence", "run", "--area", "svc")
    code, out, _ = cli("evidence", "run", "--area", "svc")
    assert code == 0
    assert "E1 reused" in out
    assert len(entries(state_file)) == 1


def test_change_outside_scope_in_the_checks_package_reruns_it(cli, repo, state_file):
    """Issue #21: a check rooted at the repository reads more than its declared scope
    (a UI spec runs against a build of src/), so any change there re-runs it."""
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    plan_both(cli)
    assert cli("evidence", "run", "--area", "svc")[0] == 0
    (repo / "web/app.js").write_text("let a = 2\n")
    code, out, _ = cli("evidence", "run", "--area", "svc")
    assert code == 0
    assert "E1 not reused" in out and "E1 reused" not in out
    assert [e["id"] for e in entries(state_file)] == ["E1", "E2"]
    # Validity is unchanged: the out-of-scope edit never staled E1, only its reuse.
    assert cli("evidence", "run", "--area", "svc")[1].count("E2 reused") == 1


def test_change_in_another_package_keeps_reuse(cli, repo, state_file):
    (repo / "web/package.json").write_text("{}\n")
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    cli("evidence", "plan", "--area", "web", "--cwd", "web", "--scope", "web/app.js",
        "--", *ok_cmd("Tests  4 passed (4)"))
    assert cli("evidence", "run", "--area", "web")[0] == 0
    (repo / "svc/app.py").write_text("x = 2\n")
    assert "E1 reused" in cli("evidence", "run", "--area", "web")[1]
    (repo / "web/build.js").write_text("// generated\n")
    out = cli("evidence", "run", "--area", "web")[1]
    assert "E1 not reused" in out and "E2" in out


def test_entry_without_recorded_inputs_is_rerun_not_refused(cli, repo, state_file):
    import al_ledger as L
    entry = {"cwd": ".", "scope": ["svc"]}
    assert not L.inputs_current(entry)
    entry["inputs_before"] = "stale"
    assert not L.inputs_current(entry)


def test_fixture_change_inside_the_scope_invalidates_the_run(cli, repo):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    cli("evidence", "plan", "--area", "svc", "--scope", "svc", "--", *ok_cmd("1 passed"))
    cli("evidence", "run", "--area", "svc")
    (repo / "svc/tests/fixtures/rows.json").write_text('[{"id": 1}]\n')
    code, out, err = cli("evidence", "require", "--tier", "broad")
    assert code == 13
    assert "STALE" in out


def test_untracked_file_and_ignored_named_config_count_as_scope(cli, repo):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    (repo / ".gitignore").write_text(".env\n")
    (repo / ".env").write_text("A=1\n")
    cli("evidence", "plan", "--area", "svc", "--scope", "svc,.env", "--", *ok_cmd("1 passed"))
    cli("evidence", "run", "--area", "svc")
    (repo / ".env").write_text("A=2\n")
    assert cli("evidence", "require")[0] == 13
    cli("evidence", "run", "--area", "svc")
    (repo / "svc/new_module.py").write_text("y = 2\n")
    assert cli("evidence", "require")[0] == 13


def test_declared_env_var_change_invalidates_without_storing_the_value(cli, monkeypatch, state_file):
    monkeypatch.setenv("AL_TEST_SECRET", "hunter2")
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    cli("evidence", "plan", "--area", "svc", "--scope", "svc", "--env", "AL_TEST_SECRET",
        "--", *ok_cmd("1 passed"))
    cli("evidence", "run", "--area", "svc")
    assert "hunter2" not in state_file().read_text()
    monkeypatch.setenv("AL_TEST_SECRET", "changed")
    assert cli("evidence", "require")[0] == 13


def test_repeated_env_names_are_fingerprinted_and_passed_without_logging_values(
        cli, monkeypatch, state_file):
    first_value, second_value = 'fixture-python-path-value', 'fixture-other-value'
    monkeypatch.setenv('PYTHONPATH', first_value)
    monkeypatch.setenv('AL_TEST_OTHER', second_value)
    cli("start", "--spec", "Verify repeated evidence environment declarations remain effective.")
    command = [PY, "-c", "import os; assert os.environ['PYTHONPATH'] and os.environ['AL_TEST_OTHER']"]
    code, out, err = cli("evidence", "plan", "--area", "env-check", "--kind", "check",
                         "--tier", "focused", "--scope", "svc",
                         "--env", "PYTHONPATH", "--env", "AL_TEST_OTHER,PYTHONPATH", "--", *command)
    assert code == 0, (out, err)
    plan = cli.state()['evidence']['plan']['env-check']
    assert plan['env'] == ['PYTHONPATH', 'AL_TEST_OTHER']
    code, out, err = cli("evidence", "run", "--area", "env-check", "--kind", "check",
                         "--tier", "focused", "--scope", "svc",
                         "--env", "PYTHONPATH", "--env", "AL_TEST_OTHER,PYTHONPATH", "--", *command)
    assert code == 0, (out, err)
    entry = entries(state_file)[0]
    assert entry['env'] == ['AL_TEST_OTHER', 'PYTHONPATH']
    assert entry['fp_before'] == entry['fp_after'] and entry['fp_after']
    log = Path(entry['log']).read_text()
    assert first_value not in log and second_value not in log
    assert first_value not in str(entry) and second_value not in str(entry)
    monkeypatch.setenv('AL_TEST_OTHER', 'changed-fixture-value')
    code, out, err = cli('evidence', 'require', '--areas', 'env-check')
    assert code == 13 and 'STALE' in out and 'changed-fixture-value' not in out + err


def test_preflight_repeated_env_flags_keep_names_only_and_run_check(cli, monkeypatch, state_file):
    monkeypatch.setenv('AL_PREFLIGHT_ONE', 'fixture-one-value')
    monkeypatch.setenv('AL_PREFLIGHT_TWO', 'fixture-two-value')
    cli("start", "--spec",
        "Verify preflight accepts repeated environment names without exposing values.")
    code, out, err = cli("preflight", "--name", "env", "--check", "env:AL_PREFLIGHT_ONE",
                         "--check", "env:AL_PREFLIGHT_TWO",
                         "--env", "AL_PREFLIGHT_ONE,AL_PREFLIGHT_TWO", "--env", "AL_PREFLIGHT_TWO")
    assert code == 0, (out, err)
    entry = entries(state_file)[0]
    assert entry['env'] == ['AL_PREFLIGHT_ONE', 'AL_PREFLIGHT_TWO']
    assert 'fixture-one-value' not in out + err and 'fixture-two-value' not in out + err
    assert 'fixture-one-value' not in str(entry) and 'fixture-two-value' not in str(entry)


def test_missing_log_blocks_require_and_validate(cli, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    plan_both(cli)
    cli("evidence", "run", "--area", "svc")
    cli("evidence", "run", "--area", "web")
    Path(entries(state_file)[0]["log"]).unlink()
    assert cli("evidence", "require")[0] == 13
    code, _, err = cli("evidence", "validate", "--claims", "E1=PASS")
    assert code == 14 and "LOG_MISSING" in err


def test_edited_log_is_not_evidence(cli, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", *ok_cmd("1 passed"))
    log = Path(entries(state_file)[0]["log"])
    log.write_text(log.read_text() + "40 passed\n")
    code, _, err = cli("evidence", "validate", "--claims", "E1=PASS")
    assert code == 14 and "LOG_ALTERED" in err


def test_invented_id_and_wrong_status_claims_are_rejected(cli):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", PY, "-c", "raise SystemExit(1)")
    code, _, err = cli("evidence", "validate", "--claims", "E1=PASS,E7=PASS")
    assert code == 14
    assert "claimed PASS, ledger says FAIL" in err
    assert "E7: no such ledger entry" in err
    assert cli("evidence", "validate", "--claims", "E1=FAIL")[0] == 0


def test_zero_tests_is_empty_not_pass(cli, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    code, *_ = cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", *ok_cmd("0 passed in 0.01s"))
    assert code == 1
    assert entries(state_file)[0]["status"] == "EMPTY"


def test_missing_command_is_an_infrastructure_failure(cli, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    code, *_ = cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", "al-no-such-binary-xyz")
    assert code == 1
    assert entries(state_file)[0]["status"] == "INFRA"


def test_preflight_failure_stops_the_broad_suite_before_it_runs(cli, tmp_path):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    code, out, err = cli("preflight", "--name", "db", "--check", "port:127.0.0.1:1", "--check", "loopback")
    assert code == 15
    assert "MISS port:127.0.0.1:1" in out
    marker = tmp_path / "suite-ran"
    cli("evidence", "plan", "--area", "svc", "--scope", "svc", "--preflight", "db",
        "--", *ok_cmd("9 passed", marker))
    code, _, err = cli("evidence", "run", "--area", "svc")
    assert code == 15 and "preflight `db` has not passed" in err
    assert not marker.exists()


def test_a_passed_preflight_survives_source_changes(cli, repo, tmp_path):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    assert cli("preflight", "--name", "rt", "--check", "env:PATH")[0] == 0
    (repo / "svc/app.py").write_text("x = 5\n")
    cli("evidence", "plan", "--area", "svc", "--scope", "svc", "--preflight", "rt", "--", *ok_cmd("1 passed"))
    assert cli("evidence", "run", "--area", "svc")[0] == 0


def test_preflight_never_prints_an_env_value(cli, monkeypatch):
    monkeypatch.setenv("AL_TEST_TOKEN", "s3cr3t-value")
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    code, out, err = cli("preflight", "--name", "env", "--check", "env:AL_TEST_TOKEN")
    assert code == 0
    assert "s3cr3t-value" not in out + err


def test_broad_browser_suite_waits_for_the_focused_runtime_check(cli, tmp_path):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    marker = tmp_path / "e2e-ran"
    cli("evidence", "plan", "--area", "alpine-focused", "--tier", "focused", "--scope", "web",
        "--", PY, "-c", "raise SystemExit(1)")
    cli("evidence", "plan", "--area", "e2e", "--scope", "web", "--needs", "alpine-focused",
        "--", *ok_cmd("30 passed", marker))
    code, _, err = cli("evidence", "run", "--area", "e2e")
    assert code == 15 and "alpine-focused" in err and not marker.exists()
    cli("evidence", "run", "--area", "alpine-focused")
    code, _, err = cli("evidence", "run", "--area", "e2e")
    assert code == 15 and "now FAIL" in err


def test_independent_fix_may_overlap_a_running_check(cli, cli_process, repo, tmp_path, state_file, run_state):
    run_state("implemented")
    release = tmp_path / "release"
    proc = cli_process("evidence", "run", "--area", "svc", "--scope", "svc", "--", *slow_check(release))
    wait_running(state_file, "svc", process=proc)
    code, out, _ = cli("overlap", "--area", "svc", "--fix-files", "web/app.js")
    assert code == 0 and "Overlap allowed" in out
    (repo / "web/app.js").write_text("let a = 3\n")
    release.write_text("go")
    proc.wait(timeout=20)
    assert [e["status"] for e in entries(state_file) if e["area"] == "svc"] == ["PASS"]
    assert all(e["status"] == "PASS" for e in entries(state_file))


def test_coupled_fix_stays_sequential_and_a_moved_scope_tears_the_run(cli, cli_process, repo, tmp_path,
                                                                      state_file):
    start_implementation(cli, tmp_path)
    release = tmp_path / "release"
    proc = cli_process("evidence", "run", "--area", "svc", "--scope", "svc", "--", *slow_check(release))
    wait_running(state_file, "svc", process=proc)
    running = next(e for e in entries(state_file) if e["area"] == "svc" and e["status"] == "RUNNING")
    code, _, err = cli("overlap", "--area", "svc", "--fix-files", "svc/tests/fixtures/rows.json")
    assert code == 5 and f"lies in running check {running['id']} (svc)" in err
    code, _, err = cli("overlap", "--area", "svc", "--fix-files", "web/app.js", "--fix-commands", "pnpm install")
    assert code == 5 and "dependency/generation mutation" in err
    (repo / "svc/app.py").write_text("x = 2\n")  # someone ignored the refusal
    release.write_text("go")
    proc.wait(timeout=20)
    assert entries(state_file)[-1]["status"] == "TORN"


def test_no_overlap_at_a_barrier(cli, tmp_path, run_state):
    run_state("implemented")
    cli("phase", "VERIFICATION")
    code, _, err = cli("overlap", "--area", "svc", "--fix-files", "web/app.js")
    assert code == 5 and "barrier" in err


def test_review_start_is_only_available_after_verification(cli, repo):
    cli("start", "--spec", "Review requires a complete verification route for the frozen candidate.")
    cli("spec-gate", "SPEC_READY")  # admitted planning isolates the phase gate under test
    cli("classify", "SMALL", "--designer", "no", "--implementers", "al-engineer")
    plan_both(cli)
    before = cli("show")[1]
    code, out, err = cli("review-start")
    assert code != 0 and str(code) == "al_state: review-start is not allowed from PLANNING"
    assert cli("show")[1] == before


def test_review_without_a_plan_is_refused_from_planning(cli):
    cli("start", "--spec", "Review cannot bypass the planned verification and gate route.")
    cli("spec-gate", "SPEC_READY")  # admitted planning isolates the phase gate under test
    cli("classify", "SMALL", "--designer", "no", "--implementers", "al-engineer")
    code, out, err = cli("review-start")
    assert code != 0 and str(code) == "al_state: review-start is not allowed from PLANNING"
