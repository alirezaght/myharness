import json
import re

import pytest

from harness.cli import main

TASK_ID = re.compile(r"task (t-[0-9a-f]{8}) created")
QUESTION = re.compile(r"^Q(\d+) \[(\w+)\]( \(decision\))? ", re.M)


@pytest.fixture
def run(db_path, capsys):
    def _run(*argv: str) -> tuple[int, str, str]:
        code = main(["--db", str(db_path), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


@pytest.fixture
def task_id(run, sample_repo):
    code, out, _ = run("start", "--complexity", "trivial", "add", "csv", "export")
    assert code == 0
    task = TASK_ID.search(out).group(1)
    assert run("investigate", task, "--repo", str(sample_repo))[0] == 0
    return task


def _next(run, task, *flags):
    code, out, err = run("interview", "next", task, *flags)
    assert (code, err) == (0, "")
    return out, [(int(m[0]), m[1], bool(m[2])) for m in QUESTION.findall(out)]


def test_guards_exit_1_without_writes(run, task_id):
    code, _, err = run("interview", "next", task_id)
    assert code == 1 and f"run: harness phase {task_id} next" in err
    assert run("interview", "checklist", task_id, "--json")[0] == 0
    assert run("interview", "next", "t-00000000")[0] == 1


def _ids(run, task):
    data = json.loads(run("interview", "checklist", task, "--json")[1])
    return {
        item["catalogue_key"] or item["ref"]: item["question_id"] for item in data["unresolved"]
    }


def _propose(run, task, qid, recommendation):
    code, out, _ = run(
        "interview", "propose", task, str(qid),
        "--title", "T", "--recommendation", recommendation, "--rationale", "because",
    )  # fmt: skip
    assert code == 0
    return int(re.match(r"P(\d+) pending", out).group(1))


def test_scripted_interview_run(run, task_id):
    assert run("phase", task_id, "next")[0] == 0
    out, [(first, _, _)] = _next(run, task_id)
    assert out.startswith(f"Q{first} [")
    assert _next(run, task_id)[1][0][0] == first

    code, _, err = run("interview", "answer", task_id, str(first), "I", "don't", "know")
    assert code == 1 and f"harness interview unknown {task_id} {first}" in err
    assert run("interview", "unknown", task_id, str(first)) == (
        0,
        f"Q{first} unresolved (developer does not know)\n",
        "",
    )
    code, out, _ = run("interview", "checklist", task_id, "--json")
    data = json.loads(out)
    assert code == 0 and data["blocking"] is True
    assert f"Q{first}" in [item["ref"] for item in data["unresolved"]]
    assert json.loads(run("interview", "summary", task_id, "--json")[1])["requirements"] == []

    ids = _ids(run, task_id)
    success, existing = ids["functional_behavior.success_path"], ids["functional_behavior.existing"]
    criteria, verify = ids["acceptance_criteria.list"], ids["acceptance_criteria.verification"]
    assert run("interview", "answer", task_id, str(success), "export", "a", "file") == (
        0,
        f"Q{success} answered\n",
        "",
    )
    accepted = _propose(run, task_id, existing, "Keep old exports")
    group_out, group = _next(run, task_id, "--group")
    assert [qid for qid, _, _ in group] == [existing] and "rationale: because" in group_out
    decided = run("interview", "decide", task_id, str(accepted), "accept")[1].splitlines()
    assert decided[0] == f"P{accepted} accepted"
    assert re.fullmatch(r"recorded DEC-0003 v1 -> .*/docs/decisions/DEC-0003-t\.md", decided[1])
    rejected = _propose(run, task_id, criteria, "Do nothing")
    assert (
        run("interview", "decide", task_id, str(rejected), "reject")[1] == f"P{rejected} rejected\n"
    )
    code, _, err = run(
        "interview", "skip", task_id, str(verify), "--source", "decision", "--ref", "DEC-0002"
    )
    assert code == 1 and "approved: DEC-0001" in err

    code, out, _ = run("interview", "checklist", task_id)
    assert code == 0 and out.rstrip().endswith("blocking: yes")
    assert f"Q{criteria} [acceptance_criteria] recommendation_rejected" in out
    assert f"Q{verify} [acceptance_criteria] not_asked" in out
    assert f"Q{first} " in out and f"Q{success} " not in out and f"Q{existing} " not in out

    requirements = json.loads(run("interview", "summary", task_id, "--json")[1])["requirements"]
    assert {(r["statement"], r["provenance"]) for r in requirements} == {
        ("export a file", "developer"),
        ("Keep old exports", "accepted_recommendation"),
    }
    code, _, err = run("interview", "answer", task_id, "999", "yes")
    assert code == 1 and err == f"harness interview: unknown question 999 for task {task_id}\n"


def test_skip_by_approved_decision_and_codebase(run, task_id):
    run("phase", task_id, "next")
    _, [(qid, _, _)] = _next(run, task_id)
    code, out, _ = run(
        "interview", "skip", task_id, str(qid), "--source", "decision", "--ref", "DEC-0001"
    )
    assert (code, out) == (0, f"Q{qid} skipped (decision DEC-0001)\n")
    _, [(other, _, _)] = _next(run, task_id)
    code, out, _ = run(
        "interview", "skip", task_id, str(other), "--source", "codebase", "--ref", "demo/export.py"
    )
    assert code == 0
    summary = json.loads(run("interview", "summary", task_id, "--json")[1])
    assert [s["skip_source"] for s in summary["answered_elsewhere"]] == ["decision", "codebase"]
    assert summary["requirements"] == []


def test_modify_requires_response(run, task_id):
    run("phase", task_id, "next")
    _, [(qid, _, _)] = _next(run, task_id)
    out = run(
        "interview", "propose", task_id, str(qid),
        "--title", "T", "--recommendation", "R", "--rationale", "W",
    )[1]  # fmt: skip
    pid = re.match(r"P(\d+)", out).group(1)
    assert run("interview", "decide", task_id, pid, "modify")[0] == 1
    assert run("interview", "decide", task_id, pid, "modify", "--response", "R2")[0] == 0
    summary = json.loads(run("interview", "summary", task_id, "--json")[1])
    assert [r["statement"] for r in summary["requirements"]] == ["R2"]


def test_next_reports_unresolved_questions_once_all_are_deferred(run, task_id):
    run("phase", task_id, "next")
    asked = 0
    while True:
        _, questions = _next(run, task_id)
        if not questions:
            break
        assert run("interview", "unknown", task_id, str(questions[0][0]))[0] == 0
        asked += 1
        assert asked < 50
    out = run("interview", "next", task_id)[1]
    assert asked > 0
    assert out == (
        f"no questions left to ask; {asked} open question(s) stay unresolved"
        f" (see: harness interview checklist {task_id})\n"
    )
    assert json.loads(run("interview", "checklist", task_id, "--json")[1])["blocking"] is True


def test_ask_decision_creates_decision_question(run, task_id):
    run("phase", task_id, "next")
    code, out, _ = run(
        "interview", "ask", task_id, "security", "Who", "may", "export?", "--decision"
    )
    assert code == 0 and out.startswith("Q")
    qid = int(re.match(r"Q(\d+) added \[security\]", out).group(1))
    seen = []
    for _ in range(50):
        next_out, questions = _next(run, task_id)
        seen.append(questions[0])
        if questions[0][0] == qid:
            break
        run("interview", "unknown", task_id, str(questions[0][0]))
    assert (qid, "security", True) in seen
    assert f"Q{qid} [security] (decision) Who may export?" in next_out
    assert f"harness interview propose {task_id} {qid}" in next_out
