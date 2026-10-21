"""P2-06: the redesign loop's engine (address_all) and its `python -m harness.redesign` entry."""

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from harness import redesign
from harness.cli import build_parser
from harness.comments import CommentService, IllegalTransition, StaleDiagramVersion
from harness.controller import HarnessController
from harness.db import connect, transaction
from harness.diagrams import DiagramService
from harness.errors import RecordNotFound
from harness.state_comments import CommentStatus
from tests.test_comments import A, B, _everything, _in, _state, _task
from tests.test_dashboard import _db_dump

OPEN, ADDRESSED, RESOLVED = CommentStatus.OPEN, CommentStatus.ADDRESSED, CommentStatus.RESOLVED
BASE_COMMANDS = [
    "approve",
    "classify",
    "dashboard",
    "decision",
    "design",
    "execution",
    "interview",
    "investigate",
    "phase",
    "resume",
    "review",
    "show",
    "spec",
    "start",
    "testplan",
]


def _events(conn: sqlite3.Connection, kind: str) -> list[dict]:
    rows = conn.execute("SELECT payload FROM events WHERE kind = ? ORDER BY id", (kind,))
    return [json.loads(row[0]) for row in rows]


def _run(capsys, *argv: str) -> tuple[int, str, str]:
    code = redesign.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _diff(before: list[str], after: list[str]) -> tuple[list[str], list[str]]:
    keep = [line for line in after if "sqlite_sequence" not in line]
    gone = [line for line in before if "sqlite_sequence" not in line]
    return sorted(set(keep) - set(before)), sorted(set(gone) - set(after))


# T1: CommentService.address_all / address(version=)


def test_address_all_moves_every_open_comment_with_the_version(controller):
    _, service = _task(controller)
    first, second = _in(service, OPEN), _in(service, OPEN)
    version, results = service.address_all([first, second, first])
    assert version == 1
    assert [(c.id, moved) for c, moved in results] == [(first, True), (second, True)]
    assert all(c.status == ADDRESSED for c, _ in results)
    payloads = _events(controller.conn, "comment.addressed")
    moved = {"from": "open", "to": "addressed", "version": 1}
    assert payloads == [{"id": first} | moved, {"id": second} | moved]
    before = _everything(controller.conn)
    again = service.address_all([second])
    assert again[0] == 1 and again[1][0][1] is False and _everything(controller.conn) == before


def test_address_without_a_version_keeps_the_p2_05_payload(controller):
    _, service = _task(controller)
    comment = _in(service, OPEN)
    service.address(comment)
    assert _events(controller.conn, "comment.addressed") == [
        {"id": comment, "from": "open", "to": "addressed"}
    ]
    other = _in(service, OPEN)
    service.address(other, version=1)
    assert _events(controller.conn, "comment.addressed")[-1]["version"] == 1


@pytest.mark.parametrize("bad", ["resolved", "unknown", "huge", "zero"])
def test_one_bad_id_writes_nothing_for_the_whole_batch(controller, bad):
    _, service = _task(controller)
    good = _in(service, OPEN)
    ids = {"resolved": _in(service, RESOLVED), "unknown": 99, "huge": 2**64, "zero": 0}
    before = _everything(controller.conn)
    error = IllegalTransition if bad == "resolved" else RecordNotFound
    with pytest.raises(error):
        service.address_all([good, ids[bad]])
    assert _everything(controller.conn) == before and not controller.conn.in_transaction
    assert service.ws.comment(good).status == OPEN


def test_a_resolved_comment_gives_the_illegal_move_message(controller):
    _, service = _task(controller)
    resolved = _in(service, RESOLVED)
    message = f"comment {resolved} is resolved: only an open comment can be addressed"
    with pytest.raises(IllegalTransition, match=message):
        service.address_all([resolved])


def test_a_design_changed_since_the_latest_version_is_refused(controller):
    task_id, service = _task(controller)
    comment = _in(service, OPEN)
    _state(controller, task_id, "component C")
    before = _everything(controller.conn)
    with pytest.raises(StaleDiagramVersion, match="the design changed while addressing"):
        service.address_all([comment])
    assert _everything(controller.conn) == before


