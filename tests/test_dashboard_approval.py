"""P2-07: the Approval Center in the read model and POST /api/approve (Approve & Implement)."""

import sqlite3
import threading
from pathlib import Path

import pytest

from harness.approval import ApprovalGate, require_approval
from harness.cli_approve import approval_lines
from harness.cli_spec import spec_store
from harness.comments import CommentService
from harness.dashboard import approve as approve_module
from harness.dashboard.approval_view import GROUPS
from harness.dashboard.server import STATIC_DIR, DashboardServer
from harness.db import transaction
from harness.diagrams import DiagramService
from harness.spec_manager import SpecificationManager
from harness.spec_record import parse_spec
from tests.complete_task import _design, _interview, settle
from tests.sample_design import add_sample_design
from tests.sample_repo import make_sample_repo, tree_snapshot
from tests.test_approval import _content_change, _pending_change
from tests.test_dashboard import _db_dump, api, cli, fetch, needs_git, serve, start_task
from tests.test_dashboard_comments import BAD_BODIES, DECLARED, EDGE, _json, post
from tests.test_dashboard_diagrams import _state, _with
from tests.test_dashboard_overview import _answered, _investigated, _propose, _with_controller

APPROVE = "/api/approve"
ZERO = "sha256:" + "0" * 64
pytestmark = needs_git


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(DashboardServer, "poll_seconds", 0.05)


def approval_task(db_path: Path, repo: Path, design: bool = True, enter: bool = True) -> str:
    """A task investigated in repo, settled (with the sample design) and in the approval phase."""
    task_id = start_task(db_path, "approve", "flow")
    cli(db_path, "investigate", task_id, "--repo", str(repo))
    if design:
        _with(db_path, lambda c: add_sample_design(c, task_id))
    _with(db_path, lambda c: settle(c, task_id))
    if enter:
        cli(db_path, "phase", task_id, "next")
    return task_id


def _final(server) -> dict:
    return api(server)["task"]["approval_center"]["final"]


def _tokens(server, task_id: str) -> dict:
    final = _final(server)
    keys = ("version", "spec_hash", "review_hash")
    return {"task": task_id} | {k: final[k] for k in keys}


def _approve(server, body) -> tuple[int, dict]:
    return _json(post(server, body, path=APPROVE))


def _state_of(db_path: Path, specs: Path) -> tuple:
    return _db_dump(db_path), tree_snapshot(specs)


def _comment(db_path: Path, task_id: str, address: bool = False) -> int:
    def step(controller):
        service = CommentService(controller, task_id)
        service.diagrams.sync()
        comment = service.add(1, EDGE, "correction", "fixture comment")[0]
        if address:
            service.address(comment.id, 1)
        return comment.id

    return _with(db_path, step)


def _resolve(db_path: Path, task_id: str, comment: int) -> None:
    def step(controller):
        service = CommentService(controller, task_id)
        service.address(comment, 1)
        service.resolve(comment)

    _with(db_path, step)


