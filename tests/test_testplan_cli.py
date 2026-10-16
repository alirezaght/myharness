import json
import re

import pytest

from harness.cli import main
from harness.controller import HarnessController
from harness.db import connect

TASK_ID = re.compile(r"task (t-[0-9a-f]{8}) created")


@pytest.fixture
def run(db_path, capsys):
    def _run(*argv: str) -> tuple[int, str, str]:
        code = main(["--db", str(db_path), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


def _ok(run, *argv):
    code, out, err = run(*argv)
    assert (code, err) == (0, ""), err
    return out


def _fails(run, *argv):
    code, out, err = run(*argv)
    assert (code, out) == (1, ""), out
    return err


def _counts(db_path, task):
    conn = connect(db_path)
    try:
        ws = HarnessController(conn).workspace(task)
        return len(ws.scenarios()), len(ws.events()), len(ws.checkpoints())
    finally:
        conn.close()


@pytest.fixture
def task(run, sample_repo):
    task = TASK_ID.search(_ok(run, "start", "--complexity", "trivial", "csv", "export")).group(1)
    _ok(run, "investigate", task, "--repo", str(sample_repo))
    _ok(run, "phase", task, "next")
    _ok(run, "interview", "ask", task, "acceptance_criteria", "When", "done?")
    _ok(run, "interview", "answer", task, "1", "rows", "export")
    _ok(run, "phase", task, "next")
    _ok(run, "phase", task, "next")
    return task


def test_scripted_planning_session(run, task, db_path):
    assert _ok(run, "testplan", "add", task, "unit", "header", "row") == "S1 stated [unit]\n"
    out = _ok(
        run, "testplan", "propose", task, "failure", "disk", "full", "--rationale", "error path",
        "--covers", "Q1", "--mock", "fs", "--mock-reason", "no real disk",
    )  # fmt: skip
    decide = f"  decide: harness testplan decide {task} 2 accept|reject|modify\n"
    assert out == "S2 pending [failure]\n" + decide
    assert "undecided scenarios: S2" in _fails(run, "testplan", "approve", task)
    assert _ok(
        run, "testplan", "decide", task, "2", "modify", "--response", "disk full aborts"
    ) == ("S2 modified\n")
    assert _ok(run, "testplan", "flag", task, "2", "--reason", "hides the writer") == (
        "S2 flagged: hides the writer\n"
    )
    review = json.loads(_ok(run, "testplan", "review", task, "--json"))
    assert [(i["ref"], i["state"]) for i in review["unresolved"]] == [
        ("S2", "mock_flagged"),
        ("plan", "not_approved"),
    ]
    assert _ok(run, "testplan", "flag", task, "2", "--clear", "--reason", "writer exists") == (
        "S2 flag cleared\n"
    )
    out = _ok(run, "testplan", "approve", task)
    assert re.fullmatch(r"approved test plan \(2 scenario\(s\), sha256:[0-9a-f]{64}\)\n", out)
    assert _ok(run, "testplan", "edit", task, "1", "--text", "quoted header") == "S1 edited\n"
    assert "plan changed_since_approval" in _ok(run, "testplan", "review", task)
    assert _ok(run, "testplan", "withdraw", task, "1") == "S1 withdrawn\n"
    shown = json.loads(_ok(run, "testplan", "show", task, "--json"))
    assert shown["approved"] is False
    assert [s["ref"] for s in shown["categories"]["failure"]] == ["S2"]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["propose", "unit", "x", "--rationale", "r", "--covers", "Q9"], "Q9 is not an answered"),
        (["add", "unit", "x", "--mock", "smtp"], "a mock needs a written justification"),
        (["add", "unit", "x", "--mock-reason", "external"], "a mock needs a written justification"),
        (["add", "unit", "x", "--covers", "X1"], "covers ref must look like"),
        (["flag", "1", "--reason", "hides"], "unknown test scenario 1"),
        (["edit", "1"], "nothing to edit"),
        (["edit", "1", "--no-mock", "--mock", "x"], "--no-mock cannot be combined"),
        (["approve"], "the test plan has no scenarios"),
    ],
)
def test_refused_commands_leave_the_db_unchanged(run, task, db_path, argv, message):
    before = _counts(db_path, task)
    action, *rest = argv
    assert message in _fails(run, "testplan", action, task, *rest)
    assert _counts(db_path, task) == before


