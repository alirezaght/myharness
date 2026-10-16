import re
import shutil
import subprocess
from pathlib import Path

import pytest

from harness.cli import main
from harness.controller import HarnessController
from harness.db import connect
from harness.spec_record import parse_spec
from tests.complete_task import settle
from tests.sample_repo import make_sample_repo, tree_snapshot

OWN_DOCS = Path(__file__).resolve().parents[1] / "docs"
OWN_SNAPSHOT = tree_snapshot(OWN_DOCS)
TASK_ID = re.compile(r"task (t-[0-9a-f]{8}) created")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


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


def _task(run, repo):
    task = TASK_ID.search(_ok(run, "start", "--complexity", "trivial", "csv", "export")).group(1)
    _ok(run, "investigate", task, "--repo", str(repo))
    return task


def _to_approval(run, db_path, task):
    conn = connect(db_path)
    try:
        settle(HarnessController(conn), task)
    finally:
        conn.close()
    _ok(run, "phase", task, "next")


def _state(db_path):
    conn = connect(db_path)
    try:
        tables = ("approvals", "spec_versions", "events", "checkpoints")
        return tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables)
    finally:
        conn.close()


def _approval_line(run, task):
    return [line for line in _ok(run, "show", task).splitlines() if line.startswith("approval:")]


def _head(repo):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


@needs_git
def test_approve_seals_records_and_show_reports_it(run, sample_repo, db_path):
    task = _task(run, sample_repo)
    specs = sample_repo / "docs" / "specs" / task
    assert _approval_line(run, task) == ["approval: none"]
    lines = _ok(run, "show", task).splitlines()
    assert lines[3] == "approval: none" and lines[2].startswith("created: ")

    before, snapshot = _state(db_path), tree_snapshot(sample_repo)
    code, out, err = run("approve", task)
    assert (code, out) == (1, "") and f"is in phase investigation; run: harness phase {task}" in err
    assert (_state(db_path), tree_snapshot(sample_repo)) == (before, snapshot)

    _to_approval(run, db_path, task)
    out = _ok(run, "approve", task).splitlines()
    v1 = parse_spec(specs / "v1.md")
    assert out == [
        f"approved v1 -> {specs / 'v1.md'} (revision {_head(sample_repo)}, {v1.working_tree})",
        f"approval #1 recorded: spec v1 {v1.content_hash}"
        " (implementation handoff authorized; AL launch is Phase 3)",
    ]
    assert _approval_line(run, task) == [f"approval: spec v1 {v1.content_hash} (current)"]

    out = _ok(run, "approve", task).splitlines()
    v2 = parse_spec(specs / "v2.md")
    assert out[1:] == [
        "v1 superseded",
        f"approval #2 recorded: spec v2 {v2.content_hash}"
        " (implementation handoff authorized; AL launch is Phase 3)",
    ]
    assert _approval_line(run, task) == [f"approval: spec v2 {v2.content_hash} (current)"]

    _ok(run, "spec", "approve", task)
    assert _approval_line(run, task) == [
        f"approval: spec v2 {v2.content_hash} (stale: spec v2 was superseded by v3;"
        f" run: harness approve {task} again)"
    ]
    assert _state(db_path)[0] == 2
    assert tree_snapshot(OWN_DOCS) == OWN_SNAPSHOT


@needs_git
def test_show_reports_a_stale_approval_after_phase_back(run, sample_repo, db_path):
    task = _task(run, sample_repo)
    _to_approval(run, db_path, task)
    _ok(run, "approve", task)
    _ok(run, "phase", task, "back", "test_plan", "--reason", "fixture")
    (line,) = _approval_line(run, task)
    assert line.endswith(
        "(stale: the task is in phase test_plan, not approval;"
        f" run: harness approve {task} again once it is back in approval)"
    )


def test_no_commit_means_no_approval_and_nothing_written(run, tmp_path, db_path):
    repo = make_sample_repo(tmp_path / "plain", with_git=False)
    task = _task(run, repo)
    _to_approval(run, db_path, task)
    before, snapshot = _state(db_path), tree_snapshot(repo)
    code, out, err = run("approve", task)
    assert (code, out) == (1, "") and "the spec cannot be tied to a commit" in err
    assert (_state(db_path), tree_snapshot(repo)) == (before, snapshot)
    assert _approval_line(run, task) == ["approval: none"]


def test_unknown_task_missing_db_and_missing_id(tmp_path, capsys):
    db = tmp_path / "absent" / "state.db"
    assert main(["--db", str(db), "approve", "t-00000000"]) == 1
    assert "harness approve: " in capsys.readouterr().err
    assert not db.parent.exists()
    with pytest.raises(SystemExit) as caught:
        main(["--db", str(db), "approve"])
    assert caught.value.code == 2
    assert "required: task_id" in capsys.readouterr().err
    assert not db.parent.exists()


def test_unknown_task_in_an_existing_db(run, db_path):
    _ok(run, "start", "fixture", "request")
    code, out, err = run("approve", "t-00000000")
    assert (code, out) == (1, "") and "t-00000000" in err
    assert _state(db_path)[0] == 0
