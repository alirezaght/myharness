#!/usr/bin/env python3
"""Issue #26: the action cache reuses a check's result keyed by its inputs across runs,
tasks and worktrees, and any changed input is a miss."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import al_action_cache as Cache
import al_evidence_storage
import al_store
from evidence_helpers import PY, entries, ok_cmd

SPEC = "Verify the candidate task preserves repository state while recording evidence for its implementation."


def _spec(argv=None, scope=("svc",), cwd=".", env=()):
    return {"area": "svc", "kind": "test", "argv": list(argv or ok_cmd("3 passed in 0.1s")), "cwd": cwd,
            "scope": list(scope), "sources": [], "env": list(env),
            "storage": al_evidence_storage.default_declaration()}


def _key(spec=None):
    plan, reason = Cache.evidence_plan({}, spec or _spec())
    assert plan is not None, reason
    return plan.digest


def counting(marker, text="3 passed in 0.1s", code=0):
    """A check that appends to a marker outside the repository each time it executes."""
    return [PY, "-c", f"open({str(marker)!r}, 'a').write('x'); print({text!r}); raise SystemExit({code})"]


# One test per key dimension -------------------------------------------------------------

def test_same_inputs_give_the_same_key(repo):
    assert _key() == _key()


def test_argv_is_part_of_the_key(repo):
    assert _key(_spec(ok_cmd("3 passed"))) != _key(_spec(ok_cmd("4 passed")))


def test_cwd_is_part_of_the_key(repo):
    assert _key(_spec(cwd=".")) != _key(_spec(cwd="svc"))


def test_declared_env_is_part_of_the_key(repo, monkeypatch):
    monkeypatch.setenv("AL_CACHE_TEST_MODE", "one")
    first = _key(_spec(env=["AL_CACHE_TEST_MODE"]))
    monkeypatch.setenv("AL_CACHE_TEST_MODE", "two")
    assert _key(_spec(env=["AL_CACHE_TEST_MODE"])) != first
    # An undeclared variable is not part of the key (a non-hermetic check must say so).
    monkeypatch.setenv("AL_CACHE_UNDECLARED", "x")
    assert _key(_spec(env=["AL_CACHE_TEST_MODE"])) == _key(_spec(env=["AL_CACHE_TEST_MODE"]))


def test_tool_content_is_part_of_the_key(repo, tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    tools.mkdir()
    tool = tools / "fixture-check"
    tool.write_text("#!/bin/sh\necho 1 passed\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    first = _key(_spec(["fixture-check"]))
    tool.write_text("#!/bin/sh\necho 2 passed\n")
    assert _key(_spec(["fixture-check"])) != first


def test_lockfile_is_part_of_the_key(repo):
    (repo / "uv.lock").write_text("version = 1\n")
    first = _key()
    (repo / "uv.lock").write_text("version = 2\n")
    assert _key() != first


def test_input_tree_is_part_of_the_key(repo):
    first = _key()
    (repo / "svc/app.py").write_text("x = 2\n")
    second = _key()
    assert second != first
    (repo / "svc/new_module.py").write_text("y = 1\n")  # untracked, not ignored: an input too
    assert _key() != second
    (repo / "svc/new_module.py").unlink()
    (repo / "svc/app.py").write_text("x = 1\n")
    assert _key() == first  # content addressed: back to the same bytes, back to the same key


def test_ignored_files_and_other_packages_are_not_inputs(repo):
    (repo / "web/package.json").write_text("{}\n")
    spec = _spec(scope=("web/app.js",), cwd="web")
    first = _key(spec)
    (repo / "svc/app.py").write_text("x = 3\n")  # another package
    (repo / "web/__pycache__").mkdir()
    (repo / "web/__pycache__/x.pyc").write_bytes(b"\0")  # ignored
    assert _key(spec) == first
    (repo / "web/build.js").write_text("// generated\n")  # the check's own package
    assert _key(spec) != first


def test_tracked_inputs_in_snapshot_excluded_directories_are_not_cached(cli, repo, state_file, tmp_path):
    """The snapshot leaves out dist/, build/, .venv/ ... even when tracked; until it includes
    them, a check reading such a tracked input must re-run rather than reuse a stale result."""
    (repo / "dist").mkdir()
    (repo / "dist/x.js").write_text("export const v = 1\n")
    subprocess.run(["git", "add", "dist/x.js"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "dist"], cwd=repo,
                   check=True, capture_output=True)
    marker = tmp_path / "runs"
    cli("start", "--spec", SPEC)
    code, out, _ = cli("evidence", "run", "--area", "dist", "--scope", "dist", "--", *counting(marker))
    assert code == 0 and "not cached: tracked inputs under dist" in out
    (repo / "dist/x.js").write_text("export const v = 2\n")
    cli("--session", "al-other", "start", "--spec", SPEC)
    code, out, _ = cli("--session", "al-other", "evidence", "run", "--area", "dist", "--scope", "dist",
                       "--", *counting(marker))
    assert code == 0 and "reused from" not in out and marker.read_text() == "xx"
    assert not (Path(Cache.store_root()) / "actions").exists()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    with pytest.raises(Cache.Uncacheable, match="tracked inputs under dist"):
        Cache.commit_tree(head, ["."])  # base gates: the base commit's tracked dist/ too


def test_inputs_leading_outside_the_repository_are_not_cached(repo, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("z = 1\n")
    (repo / "svc/linked.py").symlink_to(outside)
    plan, reason = Cache.evidence_plan({}, _spec())
    assert plan is None and "outside the repository" in reason


def test_base_commit_and_identical_worktree_share_the_input_identity(repo):
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert Cache.commit_tree(head, ["svc", "web"]) == Cache.input_tree({}, ["svc", "web"])
    assert Cache.commit_tree(head, ["."]) == Cache.input_tree({}, ["."])
    (repo / "svc/app.py").write_text("x = 9\n")
    assert Cache.commit_tree(head, ["svc"]) != Cache.input_tree({}, ["svc"])


# Store and reuse through the CLI --------------------------------------------------------

def test_second_session_reuses_the_result_with_its_origin(cli, repo, state_file, tmp_path):
    marker = tmp_path / "runs"
    cli("start", "--spec", SPEC)
    assert cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", *counting(marker))[0] == 0
    first = entries(state_file)[0]
    cli("--session", "al-other", "start", "--spec", SPEC)
    code, out, _ = cli("--session", "al-other", "evidence", "run", "--area", "svc", "--scope", "svc",
                       "--", *counting(marker))
    assert code == 0, out
    assert marker.read_text() == "x"  # not executed again
    reused = entries(lambda: state_file("al-other"))[0]
    assert reused["status"] == "PASS" and reused["counts"] == first["counts"]
    assert reused["action_cache"]["origin"]["entry"] == first["id"]
    assert reused["action_cache"]["origin"]["run"] == first["run"]
    assert f"reused from run {first['run']} ({first['id']})" in out
    assert Path(reused["log"]).read_bytes() == Path(first["log"]).read_bytes()
    assert "reused from run" in cli("--session", "al-other", "evidence", "status")[1]
    # It is ordinary evidence of this run: VALID now, STALE when its scope changes.
    assert " VALID " in cli("--session", "al-other", "evidence", "status")[1]


def test_a_failure_is_reused_but_infra_is_not_stored(cli, repo, state_file, tmp_path):
    marker = tmp_path / "runs"
    cli("start", "--spec", SPEC)
    assert cli("evidence", "run", "--area", "bad", "--scope", "svc", "--", *counting(marker, "1 failed", 1))[0] == 1
    assert cli("evidence", "run", "--area", "gone", "--scope", "svc", "--", "al-no-such-program-26")[0] == 1
    cli("--session", "al-other", "start", "--spec", SPEC)
    code, out, _ = cli("--session", "al-other", "evidence", "run", "--area", "bad", "--scope", "svc",
                       "--", *counting(marker, "1 failed", 1))
    assert code == 1 and marker.read_text() == "x"
    assert entries(lambda: state_file("al-other"))[0]["status"] == "FAIL"
    code, out, _ = cli("--session", "al-other", "evidence", "run", "--area", "gone", "--scope", "svc",
                       "--", "al-no-such-program-26")
    assert code == 1 and "reused from" not in out  # INFRA is executed again


def test_never_store_infra_torn_or_timeout(repo, tmp_path):
    log = tmp_path / "log"
    log.write_text("x\n")
    plan, _ = Cache.evidence_plan({}, _spec())
    base = {"id": "E1", "log_sha256": Cache._log_sha(log), "counts": {}, "exit": 0, "note": ""}
    for status, extra in (("INFRA", {"note": "timed out"}), ("TORN", {}), ("RUNNING", {}),
                          ("PASS", {"interrupted": True}), ("PASS", {"cleanup": {"unresolved": True}})):
        assert not Cache.save(plan, {**base, "status": status, **extra}, log, {"run": "r"})
    assert Cache.lookup(plan) is None
    assert Cache.save(plan, {**base, "status": "PASS"}, log, {"run": "r", "entry": "E1"})
    assert Cache.lookup(plan)["status"] == "PASS"


def test_changed_input_tree_misses_and_runs_again(cli, repo, state_file, tmp_path):
    marker = tmp_path / "runs"
    cli("start", "--spec", SPEC)
    assert cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", *counting(marker))[0] == 0
    (repo / "svc/app.py").write_text("x = 2\n")
    cli("--session", "al-other", "start", "--spec", SPEC)
    code, out, _ = cli("--session", "al-other", "evidence", "run", "--area", "svc", "--scope", "svc",
                       "--", *counting(marker))
    assert code == 0 and "reused from" not in out and marker.read_text() == "xx"


@pytest.mark.parametrize("opt_out", ["--no-cache", "--non-hermetic", "env"])
def test_opt_outs_execute_and_record_nothing(cli, repo, state_file, tmp_path, monkeypatch, opt_out):
    marker = tmp_path / "runs"
    flags = [] if opt_out == "env" else [opt_out]
    if opt_out == "env":
        monkeypatch.setenv("AL_ACTION_CACHE", "0")
    cli("start", "--spec", SPEC)
    assert cli("evidence", "run", "--area", "svc", "--scope", "svc", *flags, "--", *counting(marker))[0] == 0
    cli("--session", "al-other", "start", "--spec", SPEC)
    code, out, _ = cli("--session", "al-other", "evidence", "run", "--area", "svc", "--scope", "svc", *flags,
                       "--", *counting(marker))
    assert code == 0 and "reused from" not in out and marker.read_text() == "xx"
    assert not (Path(Cache.store_root()) / "actions").exists()


def test_planned_non_hermetic_check_is_never_cached(cli, repo, state_file, tmp_path):
    marker = tmp_path / "runs"
    for session in ("al-test", "al-other"):
        cli("--session", session, "start", "--spec", SPEC)
        cli("--session", session, "evidence", "plan", "--area", "svc", "--scope", "svc", "--non-hermetic",
            "--", *counting(marker))
        assert cli("--session", session, "evidence", "run", "--area", "svc")[0] == 0
    assert marker.read_text() == "xx"


def test_fresh_executes_and_replaces_the_record(cli, repo, state_file, tmp_path):
    marker = tmp_path / "runs"
    cli("start", "--spec", SPEC)
    assert cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", *counting(marker))[0] == 0
    cli("--session", "al-other", "start", "--spec", SPEC)
    assert cli("--session", "al-other", "evidence", "run", "--area", "svc", "--scope", "svc", "--fresh",
               "--", *counting(marker))[0] == 0
    assert marker.read_text() == "xx"
    other = entries(lambda: state_file("al-other"))[0]
    plan, _ = Cache.evidence_plan({}, _spec(counting(marker)))
    assert Cache.lookup(plan)["origin"]["entry"] == other["id"] and Cache.lookup(plan)["origin"]["session"] == "al-other"


def test_gate_opt_outs():
    gate = {"execution_kind": "local", "argv": ["true"], "cwd": ".", "scope": ["svc"], "env_names": [],
            "category": "boundary"}
    assert Cache.gate_plan({}, {"gates": []}, {**gate, "hermetic": False}, "base")[0] is None
    assert Cache.gate_plan({}, {"gates": []}, {**gate, "execution_kind": "remote"}, "base")[0] is None
    assert Cache.gate_plan({}, {"gates": []}, gate, "base", no_cache=True)[0] is None


def test_a_hit_from_another_worktree(cli, repo, state_file, tmp_path, monkeypatch, isolated_runtime):
    marker = tmp_path / "runs"
    cli("start", "--spec", SPEC)
    assert cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", *counting(marker))[0] == 0
    first = entries(state_file)[0]
    other = tmp_path / "second-worktree"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(other)], cwd=repo, check=True, capture_output=True)
    runtime = other / isolated_runtime.relative_to(repo)  # the host's own runtime layout
    monkeypatch.chdir(other)
    monkeypatch.setattr(al_store, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(al_store, "CURRENT", runtime / "current")
    monkeypatch.setattr(al_store, "REPO_ROOT", other)
    for name, value in list(os.environ.items()):  # the host's project-directory variable, if any
        if value == str(repo):
            monkeypatch.setenv(name, str(other))
    monkeypatch.setenv("AL_RUNTIME_DIR", str(runtime))
    assert Cache.store_root() == (repo / ".git" / "al-action-cache").resolve()
    plan, reason = Cache.evidence_plan({}, _spec(counting(marker)))
    assert plan is not None, reason
    hit = Cache.lookup(plan)
    assert hit and hit["origin"]["worktree"] == str(repo) and hit["origin"]["entry"] == first["id"]
    # The full command in the second worktree: a hit, recorded with its origin, nothing executed.
    assert cli("--session", "al-worktree", "start", "--spec", SPEC)[0] == 0
    code, out, err = cli("--session", "al-worktree", "evidence", "run", "--area", "svc", "--scope", "svc",
                         "--", *counting(marker))
    assert code == 0, out + err
    assert f"reused from run {first['run']} ({first['id']})" in out
    assert marker.read_text() == "x"
    reused = json.loads((runtime / "al-worktree.json").read_text())["evidence"]["entries"][0]
    assert reused["action_cache"]["origin"]["worktree"] == str(repo)


# Gates -------------------------------------------------------------------------------

def _gate_session(cli, repo, tmp_path, argv, session=None):
    from batch13_helpers import manifest, start
    if session:
        from hook_regression_helpers import invoke
        invoke(cli, "--session", session, "start", "--spec", "Materialize and validate this disposable gate boundary safely.")
    else:
        start(cli, repo)
    manifest(cli, tmp_path, argv)


def _boundary(cli, session=None):
    import al_gates as Gates
    state = cli.state(session)
    m = Gates.manifest(state)
    return state, m, next(g for g in m["gates"] if g["id"] == "boundary")


def test_base_gate_runs_once_for_every_task_and_the_candidate_reuses_it(cli, repo, tmp_path):
    from hook_regression_helpers import invoke
    marker = tmp_path / "runs"
    _gate_session(cli, repo, tmp_path, counting(marker, "gate ok"))
    invoke(cli, "gates", "run", "--stage", "base", "--id", "boundary")
    assert marker.read_text() == "x"
    _gate_session(cli, repo, tmp_path, counting(marker, "gate ok"), session="al-other")
    _, out, _ = invoke(cli, "gates", "run", "--stage", "base", "--id", "boundary")
    assert "reused from run" in out and marker.read_text() == "x"
    # The unchanged candidate has the base's inputs: the same action, reused again.
    _, out, _ = invoke(cli, "gates", "run", "--stage", "candidate", "--id", "boundary")
    assert "reused from run" in out and marker.read_text() == "x"
    import al_gates as Gates
    import al_scope as S
    state, m, gate = _boundary(cli, "al-other")
    assert Gates.gate_result(state, m, gate, "candidate", S.capture_candidate(state)["fingerprint"]) == "valid"
    assert Gates.gate_result(state, m, gate, "base", m["base_oid"]) == "valid"
    reused = [e for e in state["evidence"]["entries"] if e.get("action_cache")]
    assert {e["area"] for e in reused} == {"gate-base-boundary", "gate-candidate-boundary"}


def test_a_failing_base_gate_is_reused_and_an_explicit_retry_executes(cli, repo, tmp_path):
    from hook_regression_helpers import invoke
    marker = tmp_path / "runs"
    _gate_session(cli, repo, tmp_path, counting(marker, "violation", 1))
    invoke(cli, "gates", "run", "--stage", "base", "--id", "boundary", ok=False)
    _gate_session(cli, repo, tmp_path, counting(marker, "violation", 1), session="al-other")
    _, out, _ = invoke(cli, "gates", "run", "--stage", "base", "--id", "boundary", ok=False)
    assert "reused from run" in out and marker.read_text() == "x"
    state, _, _ = _boundary(cli, "al-other")
    assert state["evidence"]["entries"][-1]["status"] == "FAIL"
    invoke(cli, "gates", "run", "--stage", "base", "--id", "boundary", "--retry-failed", ok=False)
    assert marker.read_text() == "xx"


def test_gates_no_cache_and_non_hermetic_gates_execute(cli, repo, tmp_path):
    from hook_regression_helpers import invoke
    marker = tmp_path / "runs"
    _gate_session(cli, repo, tmp_path, counting(marker, "gate ok"))
    invoke(cli, "gates", "run", "--stage", "base", "--id", "boundary", "--no-cache")
    _gate_session(cli, repo, tmp_path, counting(marker, "gate ok"), session="al-other")
    invoke(cli, "gates", "run", "--stage", "base", "--id", "boundary")
    assert marker.read_text() == "xx"  # --no-cache recorded nothing
    state, m, gate = _boundary(cli, "al-other")
    assert Cache.gate_plan(state, m, gate, "base")[0] is not None
    assert Cache.gate_plan(state, m, {**gate, "hermetic": False}, "base")[0] is None


def test_the_review_packet_lists_reused_results_with_their_origin(cli, repo, state_file, tmp_path):
    marker = tmp_path / "runs"
    cli("start", "--spec", SPEC)
    cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", *counting(marker))
    original = json.loads(state_file().read_text())
    first = original["evidence"]["entries"][0]
    cli("--session", "al-other", "start", "--spec", SPEC)
    cli("--session", "al-other", "evidence", "run", "--area", "svc", "--scope", "svc", "--", *counting(marker))
    state = json.loads(state_file("al-other").read_text())
    packet = Cache.annotate_packet(state, {})
    assert [(row["area"], row["reused_from"]["run"], row["reused_from"]["entry"]) for row in packet["reused_results"]] \
        == [("svc", first["run"], first["id"])]
    assert Cache.annotate_packet(original, {}) == {}  # nothing reused, nothing added