def test_address_all_refuses_an_open_transaction_and_an_empty_list(controller):
    _, service = _task(controller)
    comment = _in(service, OPEN)
    with pytest.raises(ValueError, match="at least one"):
        service.address_all([])
    controller.conn.execute("BEGIN")
    try:
        with pytest.raises(sqlite3.OperationalError, match="outside a transaction"):
            service.address_all([comment])
    finally:
        controller.conn.execute("ROLLBACK")
    assert service.ws.comment(comment).status == OPEN


def test_huge_ids_are_unknown_for_every_move(controller):
    task_id, service = _task(controller)
    for move in (service.address, service.resolve, service.reopen):
        with pytest.raises(RecordNotFound, match=f"unknown comment {2**63} for task {task_id}"):
            move(2**63)


# T2: listing


def _loop_task(controller: HarnessController) -> tuple[str, CommentService, dict[str, int]]:
    """Version 1 (A, B, A -> B) with an open, an addressed and a resolved comment on A."""
    task_id, service = _task(controller)
    ids = {
        "open": service.add(1, A, "correction", "<b>drop</b> A\n  keep B\n")[0].id,
        "addressed": _in(service, ADDRESSED),
        "resolved": _in(service, RESOLVED),
    }
    return task_id, service, ids


def test_list_shows_only_open_comments_verbatim_and_writes_nothing(controller, db_path, capsys):
    task_id, service, ids = _loop_task(controller)
    second = service.add(1, B, "clarification", "why B?")[0].id
    dump = _db_dump(db_path)
    code, out, err = _run(capsys, task_id, "--db", str(db_path))
    assert (code, err) == (0, "")
    assert out == (
        f"task {task_id} · diagram version 1 (current) · comments: open 2 · addressed 1"
        " · resolved 1\n"
        f"#{ids['open']} · correction · component A (component:a) · version 1\n"
        "    <b>drop</b> A\n"
        "      keep B\n"
        f"#{second} · clarification · component B (component:b) · version 1\n"
        "    why B?\n"
        f"address: uv run python -m harness.redesign address {task_id} <comment id>...\n"
    )
    assert _db_dump(db_path) == dump


def test_list_marks_old_versions_removed_elements_and_a_changed_design(controller, db_path, capsys):
    task_id, service, ids = _loop_task(controller)
    ws = controller.workspace(task_id)
    with transaction(controller.conn):
        for entry in ws.design_entries():
            ws.withdraw_design(entry.id)
    _state(controller, task_id, "component B")
    assert DiagramService(controller, task_id).sync().version == 2
    _state(controller, task_id, "component C")
    dump = _db_dump(db_path)
    code, out, _ = _run(capsys, task_id, "--db", str(db_path))
    lines = out.splitlines()
    assert code == 0 and lines[0].startswith(f"task {task_id} · diagram version 2 (current)")
    assert lines[1] == (
        "the design changed since diagram version 2; address records the new version"
    )
    assert lines[2:6] == [
        f"#{ids['open']} · correction · component A (component:a) · version 1 (not current)",
        "    <b>drop</b> A",
        "      keep B",
        f"address: uv run python -m harness.redesign address {task_id} <comment id>...",
    ]
    assert _db_dump(db_path) == dump


def test_list_without_open_comments_or_diagrams(controller, db_path, capsys):
    task = controller.create_task("no diagrams")
    code, out, _ = _run(capsys, task.id, "--db", str(db_path))
    assert (code, out) == (
        0,
        f"task {task.id} · no diagrams yet · comments: open 0 · addressed 0 · resolved 0\n"
        "no open comments\n",
    )


def test_unknown_task_and_missing_db_exit_1_and_create_nothing(controller, db_path, tmp_path,
                                                               capsys):  # fmt: skip
    code, out, err = _run(capsys, "t-00000000", "--db", str(db_path))
    assert (code, out, err) == (1, "", "harness redesign: unknown task: t-00000000\n")
    missing = tmp_path / "absent" / "state.db"
    for argv in (["t-1"], ["address", "t-1", "1"]):
        code, _, err = _run(capsys, *argv, "--db", str(missing))
        assert (code, err) == (1, "harness redesign: unknown task: t-1\n")
    assert not missing.exists() and not missing.parent.exists()


# T3: address


