import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from harness.controller import HarnessController
from harness.db import MIGRATIONS_DIR, connect, default_db_path

REPO_ROOT = Path(__file__).resolve().parent.parent
posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")

WRITER = """
import sys
from harness.controller import HarnessController
from harness.db import connect
conn = connect(sys.argv[1])
c = HarnessController(conn)
task = c.create_task("Add CSV export")
ws = c.workspace(task.id)
q = ws.ask("scope", "Which formats?")
ws.answer(q.id, "CSV only")
ws.ask("storage", "Where?")
p = ws.propose("Disk", "Use local files", "Simplest", question_id=q.id)
ws.resolve(p.id, "modified", "use a temp dir")
ws.record_event("note", {"k": [1, 2]})
c.advance(task.id)
ws.checkpoint("interview started", {"open": 1})
conn.close()
print(task.id)
"""

READER = """
import json, sys
from dataclasses import asdict
from harness.controller import HarnessController
from harness.db import connect
c = HarnessController(connect(sys.argv[1]))
[task] = c.list_tasks()
ws = c.workspace(task.id)
print(json.dumps({
    "task": asdict(task),
    "history": [asdict(h) for h in c.history(task.id)],
    "questions": [asdict(q) for q in ws.questions()],
    "open": [q.id for q in ws.open_questions()],
    "proposals": [asdict(p) for p in ws.proposals()],
    "events": [[e.kind, e.payload] for e in ws.events()],
    "latest": asdict(ws.latest_checkpoint()),
}))
"""