def _rows(db_path: Path, table: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
    finally:
        conn.close()


def _codes(final: dict) -> list[str]:
    return [b["code"] for b in final["blockers"]]


def _grouped(server, seen: list) -> dict:
    """Check the groups split exactly the overview's pending approvals; collect what was seen."""
    task = api(server)["task"]
    groups, pending = task["approval_center"]["groups"], task["pending_approvals"]
    assert sorted(groups) == sorted(GROUPS)
    assert sorted(map(str, sum(groups.values(), []))) == sorted(map(str, pending))
    for name, items in groups.items():
        assert [i for i in pending if i["kind"] in GROUPS[name]] == items
        seen += [(name, i["kind"]) for i in items]
    return task["approval_center"]["final"]


def test_the_center_groups_every_pending_approval_from_the_overview(db_path, sample_repo):
    task_id = _investigated(db_path, sample_repo)
    seen: list = []
    with serve(db_path, task_id) as server:
        _propose(db_path, task_id)
        final = _grouped(server, seen)
        assert final["enabled"] is False and _codes(final)[0] == "phase"
        assert final["spec_hash"] is None and final["preview"] is None
        cli(db_path, "interview", "decide", task_id, "1", "reject")
        cli(db_path, "decision", "change", task_id, "DEC-0001", "--decision", "SQLite",
            "--rationale", "Queries", "--impact", "A migration")  # fmt: skip
        _grouped(server, seen)
        _with_controller(db_path, _interview, task_id)
        cli(db_path, "phase", task_id, "next")
        cli(db_path, "design", "propose", task_id, "components", "One", "--rationale", "r")
        _grouped(server, seen)
        cli(db_path, "design", "decide", task_id, "1", "reject")
        cli(db_path, "decision", "decide", task_id, "1", "reject")
        _with_controller(db_path, _design, task_id)
        cli(db_path, "phase", task_id, "next")
        covers = f"Q{_answered(db_path, task_id)}"
        cli(db_path, "testplan", "propose", task_id, "unit", "Rows", "--rationale", "r",
            "--covers", covers)  # fmt: skip
        _grouped(server, seen)
        cli(db_path, "testplan", "decide", task_id, "1", "reject")
        _with_controller(db_path, settle, task_id)
        cli(db_path, "phase", task_id, "next")
        final = _grouped(server, seen)
    assert seen == [
        ("decisions", "interview recommendation"),
        ("decisions", "decision change"),
        ("decisions", "design recommendation"),
        ("decisions", "decision change"),
        ("test_plan", "test scenario"),
        ("test_plan", "test plan"),
        ("final_spec", "final approval"),
    ]
    assert final["phase"] == "approval" and final["command"] == f"harness approve {task_id}"


@pytest.mark.parametrize("address", [False, True])
def test_unresolved_comments_disable_the_button_and_the_server_refuses(
    db_path, sample_repo, tmp_path, address
):
    task_id = approval_task(db_path, sample_repo)
    comment = _comment(db_path, task_id, address)
    specs = tmp_path / "specs"
    with serve(db_path, task_id, str(specs)) as server:
        final = _final(server)
        before = _state_of(db_path, specs)
        status, data = _approve(server, _tokens(server, task_id))
        assert _state_of(db_path, specs) == before
        _resolve(db_path, task_id, comment)
        ready = _final(server)
    counts = {"open": 0 if address else 1, "addressed": 1 if address else 0}
    assert final["comments"] == counts and final["enabled"] is False
    word = "addressed 1" if address else "open 1"
    assert final["blockers"] == [
        {
            "code": "comments",
            "text": f"1 diagram comment(s) not resolved (open {counts['open']}"
            f" · addressed {counts['addressed']})",
        }
    ]
    assert status == 409 and "reload" not in data and word in data["error"]
    assert data["error"].startswith("1 diagram comment(s) are not resolved")
    assert ready["enabled"] is True and ready["blockers"] == []


def test_outside_approval_a_blocking_question_or_a_failing_review_is_refused(
    db_path, sample_repo, tmp_path
):
    task_id = approval_task(db_path, sample_repo, enter=False)
    specs = tmp_path / "specs"
    with serve(db_path, task_id, str(specs)) as server:
        final = _final(server)
        before = _state_of(db_path, specs)
        body = _tokens(server, task_id) | {"spec_hash": ZERO}
        phase = _approve(server, body)
        assert _state_of(db_path, specs) == before
        cli(db_path, "phase", task_id, "next")

        def ask(controller):
            ws = controller.workspace(task_id)
            with transaction(controller.conn):
                ws.mark_asked(ws.ask("edge_cases", "fixture q").id)

        _with(db_path, ask)
        asked = _final(server)
        before = _state_of(db_path, specs)
        question = _approve(server, _tokens(server, task_id))
        assert _state_of(db_path, specs) == before
    assert _codes(final) == ["phase"] and final["enabled"] is False
    assert final["blockers"][0]["text"] == (
        "the task is in phase final_review; Approve & Implement opens in the approval phase"
    )
    assert phase[0] == 409 and "is in phase final_review" in phase[1]["error"]
    assert _codes(asked) == ["questions", "review"] and asked["enabled"] is False
    assert asked["blockers"][0]["text"].startswith("blocking questions remain: ")
    assert asked["blockers"][1]["text"].startswith(
        f"the final design review fails (harness review {task_id}): No blocking questions remain"
    )
    assert question[0] == 409 and "final design review blocks approval" in question[1]["error"]


def test_a_pending_decision_change_fails_the_review(db_path, sample_repo, tmp_path):
    task_id = approval_task(db_path, sample_repo)
    _with(db_path, lambda c: _pending_change(c, sample_repo, task_id))
    specs = tmp_path / "specs"
    with serve(db_path, task_id, str(specs)) as server:
        final = _final(server)
        before = _state_of(db_path, specs)
        status, data = _approve(server, _tokens(server, task_id))
        assert _state_of(db_path, specs) == before
    assert _codes(final) == ["review"] and final["enabled"] is False
    assert status == 409 and "final design review blocks approval" in data["error"]


def test_a_dashboard_approval_writes_the_cli_snapshot(db_path, sample_repo, tmp_path):
    task_id = approval_task(db_path, sample_repo)
    specs = tmp_path / "specs"

    def draft(controller):
        root = sample_repo
        SpecificationManager(controller, task_id, spec_store(controller, task_id, str(specs)),
                             root).draft()  # fmt: skip

    with serve(db_path, task_id, str(specs)) as server:
        tokens = _tokens(server, task_id)
        _with(db_path, draft)
        before = {t: len(_rows(db_path, t)) for t in ("approvals", "spec_versions")}
        status, data = _approve(server, tokens)
        final = _final(server)
    assert status == 201
    assert set(data) == {"created", "approval", "spec", "superseded", "output"}
    assert data["created"] is True and data["superseded"] is None
    [row] = _rows(db_path, "approvals")
    assert before == {"approvals": 0, "spec_versions": 0}
    assert len(_rows(db_path, "spec_versions")) == 1
    record = parse_spec(Path(row["path"]))
    assert record.status == "approved" and record.content_hash == row["content_hash"]
    assert data["spec"] == {"version": 1, "path": row["path"]}
    assert data["approval"] == {k: row[k] for k in data["approval"]}
    kinds = [r["kind"] for r in _rows(db_path, "events")]
    assert kinds[-2:] == ["spec.approved", "approval.granted"]
    labels = [r["label"] for r in _rows(db_path, "checkpoints")]
    assert labels[-2:] == ["spec", "approval"]
    assert not (specs / task_id / "draft.md").exists()

    def check(controller):
        store = spec_store(controller, task_id, str(specs))
        approval = require_approval(controller, task_id, store, sample_repo)
        assert approval.id == row["id"]
        gate = ApprovalGate(controller, task_id, store, sample_repo)
        assert data["output"] == approval_lines(approval, record, None)
        return gate.status()

    status_text = _with(db_path, check)
    assert data["output"][-1].endswith("(implementation handoff authorized; AL launch is Phase 3)")
    assert final["status"] == status_text and status_text.endswith("(current)")
    assert _codes(final) == ["approved"]


def _comparable(db_path: Path, old: str, new: str) -> dict:
    tables = ("approvals", "spec_versions", "events", "checkpoints")
    found = {}
    for table in tables:
        rows = []
        for row in _rows(db_path, table):
            values = {k: row[k] for k in row.keys() if k != "created_at"}
            rows.append({k: v.replace(old, new) if isinstance(v, str) else v
                         for k, v in values.items()})  # fmt: skip
        found[table] = rows
    return found


def _sealed(path: Path) -> list[str]:
    return [line for line in path.read_text().splitlines() if "**Timestamp:**" not in line]


def test_the_dashboard_snapshot_equals_the_cli_snapshot(db_path, sample_repo, tmp_path):
    task_id = approval_task(db_path, sample_repo, design=False)
    with serve(db_path, task_id) as server:
        tokens = _tokens(server, task_id)
    copies = {}
    for name in ("a", "b"):
        copies[name] = tmp_path / f"{name}.db"
        source, target = sqlite3.connect(db_path), sqlite3.connect(copies[name])
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
    a, b = tmp_path / "specs-a", tmp_path / "specs-b"
    out = cli(copies["a"], "approve", task_id, "--specs", str(a))
    with serve(copies["b"], task_id, str(b)) as server:
        status, data = _approve(server, tokens)
    assert status == 201 and out == "".join(f"{line}\n" for line in data["output"]).replace(
        str(b), str(a)
    )
    v1a, v1b = a / task_id / "v1.md", b / task_id / "v1.md"
    assert _sealed(v1a) == _sealed(v1b)
    assert _comparable(copies["a"], str(a), str(b)) == _comparable(copies["b"], str(a), str(b))


def test_a_replay_or_a_concurrent_approve_records_one_approval(db_path, sample_repo, tmp_path):
    task_id = approval_task(db_path, sample_repo)
    specs = tmp_path / "specs"
    with serve(db_path, task_id, str(specs)) as server:
        tokens = _tokens(server, task_id)
        first = _approve(server, tokens)
        after = _state_of(db_path, specs)
        replay = _approve(server, tokens)
        assert _state_of(db_path, specs) == after
    assert first[0] == 201 and replay[0] == 200 and replay[1]["created"] is False
    assert replay[1]["approval"] == first[1]["approval"]
    approval = first[1]["approval"]
    assert replay[1]["output"] == [
        f"approval #{approval['id']} is current: spec v1 {approval['content_hash']};"
        " nothing was written"
    ]
    assert sorted(p.name for p in (specs / task_id).iterdir()) == ["v1.md"]

    other = approval_task(db_path, sample_repo)
    results = []
    with serve(db_path, other, str(specs)) as server:
        tokens = _tokens(server, other)
        threads = [threading.Thread(target=lambda: results.append(_approve(server, tokens)))
                   for _ in range(2)]  # fmt: skip
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    assert sorted(status for status, _ in results) == [200, 201]
    assert len([r for r in _rows(db_path, "approvals") if r["task_id"] == other]) == 1
    assert sorted(p.name for p in (specs / other).iterdir()) == ["v1.md"]


def _new_version(db_path, task_id, sample_repo):
    _state(db_path, task_id, "component Exporter")
    _with(db_path, lambda c: DiagramService(c, task_id).sync())


def _unsynced(db_path, task_id, sample_repo):
    _state(db_path, task_id, "component Exporter")


def _review(db_path, task_id, sample_repo):
    _with(db_path, lambda c: _pending_change(c, sample_repo, task_id))


def _content(db_path, task_id, sample_repo):
    _with(db_path, lambda c: _content_change(c, sample_repo, task_id))


@pytest.mark.parametrize(
    "change, version, fragment",
    [
        (_new_version, 1, "diagram version 1 is no longer current (current: 2); reload"),
        (_unsynced, 1, "the design changed since diagram version 1; reload to see"),
        (_review, 1, "the final design review changed since the page was loaded"),
        (_content, 1, "since the page was loaded; reload before approving"),
        (None, None, "diagram version none is no longer current (current: 1)"),
    ],
)
def test_a_stale_page_is_refused_with_a_reload_hint(
    db_path, sample_repo, tmp_path, change, version, fragment
):
    task_id = approval_task(db_path, sample_repo)
    specs = tmp_path / "specs"
    with serve(db_path, task_id, str(specs)) as server:
        tokens = _tokens(server, task_id) | {"version": version}
        if change is not None:
            change(db_path, task_id, sample_repo)
        before = _state_of(db_path, specs)
        status, data = _approve(server, tokens)
        assert _state_of(db_path, specs) == before
    assert (status, data["reload"]) == (409, True) and fragment in data["error"]


def test_the_spec_hash_is_checked_on_its_own(db_path, sample_repo, tmp_path):
    task_id = approval_task(db_path, sample_repo)
    specs = tmp_path / "specs"
    with serve(db_path, task_id, str(specs)) as server:
        tokens = _tokens(server, task_id) | {"spec_hash": ZERO}
        before = _state_of(db_path, specs)
        status, data = _approve(server, tokens)
        assert _state_of(db_path, specs) == before
    assert (status, data["reload"]) == (409, True)
    assert data["error"].startswith("the specification changed since the page was loaded")


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"version": 0},
        {"version": 1.0},
        {"version": "1"},
        {"task": ""},
        {"task": 3},
        {"spec_hash": "sha256:xyz"},
        {"spec_hash": ZERO.upper()},
        {"review_hash": None},
        {"extra": 1},
    ],
)
def test_an_invalid_body_is_a_400_and_writes_nothing(db_path, sample_repo, tmp_path, changes):
    task_id = approval_task(db_path, sample_repo, design=False)
    specs = tmp_path / "specs"
    with serve(db_path, task_id, str(specs)) as server:
        tokens = _tokens(server, task_id)
        before = _state_of(db_path, specs)
        status, data = _approve(server, tokens | changes)
        missing = dict(tokens)
        del missing["review_hash"]
        assert _approve(server, missing)[0] == 400
        for raw in [*BAD_BODIES, b'{"task": "t", "task": "t"}']:
            assert _approve(server, raw) == (400, {"error": "body must be a UTF-8 JSON object"})
        assert _state_of(db_path, specs) == before
    assert status == 400 and set(data) == {"error"}


