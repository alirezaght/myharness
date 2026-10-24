#!/usr/bin/env python3
"""Evidence ledger behavior: real logs, scoped reuse and invalidation."""
import json
import os
import sys
import time
from pathlib import Path

from evidence_helpers import PY, SAME_SESSION, entries, git, ok_cmd, plan_both, slow_check, start_implementation, wait_running

def test_a_stale_pass_is_not_accepted_as_a_current_claim(cli, repo):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", *ok_cmd("1 passed"))
    (repo / "svc/app.py").write_text("x = 3\n")
    code, _, err = cli("evidence", "validate", "--claims", "E1=PASS")
    assert code == 14 and "STALE" in err




def test_an_unfamiliar_runner_passes_with_unknown_count_but_silence_is_empty(cli, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    # Any ecosystem's runner: exit 0 with a summary AL does not know is a pass with an unknown count.
    code, *_ = cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", *ok_cmd("all good"))
    assert code == 0
    assert entries(state_file)[0]["status"] == "PASS" and "not recognised" in entries(state_file)[0]["note"]
    code, *_ = cli("evidence", "run", "--area", "quiet", "--scope", "svc", "--", PY, "-c", "pass")
    assert code == 1 and entries(state_file)[-1]["status"] == "EMPTY"
    code, *_ = cli("evidence", "run", "--area", "lint", "--kind", "lint", "--scope", "svc", "--", *ok_cmd("ok"))
    assert code == 0


def test_needs_is_not_satisfied_by_a_pass_of_an_older_command(cli, tmp_path):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    marker = tmp_path / "e2e-ran"
    cli("evidence", "plan", "--area", "focused", "--tier", "focused", "--scope", "web", "--", *ok_cmd("1 passed"))
    cli("evidence", "run", "--area", "focused")
    cli("evidence", "plan", "--area", "focused", "--tier", "focused", "--scope", "web",
        "--", *ok_cmd("2 passed"))
    cli("evidence", "plan", "--area", "e2e", "--scope", "web", "--needs", "focused", "--", *ok_cmd("5 passed", marker))
    code, _, err = cli("evidence", "run", "--area", "e2e")
    assert code == 15 and "focused" in err and not marker.exists()


def test_timeout_also_kills_descendants_that_outlive_the_parent(cli, tmp_path):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    pidfile = tmp_path / "child.pid"
    script = ("import os,signal,subprocess,sys,time\n"
              "c = subprocess.Popen([sys.executable, '-c', "
              "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)'])\n"
              f"open({str(pidfile)!r}, 'w').write(str(c.pid))\n"
              "time.sleep(60)\n")
    cli("evidence", "run", "--area", "svc", "--scope", "svc", "--timeout", "1", "--", PY, "-c", script)
    child = int(pidfile.read_text())
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise AssertionError("descendant survived the timeout")


def test_adding_a_prerequisite_to_the_plan_voids_the_earlier_pass(cli):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    cli("evidence", "plan", "--area", "svc", "--scope", "svc", "--", *ok_cmd("1 passed"))
    cli("evidence", "run", "--area", "svc")
    cli("evidence", "plan", "--area", "svc", "--scope", "svc", "--preflight", "db", "--", *ok_cmd("1 passed"))
    code, out, _ = cli("evidence", "require")
    assert code == 13 and "NOT_RUN" in out


def test_suite_and_file_summaries_and_the_echoed_command_are_not_test_counts(cli, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    jest = ok_cmd("Test Suites: 1 passed, 1 total\nTests:       1 todo, 1 total")
    code, *_ = cli("evidence", "run", "--area", "jest", "--scope", "svc", "--", *jest)
    assert code == 1 and entries(state_file)[-1]["status"] == "EMPTY"
    vitest = ok_cmd("Test Files  2 passed (2)")
    code, *_ = cli("evidence", "run", "--area", "vitest", "--scope", "svc", "--", *vitest)
    assert code == 1 and entries(state_file)[-1]["status"] == "EMPTY"
    code, *_ = cli("evidence", "run", "--area", "echo", "--scope", "svc", "--", PY, "-c", "pass", "3 passed")
    assert code == 1 and entries(state_file)[-1]["status"] == "EMPTY"


def test_an_unreadable_scoped_file_is_unverifiable_not_valid(cli, repo):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    cli("evidence", "plan", "--area", "svc", "--scope", "svc", "--", *ok_cmd("1 passed"))
    cli("evidence", "run", "--area", "svc")
    secret = repo / "svc/app.py"
    secret.chmod(0)
    try:
        code, out, _ = cli("evidence", "require")
        assert code == 13 and "UNVERIFIABLE" in out
    finally:
        secret.chmod(0o644)


def test_removing_the_executable_bit_invalidates_the_run(cli, repo):
    script = repo / "svc/run.sh"
    script.write_text("#!/bin/sh\necho 1 passed\n")
    script.chmod(0o755)
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    cli("evidence", "plan", "--area", "svc", "--scope", "svc", "--", *ok_cmd("1 passed"))
    cli("evidence", "run", "--area", "svc")
    script.chmod(0o644)
    assert cli("evidence", "require")[0] == 13


def test_a_multiline_argv_is_not_mistaken_for_test_output(cli, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    code, *_ = cli("evidence", "run", "--area", "x", "--scope", "svc", "--", PY, "-c", "pass\n# 3 passed")
    assert code == 1 and entries(state_file)[-1]["status"] == "EMPTY"


def test_turbo_prefixed_suite_summaries_are_not_test_counts(cli, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    out = ok_cmd("@acme/web:test: Test Suites: 1 passed, 1 total\n@acme/web:test: Tests: 1 todo, 1 total")
    code, *_ = cli("evidence", "run", "--area", "x", "--scope", "svc", "--", *out)
    assert code == 1 and entries(state_file)[-1]["status"] == "EMPTY"


def test_review_start_is_refused_from_planning_before_it_can_skip_gates(cli, tmp_path):
    from launcher_context import admit_planning
    cli("start", "--spec", "A complete task package must have sourced repository gates before review.")
    cli("spec-gate", "SPEC_READY")
    cli("classify", "SMALL", "--designer", "no", "--implementers", "al-engineer")
    admit_planning(cli, tmp_path)  # isolate the phase gate from proof admission
    cli("evidence", "plan", "--area", "focused", "--tier", "focused", "--scope", "web", "--", *ok_cmd("1 passed"))
    cli("evidence", "run", "--area", "focused")
    code, out, err = cli("review-start")
    assert code != 0 and "PLANNING" in str(code)


def test_inflight_result_stays_with_its_immutable_run(cli, cli_process, tmp_path, state_file):
    cli("start", "--spec", "The first immutable run records its own completed evidence operation.")
    first_state = state_file()
    release = tmp_path / "release"
    proc = cli_process("evidence", "run", "--area", "old", "--scope", "svc", "--", *slow_check(release))
    wait_running(state_file, "old", process=proc)
    cli("--session", "al-test-second", "start", "--spec", "The second immutable run keeps new evidence separate from the first run.")
    second_state = state_file("al-test-second")
    cli("--session", "al-test-second", "evidence", "run", "--area", "new", "--scope", "web", "--", *ok_cmd("1 passed"))
    release.write_text("go")
    proc.wait(timeout=20)
    assert [(e["id"], e["area"], e["status"]) for e in entries(lambda: second_state)] == [("E1", "new", "PASS")]
    assert [(e["id"], e["area"], e["status"]) for e in json.loads(first_state.read_text())["evidence"]["entries"]] == [("E1", "old", "PASS")]


def test_a_broken_preflight_two_levels_down_blocks_required_evidence(cli, tmp_path):
    start_implementation(cli, tmp_path)
    cli("preflight", "--name", "rt", "--check", "env:PATH")
    cli("evidence", "plan", "--area", "focused", "--tier", "focused", "--scope", "web", "--preflight", "rt",
        "--", *ok_cmd("1 passed"))
    cli("evidence", "plan", "--area", "e2e", "--scope", "svc", "--needs", "focused", "--", *ok_cmd("9 passed"))
    cli("evidence", "run", "--area", "focused")
    cli("evidence", "run", "--area", "e2e")
    cli("phase", "VERIFICATION")
    code, out, err = cli("evidence", "require", "--tier", "broad")
    assert code == 0, out + err
    assert cli("preflight", "--name", "rt", "--check", "port:127.0.0.1:1")[0] == 15
    code, out, err = cli("evidence", "require", "--tier", "broad")
    assert code == 13 and "PREREQ_INVALID" in out + err


def test_a_needs_cycle_is_never_valid(cli):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    cli("evidence", "plan", "--area", "a", "--scope", "svc", "--needs", "b", "--", *ok_cmd("1 passed"))
    cli("evidence", "plan", "--area", "b", "--scope", "web", "--needs", "a", "--", *ok_cmd("1 passed"))
    assert cli("evidence", "require")[0] == 13


def test_ad_hoc_prerequisites_are_rechecked_recursively(cli, tmp_path):
    start_implementation(cli, tmp_path)
    cli("preflight", "--name", "rt", "--check", "env:PATH")
    cli("evidence", "plan", "--area", "focused", "--tier", "focused", "--scope", "web", "--preflight", "rt",
        "--", *ok_cmd("1 passed"))
    cli("evidence", "run", "--area", "focused")
    cli("evidence", "plan", "--area", "e2e", "--scope", "svc", "--needs", "focused", "--", *ok_cmd("9 passed"))
    cli("evidence", "run", "--area", "e2e")
    cli("phase", "VERIFICATION")
    code, out, err = cli("evidence", "require", "--tier", "broad")
    assert code == 0, out + err
    cli("preflight", "--name", "rt", "--check", "port:127.0.0.1:1")
    code, out, err = cli("evidence", "require", "--tier", "broad")
    assert code == 13 and "PREREQ_INVALID" in out + err




def test_log_files_of_separate_runs_never_collide(cli, state_file):
    cli("start", "--spec", "The first immutable run records its own evidence artifacts.")
    first_state = state_file()
    cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", *ok_cmd("1 passed"))
    first_log = entries(lambda: first_state)[0]["log"]
    cli("--session", "al-test-second", "start", "--spec", "The second immutable run records a separate evidence artifact.")
    second_state = state_file("al-test-second")
    cli("--session", "al-test-second", "evidence", "run", "--area", "svc", "--scope", "svc", "--", *ok_cmd("2 passed"))
    second_log = entries(lambda: second_state)[0]["log"]
    assert first_log != second_log and "1 passed" in Path(first_log).read_text()


def test_overlap_normalises_fix_paths(cli, cli_process, repo, tmp_path, state_file):
    start_implementation(cli, tmp_path)
    release = tmp_path / "release"
    proc = cli_process("evidence", "run", "--area", "svc", "--scope", "svc", "--", *slow_check(release))
    wait_running(state_file, "svc", process=proc)
    try:
        for fix in (str(repo / "svc/app.py"), ".", "svc/../svc/app.py"):
            code, *_ = cli("overlap", "--area", "svc", "--fix-files", fix)
            assert code == 5, fix
        code, _, err = cli("overlap", "--area", "svc", "--fix-files", ".")
        assert "is the whole repository" in err
        code, _, err = cli("overlap", "--area", "svc", "--fix-files", str(tmp_path / "elsewhere.py"))
        assert code == 5 and "outside the repository" in err
        assert cli("overlap", "--area", "svc", "--fix-files", str(repo / "web/app.js"))[0] == 0
    finally:
        release.write_text("go")
        proc.wait(timeout=20)


def test_a_pass_claim_whose_prerequisite_broke_is_rejected(cli):
    cli("start", "--spec", "Evidence must remain bound to valid prerequisites and environment names.")
    cli("preflight", "--name", "rt", "--check", "env:PATH")
    cli("evidence", "run", "--area", "svc", "--scope", "svc", "--preflight", "rt", "--", *ok_cmd("1 passed"))
    assert cli("evidence", "validate", "--claims", "E2=PASS")[0] == 0
    cli("preflight", "--name", "rt", "--check", "port:127.0.0.1:1")
    code, _, err = cli("evidence", "validate", "--claims", "E2=PASS")
    assert code == 14 and "PREREQ_INVALID" in err






def test_create_refuses_a_stale_run_reservation(cli, state_file):
    import al_evidence
    import pytest
    cli("start", "--spec", "A stale evidence reservation must not attach to another run identity.")
    current = json.loads(state_file().read_text())
    with pytest.raises(SystemExit) as exc:
        al_evidence._create(current["session_id"], "stale-run-id", area="a", kind="test", tier="focused",
                            argv=[PY, "-c", "print('1 passed')"], cwd=".", scope=["svc"], env_names=[])
    assert "restarted" in str(exc.value.code)
    assert entries(state_file) == []


def test_exit_zero_with_reported_failures_is_a_fail(cli, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    code, *_ = cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", *ok_cmd("3 passed, 1 failed in 0.2s"))
    assert code == 1 and entries(state_file)[0]["status"] == "FAIL"
    code, *_ = cli("evidence", "run", "--area", "web", "--scope", "web", "--", *ok_cmd("4 passed, 2 errors"))
    assert code == 1 and entries(state_file)[1]["status"] == "FAIL"


def test_status_shows_a_broken_prerequisite(cli):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    cli("preflight", "--name", "rt", "--check", "env:PATH")
    cli("evidence", "run", "--area", "svc", "--scope", "svc", "--preflight", "rt", "--", *ok_cmd("1 passed"))
    cli("preflight", "--name", "rt", "--check", "port:127.0.0.1:1")
    _, out, _ = cli("evidence", "status")
    assert "PREREQ_INVALID" in out.splitlines()[1]


def test_an_early_failure_is_not_hidden_by_a_later_passing_suite(cli, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    out = ok_cmd("2 passed, 1 failed in 0.1s\n" + "noise\n" * 200 + "5 passed in 0.2s")
    code, *_ = cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", *out)
    assert code == 1 and entries(state_file)[0]["status"] == "FAIL"


def test_overlap_checks_every_running_check(cli, cli_process, tmp_path, state_file):
    start_implementation(cli, tmp_path)
    r1, r2 = tmp_path / "r1", tmp_path / "r2"
    def waiting_check(marker):
        script = tmp_path / f"wait-{marker.name}.sh"
        script.write_text("#!/bin/sh\nwhile [ ! -e \"$1\" ]; do sleep 0.05; done\nprintf '2 passed\\n'\n")
        script.chmod(0o755)
        return [str(script), str(marker)]
    p1 = cli_process("evidence", "run", "--area", "svc", "--scope", "svc", "--", *waiting_check(r1))
    wait_running(state_file, "svc", process=p1)
    p2 = cli_process("evidence", "run", "--area", "web", "--scope", "web", "--", *waiting_check(r2))
    wait_running(state_file, "web", process=p2)
    try:
        code, _, err = cli("overlap", "--area", "svc", "--fix-files", "web/app.js")
        assert code == 5 and "(web)" in err
        assert cli("overlap", "--area", "svc", "--fix-files", "docs/readme.md")[0] == 0
    finally:
        r1.write_text("go")
        r2.write_text("go")
        p1.wait(timeout=20)
        p2.wait(timeout=20)


def test_a_failed_suite_line_is_a_failure(cli, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    out = ok_cmd("Test Suites: 1 failed, 1 total\nTests: 3 passed, 3 total\nTests: 2 passed, 2 total")
    code, *_ = cli("evidence", "run", "--area", "x", "--scope", "svc", "--", *out)
    assert code == 1 and entries(state_file)[0]["status"] == "FAIL"


def test_old_writer_completion_cannot_update_a_new_run(cli, cli_process, tmp_path, state_file):
    cli("start", "--spec", "A running evidence operation remains attached to its immutable run.")
    first_state = state_file()
    release = tmp_path / "release"
    proc = cli_process("evidence", "run", "--area", "old", "--scope", "svc", "--", *slow_check(release))
    wait_running(state_file, "old", process=proc)
    cli("--session", "al-test-second", "start", "--spec", "A new run receives separate evidence and state.")
    second_state = state_file("al-test-second")
    release.write_text("go")
    out, err = proc.communicate(timeout=20)
    assert proc.returncode == 0, out + err
    assert json.loads(first_state.read_text())["evidence"]["entries"][0]["status"] == "PASS"
    assert json.loads(second_state.read_text())["evidence"]["entries"] == []


def test_a_flaky_only_playwright_run_is_a_pass(cli, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    code, *_ = cli("evidence", "run", "--area", "e2e", "--scope", "web", "--", *ok_cmd("  1 flaky\n  0 failed"))
    assert code == 0 and entries(state_file)[0]["status"] == "PASS"


def test_a_just_allocated_run_counts_as_running_for_overlap(cli, state_file, tmp_path):
    import al_evidence
    start_implementation(cli, tmp_path)
    st = json.loads(state_file().read_text())
    al_evidence._create(st["session_id"], st.get("run_id"), area="svc", kind="test", tier="focused",
                        argv=[sys.executable, "-c", "print('running')"], cwd=".", scope=["svc"], env_names=[])  # no child started yet
    code, _, err = cli("overlap", "--area", "svc", "--fix-files", "svc/app.py")
    assert code == 5 and "lies in running check" in err






def test_cancelling_the_verifier_stops_the_check_and_records_it(cli, cli_process, tmp_path, state_file):
    import signal as sig
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    pidfile = tmp_path / "check.pid"
    check = [PY, "-c", f"import os,time; open({str(pidfile)!r},'w').write(str(os.getpid())); time.sleep(60)"]
    proc = cli_process("evidence", "run", "--area", "svc", "--scope", "svc", "--", *check)
    wait_running(state_file, "svc", process=proc)
    while not pidfile.exists():
        time.sleep(0.05)
    proc.send_signal(sig.SIGINT)
    proc.wait(timeout=30)
    child = int(pidfile.read_text())
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("the check survived the cancelled verifier")
    e = entries(state_file)[0]
    assert e["status"] == "INFRA" and "cancelled" in e["note"]
