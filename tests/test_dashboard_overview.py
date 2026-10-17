"""P2-02: task overview (§8.1) and task list, switching and resuming (§8.9) in the read model."""

import json
import re

from harness.controller import HarnessController
from harness.dashboard.overview import APPROVED, AWAITING_ANSWER, AWAITING_APPROVAL
from harness.dashboard.server import STATIC_DIR
from harness.dashboard.view import NO_ACTIVE_TASK, dashboard_view
from harness.db import connect
from harness.phases import Phase
from harness.resume import NOTHING_TO_RESUME
from harness.state_models import QuestionStatus
from tests.complete_task import _design, _interview, _test_plan, settle
from tests.test_dashboard import _stamp, api, cli, fetch, needs_git, serve, start_task

VERDICTS = "accept|reject|modify"


def _investigated(db_path, sample_repo, *words: str) -> str:
    task_id = start_task(db_path, *(words or ("csv", "export")))
    cli(db_path, "investigate", task_id, "--repo", str(sample_repo))
    cli(db_path, "phase", task_id, "next")
    return task_id


def _resume(db_path, task_id: str) -> dict:
    return json.loads(cli(db_path, "resume", task_id, "--json"))


def _with_controller(db_path, step, task_id: str) -> None:
    conn = connect(db_path)
    try:
        step(HarnessController(conn), task_id)
    finally:
        conn.close()


def _events(db_path, task_id: str) -> list:
    conn = connect(db_path)
    try:
        return HarnessController(conn).workspace(task_id).events()
    finally:
        conn.close()


def _with_created(db_path, task_id: str) -> str:
    conn = connect(db_path)
    try:
        return HarnessController(conn).get_task(task_id).created_at
    finally:
        conn.close()


def _propose(db_path, task_id: str) -> str:
    out = cli(
        db_path, "interview", "ask", task_id, "security", "Who", "may", "export?", "--decision"
    )
    qid = re.search(r"Q(\d+)", out).group(1)
    cli(
        db_path, "interview", "propose", task_id, qid, "--title", "Export access",
        "--recommendation", "Admins only", "--rationale", "Least privilege",
    )  # fmt: skip
    return qid


@needs_git
def test_overview_fields_match_the_cli(db_path, sample_repo):
    task_id = _investigated(db_path, sample_repo)
    _propose(db_path, task_id)
    with serve(db_path, task_id) as server:
        task = api(server)["task"]
        assert task["created_at"] == _with_created(db_path, task_id)
        assert task["status"] == AWAITING_APPROVAL
        assert task["pending_approvals"] == [
            {
                "kind": "interview recommendation",
                "ref": "P1",
                "title": "Export access",
                "command": f"harness interview decide {task_id} 1 {VERDICTS}",
            }
        ]
        last = _events(db_path, task_id)[-1]
        assert task["latest_activity"] == {"kind": last.kind, "at": last.created_at}
        assert last.kind == "proposal.created"
        assert task["resume"] == _resume(db_path, task_id)

        cli(db_path, "interview", "decide", task_id, "1", "reject")
        after = api(server)["task"]
    assert after["pending_approvals"] == []
    assert after["resume"]["pending_question"] is not None
    assert after["status"] == AWAITING_ANSWER
    assert after["latest_activity"]["kind"] == "proposal.decided"
    assert after["resume"] == _resume(db_path, task_id)


def _answered(db_path, task_id: str) -> int:
    conn = connect(db_path)
    try:
        ws = HarnessController(conn).workspace(task_id)
        return ws.questions(QuestionStatus.ANSWERED)[0].id
    finally:
        conn.close()


def _pending(server) -> list[tuple[str, str, str, str]]:
    return [
        (i["kind"], i["ref"], i["title"], i["command"])
        for i in api(server)["task"]["pending_approvals"]
    ]


@needs_git
def test_every_pending_approval_kind_and_phase_gating(db_path, sample_repo):
    task_id = _investigated(db_path, sample_repo)
    cli(
        db_path, "decision", "change", task_id, "DEC-0001", "--decision", "Store in SQLite",
        "--rationale", "Queries", "--impact", "A migration",
    )  # fmt: skip
    change = ("decision change", "C1", "DEC-0001: Storage engine")
    with serve(db_path, task_id) as server:
        assert _pending(server) == [(*change, f"harness decision decide {task_id} 1 {VERDICTS}")]

        _with_controller(db_path, _interview, task_id)
        cli(db_path, "phase", task_id, "next")
        cli(
            db_path, "design", "propose", task_id, "components", "One", "module", "--rationale", "r"
        )
        assert _pending(server)[0] == (
            "design recommendation",
            "E1",
            "One module",
            f"harness design decide {task_id} 1 {VERDICTS}",
        )
        cli(db_path, "design", "decide", task_id, "1", "reject")
        cli(db_path, "decision", "decide", task_id, "1", "reject")
        assert _pending(server) == []

        _with_controller(db_path, _design, task_id)
        cli(db_path, "phase", task_id, "next")
        plan = ("test plan", "plan", "not_approved", f"harness testplan approve {task_id}")
        assert _pending(server) == [plan]
        assert api(server)["task"]["status"] == AWAITING_APPROVAL
        covers = f"Q{_answered(db_path, task_id)}"
        scenario = ("unit", "Rows", "--rationale", "r", "--covers", covers)
        cli(db_path, "testplan", "propose", task_id, *scenario)
        assert _pending(server) == [
            ("test scenario", "S1", "Rows", f"harness testplan decide {task_id} 1 {VERDICTS}"),
            plan,
        ]
        cli(db_path, "testplan", "decide", task_id, "1", "reject")
        _with_controller(db_path, _test_plan, task_id)
        assert _pending(server) == []

        _with_controller(db_path, settle, task_id)
        assert _pending(server) == []
        cli(db_path, "phase", task_id, "next")
        approve = f"harness approve {task_id}"
        assert _pending(server) == [("final approval", "spec", "approval: none", approve)]

        cli(db_path, "approve", task_id)
        data = api(server)
        task, row = data["task"], data["tasks"][0]
        assert (task["pending_approvals"], task["status"]) == ([], APPROVED)
        assert task["resume"]["next"] is None and task["resume"]["resumable"] is False
        assert (row["status"], row["next"]) == (APPROVED, None)

        cli(db_path, "phase", task_id, "back", "final_review", "--reason", "recheck")
        cli(db_path, "phase", task_id, "next")
        [(kind, ref, title, command)] = _pending(server)
    assert (kind, ref, command) == ("final approval", "spec", approve)
    assert title.startswith("approval: spec v1 ") and "(stale: " in title