def test_wrong_phase_hint_and_absent_db(run, task, tmp_path):
    _ok(run, "phase", task, "next")
    err = _fails(run, "testplan", "add", task, "unit", "x")
    assert (
        f'is in phase final_review; run: harness phase {task} back test_plan --reason "<why>"'
        in err
    )
    assert _ok(run, "testplan", "review", task).startswith("test plan review (0 effective")
    db = tmp_path / "absent" / "state.db"
    for argv in (["show", "t-00000000"], ["review", "t-00000000"]):
        assert main(["--db", str(db), "testplan", *argv]) == 1
    assert not db.parent.exists()


def test_help_lists_all_nine_actions(db_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--db", str(db_path), "testplan", "--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    for action in (
        "add",
        "propose",
        "decide",
        "edit",
        "withdraw",
        "flag",
        "approve",
        "show",
        "review",
    ):
        assert re.search(rf"\b{action}\b", out), action


def test_show_and_review_json_shapes(run, task):
    _ok(run, "testplan", "add", task, "unit", "header", "row", "--covers", "Q1")
    shown = json.loads(_ok(run, "testplan", "show", task, "--json"))
    assert set(shown) == {"categories", "approved", "review"}
    assert set(shown["categories"]) == {
        "unit",
        "integration",
        "e2e",
        "regression",
        "edge_case",
        "failure",
    }
    (scenario,) = shown["categories"]["unit"]
    assert scenario["ref"] == "S1" and scenario["covers"] == ["Q1"]
    assert {"mock", "mock_justification", "concern", "provenance"} <= set(scenario)
    review = json.loads(_ok(run, "testplan", "review", task, "--json"))
    assert set(review) == {
        "blocking",
        "unresolved",
        "missing_categories",
        "counts",
        "approval",
        "approved",
    }
    assert review["approved"] is False and review["approval"] is None
    assert "unit" not in review["missing_categories"]
    assert shown["review"] == review


def test_edit_no_mock_removes_the_mock_and_the_flag(run, task, db_path):
    _ok(run, "testplan", "add", task, "unit", "mailer", "--mock", "smtp", "--mock-reason", "no net")
    _ok(run, "testplan", "flag", task, "1", "--reason", "hides the mailer")
    assert _ok(run, "testplan", "edit", task, "1", "--no-mock") == "S1 edited\n"
    conn = connect(db_path)
    try:
        scenario = HarnessController(conn).workspace(task).scenario(1)
    finally:
        conn.close()
    assert (scenario.mock, scenario.mock_justification, scenario.concern) == (None, None, None)
    review = json.loads(_ok(run, "testplan", "review", task, "--json"))
    assert [i["state"] for i in review["unresolved"]] == ["not_approved"]


def test_deciding_twice_is_refused(run, task, db_path):
    _ok(run, "testplan", "propose", task, "unit", "x", "--rationale", "r", "--covers", "Q1")
    _ok(run, "testplan", "decide", task, "1", "accept")
    before = _counts(db_path, task)
    assert "already" in _fails(run, "testplan", "decide", task, "1", "reject")
    assert _counts(db_path, task) == before


def test_propose_without_covers_is_refused(run, task, db_path):
    before = _counts(db_path, task)
    try:
        code, out, _ = run("testplan", "propose", task, "unit", "x", "--rationale", "r")
    except SystemExit as exc:
        code, out = exc.code, ""
    assert code not in (0, None) and out == ""
    assert _counts(db_path, task) == before


def test_unknown_task_fails_without_creating_the_db(tmp_path):
    db = tmp_path / "absent" / "state.db"
    assert main(["--db", str(db), "testplan", "add", "t-00000000", "unit", "x"]) == 1
    assert not db.parent.exists()
