import json
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from harness.cli import main
from harness.db import connect
from harness.state import TaskWorkspace
from harness.state_models import QuestionStatus

REPO_ROOT = Path(__file__).resolve().parents[1]
TASK_ID = re.compile(r"task (t-[0-9a-f]{8}) created")
QUESTION = re.compile(r"^Q(\d+) \[(\w+)\]( \(decision\))? .*$", re.M)
TIMEOUT = 30

CHILD = """
import sys, time
from harness.cli import main
from harness.controller import HarnessController
from harness.db import connect
from harness.interview import InterviewEngine

db, task = sys.argv[1], sys.argv[2]
assert main(["--db", db, "interview", "next", task]) == 0
sys.stdout.flush()
conn = connect(db)
controller = HarnessController(conn)
qid = InterviewEngine(controller, task).upcoming().id
ws = controller.workspace(task)
conn.execute("BEGIN IMMEDIATE")
ws.answer(qid, "half-typed answer")
ws.record_event("question.answered", {"id": qid})
ws.checkpoint("interview", {"next_question_id": None})
print("READY", flush=True)
time.sleep(60)
"""


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


def _task(run, repo, *words):
    out = _ok(run, "start", "--complexity", "trivial", *(words or ("add", "csv", "export")))
    task = TASK_ID.search(out).group(1)
    _ok(run, "investigate", task, "--repo", str(repo))
    return task


def _counts(db_path):
    conn = connect(db_path)
    try:
        tables = ("tasks", "questions", "proposals", "events", "checkpoints", "approvals")
        return tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables)
    finally:
        conn.close()


def _question(db_path, task, qid):
    conn = connect(db_path)
    try:
        return TaskWorkspace(conn, task).question(qid)
    finally:
        conn.close()


def _read_until_ready(proc) -> list[str]:
    lines: queue.Queue[str | None] = queue.Queue()

    def pump():
        for line in proc.stdout:
            lines.put(line.rstrip("\n"))
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    seen = []
    while (line := lines.get(timeout=TIMEOUT)) != "READY":
        assert line is not None, f"child exited early: {seen}"
        seen.append(line)
    return seen


def test_killed_mid_interview_resume_continues_from_the_same_question(run, db_path, sample_repo):
    task = _task(run, sample_repo)
    _ok(run, "phase", task, "next")
    before = _counts(db_path)
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(db_path), task],
        stdout=subprocess.PIPE,
        text=True,
        cwd=REPO_ROOT,
    )
    try:
        asked = QUESTION.search("\n".join(_read_until_ready(proc))).group(0)
        proc.kill()
        proc.wait(timeout=TIMEOUT)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=TIMEOUT)
        proc.stdout.close()
    qid = int(QUESTION.search(asked).group(1))
    after_kill = _counts(db_path)
    assert after_kill[3:5] == (before[3] + 1, before[4] + 1)
    assert after_kill[0] == before[0] and after_kill[2] == before[2]

    out = _ok(run, "resume", task)
    assert QUESTION.search(out).group(0) == asked
    assert out.splitlines()[-1] == f"next: harness interview next {task}"
    assert out.splitlines()[2].startswith("checkpoint: interview ")
    assert _ok(run, "resume", task) == out
    assert _counts(db_path) == after_kill
    question = _question(db_path, task, qid)
    assert (question.status, question.answer) == (QuestionStatus.OPEN, None)

    assert QUESTION.search(_ok(run, "interview", "next", task)).group(0) == asked


def test_resume_shows_state_as_text_and_json_and_writes_nothing(run, db_path, sample_repo):
    task = _task(run, sample_repo)
    before = _counts(db_path)
    lines = _ok(run, "resume", task).splitlines()
    assert lines[0] == f"task {task} · phase investigation · complexity trivial (developer)"
    assert lines[1] == "request: add csv export"
    assert lines[2].startswith("checkpoint: investigation ")
    assert lines[3:] == [f"next: harness phase {task} next"]
    data = json.loads(_ok(run, "resume", task, "--json"))
    assert (data["task_id"], data["phase"], data["next"]) == (
        task,
        "investigation",
        f"harness phase {task} next",
    )
    assert data["pending_question"] is None and data["resumable"] is True
    assert data["checkpoint"]["label"] == "investigation"
    assert _counts(db_path) == before


def test_resume_lists_resumable_tasks(run, db_path, sample_repo):
    first = _task(run, sample_repo, "first", "request")
    second = _task(run, sample_repo, "second", "request")
    _ok(run, "phase", second, "next")
    before = _counts(db_path)
    out = _ok(run, "resume")
    blocks = {line.split(" · ")[0]: line for line in out.splitlines() if line.startswith("t-")}
    assert set(blocks) == {first, second}
    assert f"  next: harness interview next {second}" in out.splitlines()
    assert "  second request" in out.splitlines()
    assert len(out.splitlines()) == 6
    data = json.loads(_ok(run, "resume", "--json"))
    assert {d["task_id"] for d in data} == {first, second}
    assert _counts(db_path) == before


def test_resume_without_state_or_with_unknown_task(run, db_path, capsys):
    assert _ok(run, "resume") == "no tasks to resume\n"
    assert json.loads(_ok(run, "resume", "--json")) == []
    code, out, err = run("resume", "t-00000000")
    assert (code, out) == (1, "") and err == "harness resume: unknown task: t-00000000\n"
    assert not db_path.parent.exists()
    _ok(run, "start", "fixture", "request")
    code, _, err = run("resume", "t-00000000")
    assert code == 1 and "unknown task: t-00000000" in err
