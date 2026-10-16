import json
import re

import pytest

from harness.cli import main
from harness.controller import HarnessController
from harness.db import connect
from harness.review_report import Check
from tests.complete_task import settle

TASK_ID = re.compile(r"task (t-[0-9a-f]{8}) created")


@pytest.fixture
def run(db_path, capsys):
    def _run(*argv: str) -> tuple[int, str, str]:
        code = main(["--db", str(db_path), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


def _final_review(run) -> str:
    code, out, _ = run("start", "--complexity", "trivial", "fixture", "request")
    assert code == 0
    task_id = TASK_ID.search(out).group(1)
    for _ in range(4):
        assert run("phase", task_id, "next")[0] == 0
    return task_id


def _settle(db_path, task_id: str) -> None:
    conn = connect(db_path)
    try:
        settle(HarnessController(conn), task_id)
    finally:
        conn.close()


def _counts(db_path) -> tuple[int, ...]:
    conn = connect(db_path)
    try:
        tables = ("events", "checkpoints", "phase_transitions")
        return tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables)
    finally:
        conn.close()


def test_phase_next_refuses_with_hint_then_passes_after_settle(run, db_path):
    task_id = _final_review(run)
    before = _counts(db_path)
    code, out, err = run("phase", task_id, "next")
    assert (code, out) == (1, "")
    assert err.startswith(f"harness phase: task {task_id} cannot enter approval:")
    assert f'    → harness phase {task_id} back interview --reason "<why>"' in err
    assert "blocking: yes" in err
    assert _counts(db_path) == before
    _settle(db_path, task_id)
    code, out, err = run("phase", task_id, "next")
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        f"task {task_id} · phase approval · complexity trivial (developer)",
        "final design review passed (checkpoint recorded)",
    ]


def test_review_is_read_only_and_exits_by_blocking(run, db_path):
    task_id = _final_review(run)
    before = _counts(db_path)
    code, out, _ = run("review", task_id)
    assert code == 1
    assert out.startswith("final design review (SPEC §3 step 6):")
    assert "blocking: yes" in out
    code, out, _ = run("review", task_id, "--json")
    data = json.loads(out)
    assert code == 1
    assert sorted(data) == ["blocking", "items", "route_back", "signals", "state_hash"]
    assert [item["check"] for item in data["items"]] == [str(c) for c in Check]
    assert (data["blocking"], data["route_back"]) == (True, "interview")
    assert _counts(db_path) == before
    _settle(db_path, task_id)
    settled = _counts(db_path)
    code, out, _ = run("review", task_id)
    assert (code, out.splitlines()[-1]) == (0, "blocking: no")
    assert _counts(db_path) == settled


def test_review_unknown_task_and_missing_db(run, db_path, tmp_path):
    _final_review(run)
    code, _, err = run("review", "t-00000000")
    assert code == 1 and "t-00000000" in err
    missing = tmp_path / "nowhere" / "state.db"
    assert main(["--db", str(missing), "review", "t-00000000"]) == 1
    assert not missing.exists()