def test_task_list_switching_and_resume_rows(db_path):
    launch = start_task(db_path, "launch", "task")
    other = start_task(db_path, "other", "task")
    cli(db_path, "phase", other, "next")
    _stamp(db_path, launch, "2026-01-01T00:00:00+00:00")
    _stamp(db_path, other, "2026-01-02T00:00:00+00:00")
    with serve(db_path, launch) as server:
        default, empty = api(server), api(server, "?task=")
        switched = api(server, f"?task={other}")
        unknown = api(server, "?task=t-00000000")
        garbage = api(server, "?task=../x")
    assert default["task"]["id"] == empty["task"]["id"] == launch
    assert switched["task"]["id"] == other and switched["task"]["phase"] == "interview"
    assert switched["notice"] is None and default["notice"] is None
    assert (unknown["task"], unknown["notice"]) == (None, "unknown task: t-00000000")
    assert (garbage["task"], garbage["notice"]) == (None, "unknown task: ../x")
    rows = default["tasks"]
    assert [r["id"] for r in rows] == [other, launch]
    assert set(rows[0]) == {"id", "request", "phase", "updated_at", "status", "next"}
    for listed in (switched, unknown, garbage):
        assert listed["tasks"] == rows
    for row in rows:
        resume = _resume(db_path, row["id"])
        assert row["next"] == resume["next"] == f"harness investigate {row['id']}"
        assert (row["phase"], row["updated_at"]) == (resume["phase"], resume["updated_at"])
        assert row["status"] == "in progress"
    with serve(db_path, None) as server:
        none = api(server)
    assert (none["task"], none["notice"], none["tasks"]) == (None, NO_ACTIVE_TASK, rows)


def test_an_unreadable_task_is_marked_in_the_list_only(db_path):
    good = start_task(db_path, "good", "task")
    bad = start_task(db_path, "bad", "task")
    _stamp(db_path, good, "2026-01-02T00:00:00+00:00")
    _stamp(db_path, bad, "2026-01-01T00:00:00+00:00")
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO checkpoints (task_id, phase, label, payload, created_at)"
            " VALUES (?, ?, 'investigation', '{broken', '2026-01-01T00:00:00+00:00')",
            (bad, str(Phase.INVESTIGATION)),
        )
    finally:
        conn.close()
    with serve(db_path, good) as server:
        data = api(server)
        status, _, body = fetch(server, f"/api/dashboard?task={bad}")
    assert data["task"]["id"] == good
    [first, second] = data["tasks"]
    assert (first["id"], first["status"]) == (good, "in progress")
    assert second["id"] == bad and second["next"] is None
    assert second["status"].startswith("unavailable: ")
    assert status == 500 and set(json.loads(body)) == {"error"}


def test_the_page_states_nothing_to_resume_like_the_cli():
    assert f'"{NOTHING_TO_RESUME}"' in (STATIC_DIR / "app.js").read_text(encoding="utf-8")


def test_a_view_is_one_committed_snapshot(db_path, monkeypatch):
    task_id = start_task(db_path, "csv", "export")
    original, spent = HarnessController.list_tasks, []

    def commit_mid_view(controller: HarnessController) -> list:
        if not spent:
            spent.append(True)
            cli(db_path, "phase", task_id, "next")
        return original(controller)

    monkeypatch.setattr(HarnessController, "list_tasks", commit_mid_view)
    view = dashboard_view(db_path, task_id, None)
    task, row = view["task"], view["tasks"][0]
    assert task["phase"] == task["resume"]["phase"] == row["phase"] == "investigation"
    assert row["updated_at"] == task["resume"]["updated_at"]
    after = dashboard_view(db_path, task_id, None)
    assert spent == [True]
    task, row = after["task"], after["tasks"][0]
    assert task["phase"] == task["resume"]["phase"] == row["phase"] == "interview"