def _run(script, *args):
    result = subprocess.run(
        [sys.executable, "-c", script, *args],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    return result.stdout.strip()


def test_state_survives_process_restart(tmp_path):
    db = tmp_path / "state" / "state.db"
    task_id = _run(WRITER, str(db))
    state = json.loads(_run(READER, str(db)))
    assert state["task"]["id"] == task_id and state["task"]["phase"] == "interview"
    assert [h["to_phase"] for h in state["history"]] == ["investigation", "interview"]
    [answered, open_q] = state["questions"]
    assert (answered["status"], answered["answer"]) == ("answered", "CSV only")
    assert state["open"] == [open_q["id"]]
    [proposal] = state["proposals"]
    assert (proposal["status"], proposal["response"], proposal["question_id"]) == (
        "modified",
        "use a temp dir",
        answered["id"],
    )
    assert [kind for kind, _ in state["events"]] == [
        "task.created",
        "note",
        "phase.changed",
    ]
    assert state["events"][1] == ["note", {"k": [1, 2]}]
    assert (state["latest"]["phase"], state["latest"]["payload"]) == ("interview", {"open": 1})


def test_existing_0001_database_upgrades_keeping_rows(tmp_path):
    db = tmp_path / "old.db"
    raw = sqlite3.connect(db, autocommit=True)
    raw.executescript((MIGRATIONS_DIR / "0001_tasks.sql").read_text(encoding="utf-8"))
    raw.executescript(
        "CREATE TABLE schema_migrations "
        "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL);"
        "INSERT INTO schema_migrations VALUES (1, '0001_tasks.sql', 'then');"
        "INSERT INTO tasks VALUES ('t-old00001', 'Old task', 'simple', 'proposed',"
        " 'design', 'then', 'then');"
    )
    raw.close()

    conn = connect(db)
    try:
        names = [r[0] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY version")]
        assert names == [
            "0001_tasks.sql",
            "0002_workspace.sql",
            "0003_interview.sql",
            "0004_design.sql",
            "0005_decision_changes.sql",
        ]
        controller = HarnessController(conn)
        assert controller.get_task("t-old00001").request == "Old task"
        cp = controller.workspace("t-old00001").checkpoint("after upgrade")
        assert cp.phase == "design"
    finally:
        conn.close()


def test_upgrade_applies_only_the_new_migration(tmp_path, monkeypatch):
    from harness import db as db_module

    db = tmp_path / "old.db"
    only_first = [m for m in db_module._migrations() if m[0] == 1]
    monkeypatch.setattr(db_module, "_migrations", lambda: only_first)
    connect(db).close()
    monkeypatch.undo()
    raw = sqlite3.connect(db, autocommit=True)
    try:
        assert db_module.apply_migrations(raw) == [
            "0002_workspace.sql",
            "0003_interview.sql",
            "0004_design.sql",
            "0005_decision_changes.sql",
        ]
    finally:
        raw.close()


@posix_only
def test_new_database_is_private_to_the_owner(tmp_path):
    db = tmp_path / "fresh" / "state.db"
    connect(db).close()
    assert stat.S_IMODE(db.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(db.stat().st_mode) == 0o600


@posix_only
def test_default_location_directory_is_tightened(isolated_home, tmp_path):
    db = default_db_path(tmp_path / "repo")
    db.parent.mkdir(parents=True, mode=0o755)
    os.chmod(db.parent, 0o755)
    connect(db).close()
    assert stat.S_IMODE(db.parent.stat().st_mode) == 0o700


@posix_only
def test_foreign_existing_directory_is_left_alone(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o755)
    connect(shared / "state.db").close()
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    assert stat.S_IMODE((shared / "state.db").stat().st_mode) == 0o600


def test_default_db_path_is_outside_the_repository(isolated_home):
    path = default_db_path(REPO_ROOT)
    assert not path.resolve().is_relative_to(REPO_ROOT)
    assert path.is_relative_to(isolated_home)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
@pytest.mark.parametrize("name", ["state.db", "state.db-wal", "state.db-shm"])
def test_state_files_are_git_ignored(name):
    result = subprocess.run(
        ["git", "check-ignore", "-q", name], cwd=REPO_ROOT, capture_output=True, check=False
    )
    assert result.returncode == 0


def test_0002_database_upgrades_questions_with_interview_columns(tmp_path, monkeypatch):
    from harness import db as db_module

    db = tmp_path / "old.db"
    first_two = [m for m in db_module._migrations() if m[0] <= 2]
    monkeypatch.setattr(db_module, "_migrations", lambda: first_two)
    old = connect(db)
    task = HarnessController(old).create_task("Old task")
    question_id = old.execute(
        "INSERT INTO questions (task_id, topic, text, source, status, created_at, updated_at)"
        " VALUES (?, 'scope', 'Which formats?', 'interview', 'open', 'then', 'then')",
        (task.id,),
    ).lastrowid
    old.close()
    monkeypatch.undo()

    conn = connect(db)
    try:
        names = [r[0] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY version")]
        assert names == [
            "0001_tasks.sql",
            "0002_workspace.sql",
            "0003_interview.sql",
            "0004_design.sql",
            "0005_decision_changes.sql",
        ]
        upgraded = HarnessController(conn).workspace(task.id).question(question_id)
        assert (upgraded.kind, upgraded.asked_at, upgraded.skip_source) == ("question", None, None)
        assert upgraded.catalogue_key is None and upgraded.text == "Which formats?"
    finally:
        conn.close()


def test_0003_database_upgrades_with_design_entries(tmp_path, monkeypatch):
    from harness import db as db_module

    db = tmp_path / "old.db"
    first_three = [m for m in db_module._migrations() if m[0] <= 3]
    monkeypatch.setattr(db_module, "_migrations", lambda: first_three)
    old = connect(db)
    task = HarnessController(old).create_task("Old task")
    question = HarnessController(old).workspace(task.id).ask("scope", "Which formats?")
    assert (
        old.execute("SELECT name FROM sqlite_master WHERE name = 'design_entries'").fetchone()
        is None
    )
    old.close()
    monkeypatch.undo()

    conn = connect(db)
    try:
        assert db_module.apply_migrations(conn) == []
        names = [r[0] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY version")]
        assert names[-1] == "0005_decision_changes.sql"
        ws = HarnessController(conn).workspace(task.id)
        assert ws.question(question.id).text == "Which formats?"
        assert ws.design_entries() == []
        entry = ws.state_design("components", "an Exporter")
        assert ws.design_entries() == [entry]
    finally:
        conn.close()


def test_0004_database_upgrades_with_decision_changes(tmp_path, monkeypatch):
    from harness import db as db_module

    db = tmp_path / "old.db"
    first_four = [m for m in db_module._migrations() if m[0] <= 4]
    monkeypatch.setattr(db_module, "_migrations", lambda: first_four)
    old = connect(db)
    task = HarnessController(old).create_task("Old task")
    proposal = (
        HarnessController(old)
        .workspace(task.id)
        .conn.execute(
            "INSERT INTO proposals (task_id, title, recommendation, rationale, status, created_at)"
            " VALUES (?, 'T', 'R', 'W', 'pending', 'then')",
            (task.id,),
        )
        .lastrowid
    )
    old.close()
    monkeypatch.undo()

    conn = connect(db)
    try:
        ws = HarnessController(conn).workspace(task.id)
        assert ws.proposal(proposal).impact is None
        assert ws.decision_changes() == []
        change = ws.propose_change("DEC-0001", 1, "T", "D", "R", "", "")
        assert (change.ref, change.status, change.scope, change.impact) == (
            "C1",
            "pending",
            "not stated",
            "not stated",
        )
        assert ws.decision_changes("pending") == [change]
    finally:
        conn.close()