def test_address_syncs_a_changed_design_then_addresses_in_the_new_version(
    controller, db_path, capsys
):
    task_id, service, ids = _loop_task(controller)
    _state(controller, task_id, "component C")
    tasks = controller.conn.execute("SELECT * FROM tasks").fetchall()
    dump = _db_dump(db_path)
    code, out, err = _run(capsys, "address", task_id, str(ids["open"]), str(ids["addressed"]),
                          "--db", str(db_path))  # fmt: skip
    assert (code, err) == (0, "")
    assert out == (
        "recorded diagram version 2\n"
        f"#{ids['open']} addressed in diagram version 2\n"
        f"#{ids['addressed']} already addressed\n"
    )
    added, removed = _diff(dump, _db_dump(db_path))
    tables = [line.split(" VALUES")[0] for line in added]
    assert tables == ['INSERT INTO "comments"', 'INSERT INTO "diagram_versions"',
                      'INSERT INTO "events"'], added  # fmt: skip
    assert "'addressed'" in added[0] and "comment.addressed" in added[2]
    assert [line.split(" VALUES")[0] for line in removed] == ['INSERT INTO "comments"']
    assert controller.conn.execute("SELECT * FROM tasks").fetchall() == tasks
    assert controller.conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 0
    assert _events(controller.conn, "comment.addressed")[-1] == {
        "id": ids["open"], "from": "open", "to": "addressed", "version": 2
    }  # fmt: skip
    code, out, _ = _run(capsys, "address", task_id, str(ids["open"]), "--db", str(db_path))
    assert (code, out) == (0, f"#{ids['open']} already addressed\n")


def test_address_without_a_design_change_says_so(controller, db_path, capsys):
    task_id, _, ids = _loop_task(controller)
    code, out, _ = _run(capsys, "address", task_id, str(ids["open"]), "--db", str(db_path))
    assert (code, out) == (
        0,
        f"#{ids['open']} addressed in diagram version 1 (diagrams unchanged since version 1)\n",
    )


def test_address_with_a_resolved_id_exits_1_and_writes_nothing(controller, db_path, capsys):
    task_id, _, ids = _loop_task(controller)
    dump = _db_dump(db_path)
    argv = ["address", task_id, str(ids["open"]), str(ids["resolved"]), "--db", str(db_path)]
    code, out, err = _run(capsys, *argv)
    assert (code, out) == (1, "")
    assert err == (
        f"harness redesign: comment {ids['resolved']} is resolved: only an open comment can be"
        " addressed\n"
    )
    code, _, err = _run(capsys, "address", task_id, "77", "--db", str(db_path))
    assert (code, err) == (1, f"harness redesign: unknown comment 77 for task {task_id}\n")
    assert _db_dump(db_path) == dump


def test_a_locked_database_says_retry(controller, db_path, capsys, monkeypatch):
    task_id, _, ids = _loop_task(controller)

    def impatient(path: Path) -> sqlite3.Connection:
        conn = connect(path)
        conn.execute("PRAGMA busy_timeout=50")
        return conn

    monkeypatch.setattr(redesign, "connect", impatient)
    dump = _db_dump(db_path)
    holder = sqlite3.connect(db_path, autocommit=True)
    try:
        holder.execute("BEGIN IMMEDIATE")
        code, out, err = _run(capsys, "address", task_id, str(ids["open"]), "--db", str(db_path))
        holder.execute("ROLLBACK")
    finally:
        holder.close()
    assert (code, out) == (1, "") and err.startswith("harness redesign: database is locked")
    assert err.endswith("; retry\n") and _db_dump(db_path) == dump


# T4 and T5: the real module entry point; the harness CLI is unchanged


def test_the_module_runs_with_exit_codes_0_1_2(controller, db_path):
    task_id, _, ids = _loop_task(controller)

    def run(*argv: str) -> subprocess.CompletedProcess:
        command = [sys.executable, "-m", "harness.redesign", *argv]
        return subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)

    listed = run(task_id, "--db", str(db_path))
    assert listed.returncode == 0 and f"#{ids['open']} · correction" in listed.stdout
    done = run("address", task_id, str(ids["open"]), "--db", str(db_path))
    assert done.returncode == 0 and "addressed in diagram version 1" in done.stdout
    assert run("t-00000000", "--db", str(db_path)).returncode == 1
    for argv in ([], ["address", task_id], ["address", task_id, "x"]):
        assert run(*argv, "--db", str(db_path)).returncode == 2, argv


def test_the_harness_cli_has_no_redesign_or_comments_command():
    parser = build_parser()
    [sub] = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    assert sorted(sub.choices) == BASE_COMMANDS
    assert "redesign" not in parser.format_help() and "comments" not in sub.choices