def test_unknown_tasks_a_missing_db_and_no_commit_are_refused(db_path, sample_repo, tmp_path):
    task_id = approval_task(db_path, sample_repo, design=False)
    specs = tmp_path / "specs"
    with serve(db_path, task_id, str(specs)) as server:
        tokens = _tokens(server, task_id)
        before = _state_of(db_path, specs)
        unknown = _approve(server, tokens | {"task": "t-00000000"})
        malformed = _approve(server, tokens | {"task": "../x"})
        assert _state_of(db_path, specs) == before
    assert unknown == (404, {"error": "unknown task: t-00000000"})
    assert malformed == (404, {"error": "unknown task: ../x"})
    missing = tmp_path / "absent" / "state.db"
    with serve(missing, task_id) as server:
        assert _approve(server, tokens)[0] == 404
    assert not missing.exists() and not missing.parent.exists()

    bare = make_sample_repo(tmp_path / "bare", with_git=False)
    other = approval_task(db_path, bare, design=False)
    with serve(db_path, other, str(specs)) as server:
        tokens = _tokens(server, other)
        before = _state_of(db_path, specs)
        status, data = _approve(server, tokens)
        assert _state_of(db_path, specs) == before
    assert status == 409 and "cannot be tied to a commit" in data["error"]


def test_headers_are_checked_before_the_body_like_every_write(db_path, sample_repo, tmp_path):
    task_id = approval_task(db_path, sample_repo, design=False)
    specs = tmp_path / "specs"
    with serve(db_path, task_id, str(specs)) as server:
        tokens = _tokens(server, task_id)
        before = _state_of(db_path, specs)
        port = server.port
        for headers, status in (
            ({"Origin": None}, 403),
            ({"Origin": "http://evil.example"}, 403),
            ({"Sec-Fetch-Site": "cross-site"}, 403),
            ({"Content-Type": "text/plain"}, 415),
            ({"Content-Length": None, "Transfer-Encoding": "chunked"}, 411),
            ({"Content-Length": "x"}, 400),
            ({"Host": f"evil.example:{port}"}, 403),
        ):
            assert post(server, tokens, headers)[0] == status, headers
            assert post(server, tokens, headers, path=APPROVE)[0] == status, headers
        short = post(server, b"{", {"Content-Length": "10"}, shut=True, path=APPROVE)
        assert _json(short) == (400, {"error": "request body is incomplete"})
        assert post(server, tokens, path=f"{APPROVE}/x")[0] == 501
        assert post(server, tokens, path=APPROVE, method="PUT")[0] == 501
        assert fetch(server, APPROVE)[0] == 404
        assert _state_of(db_path, specs) == before


