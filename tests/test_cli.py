import re
import subprocess
import sys

import pytest

from harness.classification import INTERVIEW_TOPICS
from harness.cli import main

TASK_ID = re.compile(r"task (t-[0-9a-f]{8}) created")


@pytest.fixture
def run(db_path, capsys):
    """Invoke main() against the temp DB and return (exit code, stdout, stderr)."""

    def _run(*argv: str) -> tuple[int, str, str]:
        code = main(["--db", str(db_path), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


@pytest.fixture
def task_id(run):
    code, out, _ = run("start", "fix", "the", "login", "crash")
    assert code == 0
    match = TASK_ID.search(out)
    assert match, out
    return match.group(1)


def test_start_prints_id_phase_and_override_hint(run):
    code, out, err = run("start", "fix the login crash")
    assert code == 0
    assert err == ""
    first, second = out.splitlines()
    assert TASK_ID.match(first)
    assert "phase investigation" in first
    assert "complexity simple (proposed)" in first
    assert second.startswith("override: harness classify t-")


def test_start_with_explicit_complexity_has_no_override_hint(run):
    code, out, _ = run("start", "--complexity", "complex", "rework things")
    assert code == 0
    assert "complexity complex (developer)" in out
    assert "override:" not in out


def test_phase_next_steps_through_every_phase_then_rejects(run, task_id):
    for expected in ["interview", "design", "test_plan", "final_review", "approval"]:
        code, out, _ = run("phase", task_id, "next")
        assert code == 0
        assert f"phase {expected}" in out
    code, out, err = run("phase", task_id, "next")
    assert code == 1
    assert out == ""
    assert err.startswith("harness phase: ")


def test_phase_back_requires_reason_and_a_strictly_earlier_phase(run, task_id):
    for _ in range(3):
        run("phase", task_id, "next")  # now in test_plan

    code, _, err = run("phase", task_id, "back", "design")
    assert code == 1
    assert "reason" in err

    code, _, err = run("phase", task_id, "back", "test_plan", "--reason", "same phase")
    assert code == 1
    assert err.startswith("harness phase: ")

    code, _, err = run("phase", task_id, "back", "final_review", "--reason", "later phase")
    assert code == 1

    code, out, _ = run("phase", task_id, "back", "design", "--reason", "scope changed")
    assert code == 0
    assert "phase design" in out


def test_phase_back_to_unknown_phase_is_a_usage_error(run, task_id, capsys):
    with pytest.raises(SystemExit) as exc:
        run("phase", task_id, "back", "bogus", "--reason", "x")
    assert exc.value.code == 2


def test_rejected_transition_leaves_history_unchanged(run, task_id):
    _, before, _ = run("show", task_id)
    run("phase", task_id, "back", "investigation", "--reason", "")
    _, after, _ = run("show", task_id)
    assert before == after


def test_classify_flips_source_to_developer(run, task_id):
    code, out, _ = run("classify", task_id, "trivial")
    assert code == 0
    assert "complexity trivial (developer)" in out
    _, shown, _ = run("show", task_id)
    assert "complexity trivial (developer)" in shown


def test_show_prints_request_topics_and_history(run, task_id):
    run("phase", task_id, "next")
    run("phase", task_id, "back", "investigation", "--reason", "missed a module")
    code, out, _ = run("show", task_id)
    assert code == 0
    assert "request: fix the login crash" in out
    topics_line = next(line for line in out.splitlines() if line.startswith("interview topics:"))
    topics = topics_line.removeprefix("interview topics: ").split(", ")
    assert topics and set(topics) < set(INTERVIEW_TOPICS)
    history = out.split("history:\n", 1)[1].splitlines()
    assert len(history) == 3
    assert "- -> investigation" in history[0]
    assert "investigation -> interview" in history[1]
    assert "interview -> investigation (missed a module)" in history[2]


def test_state_persists_across_invocations(run, task_id):
    run("phase", task_id, "next")
    _, out, _ = run("show", task_id)
    assert "phase interview" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["phase", "t-deadbeef", "next"],
        ["classify", "t-deadbeef", "simple"],
        ["show", "t-deadbeef"],
    ],
)
def test_unknown_task_exits_1_on_existing_db(run, task_id, argv):
    code, out, err = run(*argv)
    assert code == 1
    assert out == ""
    assert "t-deadbeef" in err


@pytest.mark.parametrize("request_args", [[], [""], ["   "]])
def test_start_rejects_empty_request_without_creating_state(run, db_path, request_args):
    code, _, err = run("start", *request_args)
    assert code == 1
    assert "request" in err
    assert not db_path.exists()


@pytest.mark.parametrize(
    "argv",
    [["phase", "t-deadbeef", "next"], ["classify", "t-deadbeef", "simple"], ["show", "t-deadbeef"]],
)
def test_non_start_commands_on_missing_db_exit_1_and_create_nothing(run, db_path, argv):
    code, _, err = run(*argv)
    assert code == 1
    assert "t-deadbeef" in err
    assert not db_path.exists()
    assert not db_path.parent.exists()


def test_db_env_var_is_used_when_flag_is_absent(tmp_path, monkeypatch, capsys):
    target = tmp_path / "envdb" / "state.db"
    monkeypatch.setenv("HARNESS_DB", str(target))
    assert main(["start", "tidy up"]) == 0
    assert target.exists()


def test_import_version_and_help_leave_home_empty(isolated_home, capsys):
    assert not isolated_home.exists()
    for flag in ("--version", "--help"):
        with pytest.raises(SystemExit) as exc:
            main([flag])
        assert exc.value.code == 0
    capsys.readouterr()
    assert not isolated_home.exists()


def test_import_alone_creates_no_files(tmp_path):
    home = tmp_path / "fresh-home"
    result = subprocess.run(
        [sys.executable, "-c", "import harness.cli, harness.controller"],
        env={"HARNESS_HOME": str(home), "PATH": ""},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not home.exists()


def test_start_without_db_flag_creates_state_under_harness_home(isolated_home, capsys):
    assert main(["start", "add an endpoint"]) == 0
    capsys.readouterr()
    assert list(isolated_home.glob("projects/*/state.db"))