def test_a_locked_database_is_a_503_and_the_retry_approves_once(
    db_path, sample_repo, tmp_path, monkeypatch
):
    task_id = approval_task(db_path, sample_repo, design=False)
    specs = tmp_path / "specs"
    holder = sqlite3.connect(db_path, autocommit=True, check_same_thread=False)

    class LockedOut(ApprovalGate):
        def approve(self, guard=None):
            self.manager.conn.execute("PRAGMA busy_timeout=100")
            holder.execute("BEGIN IMMEDIATE")
            try:
                return super().approve(guard=guard)
            finally:
                holder.execute("ROLLBACK")

    try:
        with serve(db_path, task_id, str(specs)) as server:
            tokens = _tokens(server, task_id)
            before = _state_of(db_path, specs)
            monkeypatch.setattr(approve_module, "ApprovalGate", LockedOut)
            status, data = _approve(server, tokens)
            assert _state_of(db_path, specs) == before
            monkeypatch.undo()
            saved = _approve(server, tokens)
            again = _approve(server, tokens)
    finally:
        holder.close()
    assert (status, data["retry"]) == (503, True) and "locked" in data["error"]
    assert saved[0] == 201 and again[0] == 200 and again[1]["created"] is False
    assert len(_rows(db_path, "approvals")) == 1


def test_the_approvals_script_is_dom_only_and_posts_only_to_approve(db_path):
    with serve(db_path, None) as server:
        status, headers, script = fetch(server, "/static/approvals.js")
    assert status == 200 and headers["Content-Type"] == "text/javascript; charset=utf-8"
    source = script.decode("utf-8")
    assert "innerHTML" not in source and "createElement" not in source  # built through el()
    assert [line for line in source.splitlines() if '"/api/' in line] == [
        'const APPROVE_API = "/api/approve";'
    ]
    assert "renderApprovalCenter" in DECLARED.findall(source)
    assert source == (STATIC_DIR / "approvals.js").read_text(encoding="utf-8")
