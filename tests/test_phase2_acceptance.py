"""P2-08: the wrong-connection scenario end to end (spec S5, DEC-0018).

A design with a wrong connection reaches the approval phase; the developer comments on it in the
dashboard; the harness fixes it through the redesign loop (the redesign tool plus the existing
CLI design commands, with the developer's verdicts scripted); the developer resolves every
comment; only then does Approve & Implement record the same snapshot as `harness approve`.
"""

import io
import re
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from harness import redesign
from harness.approval import require_approval
from harness.cli_spec import spec_store
from harness.dashboard.server import DashboardServer
from tests.complete_task import _interview, settle
from tests.test_dashboard import api, cli, needs_git, serve, start_task
from tests.test_dashboard_approval import (
    _approve,
    _assert_dashboard_execution_event,
    _codes,
    _comparable,
    _final,
    _rows,
    _sealed,
    _state_of,
    _tokens,
    _wait_for_execution,
)
from tests.test_dashboard_comments import _json, post
from tests.test_dashboard_diagrams import _with
from tests.test_dashboard_redesign import _compare, _move

pytestmark = needs_git

WRONG = "connection:item-store->exporter"
RIGHT = "connection:exporter->item-store"
WRONG_DESIGN = (
    "component Exporter\ncomponent Item Store\nconnect Item Store -> Exporter: writes rows"
)
FIXED_DESIGN = (
    "component Exporter\ncomponent Item Store\nconnect Exporter -> Item Store: writes rows"
)


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(DashboardServer, "poll_seconds", 0.05)


def wrong_connection_task(db_path: Path, repo: Path) -> str:
    """A task in the approval phase whose components entry draws the connection the wrong way."""
    task_id = start_task(db_path, "fixture", "export")
    cli(db_path, "investigate", task_id, "--repo", str(repo))
    cli(db_path, "phase", task_id, "next")
    _with(db_path, lambda c: _interview(c, task_id))
    cli(db_path, "phase", task_id, "next")
    cli(db_path, "design", "add", task_id, "components", WRONG_DESIGN)
    _with(db_path, lambda c: settle(c, task_id))
    cli(db_path, "phase", task_id, "next")
    return task_id


def _redesign(db_path: Path, *argv: str) -> str:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = redesign.main([*argv, "--db", str(db_path)])
    assert (code, err.getvalue()) == (0, ""), err.getvalue()
    return out.getvalue()


def _wrong_entry(db_path: Path, task_id: str) -> int:
    def step(controller):
        entries = controller.workspace(task_id).design_entries()
        [entry] = [e for e in entries if e.effective and e.text == WRONG_DESIGN]
        return entry.id

    return _with(db_path, step)


def harness_fixes(db_path: Path, task_id: str, fixed: list[int], answered: list[int]) -> list[str]:
    """The /harness redesign loop through the real entry points; the verdicts are scripted.

    Returns the redesign tool's output: the listing, then one `address` output per non-empty
    group (the design-change comments, then the clarifications answered without a change).
    """
    outputs = [_redesign(db_path, task_id)]
    reason = f"comment #{fixed[0]}"
    old = _wrong_entry(db_path, task_id)
    cli(db_path, "phase", task_id, "back", "design", "--reason", reason)
    out = cli(db_path, "design", "propose", task_id, "components", FIXED_DESIGN,
              "--rationale", reason)  # fmt: skip
    entry = re.match(r"E(\d+) pending", out).group(1)
    cli(db_path, "design", "decide", task_id, entry, "accept")
    cli(db_path, "design", "withdraw", task_id, str(old))
    for ids in (fixed, answered):
        if ids:
            outputs.append(_redesign(db_path, "address", task_id, *map(str, ids)))
    for _ in range(3):
        cli(db_path, "phase", task_id, "next")
    return outputs


def _comment(server, task_id: str, element: str, kind: str, text: str, rid: str) -> int:
    body = {"task": task_id, "version": 1, "element": element, "kind": kind, "text": text,
            "request_id": rid}  # fmt: skip
    status, data = _json(post(server, body))
    assert status == 201, data
    return data["comment"]["id"]


def _backup(source_path: Path, target_path: Path) -> None:
    source, target = sqlite3.connect(source_path), sqlite3.connect(target_path)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()


def test_the_wrong_connection_scenario_end_to_end(db_path, sample_repo, tmp_path):
    task_id = wrong_connection_task(db_path, sample_repo)
    specs, specs_cli = tmp_path / "specs", tmp_path / "specs-cli"
    with serve(db_path, task_id, str(specs)) as server:
        # a) diagram v1 shows the wrong connection; only comments will block
        task = api(server)["task"]
        assert task["diagrams"]["current"]["version"] == 1
        items = [i for scene in task["diagrams"]["current"]["diagrams"] for i in scene["items"]]
        connections = {i["id"] for i in items if i["kind"] == "connection"}
        assert WRONG in connections and RIGHT not in connections
        ready = _final(server)
        assert ready["phase"] == "approval" and ready["enabled"] is True
        assert ready["blockers"] == []

        # b) the developer comments; Approve & Implement is disabled and refused
        first = _comment(server, task_id, WRONG, "correction",
                         "fixture: the arrow points the wrong way", "req-0001")  # fmt: skip
        second = _comment(server, task_id, "component:exporter", "clarification",
                          "fixture: who owns the rows?", "req-0002")  # fmt: skip
        assert (first, second) == (1, 2)
        old = _tokens(server, task_id)
        final = _final(server)
        assert _codes(final) == ["comments"] and final["enabled"] is False
        assert final["comments"] == {"open": 2, "addressed": 0}
        before = _state_of(db_path, specs)
        status, data = _approve(server, old)
        assert _state_of(db_path, specs) == before
        assert status == 409 and "reload" not in data
        assert data["error"].startswith("2 diagram comment(s) are not resolved")

        # c) the harness fixes it through the redesign loop
        outputs = harness_fixes(db_path, task_id, fixed=[first], answered=[second])
        listed = outputs[0]
        assert (
            f"#{first} · correction · connection Item Store -> Exporter ({WRONG}) · version 1\n"
            "    fixture: the arrow points the wrong way\n"
        ) in listed
        assert f"#{second} · clarification · component Exporter (component:exporter)" in listed
        assert outputs[1:] == [
            f"recorded diagram version 2\n#{first} addressed in diagram version 2\n",
            f"#{second} addressed in diagram version 2\n",
        ]
        assert "no open comments" in _redesign(db_path, task_id)

        # d) the revision is shown; addressed comments still block
        task = api(server)["task"]
        assert task["diagrams"]["current"]["version"] == 2
        assert task["comments"]["addressed_in"] == {str(first): 2, str(second): 2}
        status, diff = _compare(server, "from=1&to=2")
        assert status == 200 and diff["marks"]["from"] == {WRONG: "removed"}
        assert diff["marks"]["to"] == {RIGHT: "added"}
        assert [(c["change"], c["id"]) for c in diff["changes"]] == [
            ("removed", WRONG), ("added", RIGHT)
        ]  # fmt: skip
        final = _final(server)
        assert final["phase"] == "approval" and _codes(final) == ["comments"]
        assert final["comments"] == {"open": 0, "addressed": 2}
        before = _state_of(db_path, specs)
        status, data = _approve(server, _tokens(server, task_id))
        assert _state_of(db_path, specs) == before
        assert status == 409 and "(open 0 · addressed 2)" in data["error"]

        # e) every comment, not some: resolving one keeps the button disabled
        status, data = _move(server, task_id, first)
        assert status == 200 and data["changed"] is True
        final = _final(server)
        assert _codes(final) == ["comments"] and final["comments"] == {"open": 0, "addressed": 1}
        assert _move(server, task_id, second)[0] == 200
        final = _final(server)
        assert final["enabled"] is True and final["blockers"] == []

        # f) a page still showing version 1 is refused with a reload hint
        before = _state_of(db_path, specs)
        status, data = _approve(server, old)
        assert _state_of(db_path, specs) == before
        assert (status, data["reload"]) == (409, True)

        # g) the dashboard approval writes the same snapshot as the CLI gate
        cli_db = tmp_path / "cli.db"
        _backup(db_path, cli_db)
        status, data = _approve(server, _tokens(server, task_id))
        assert status == 201 and data["created"] is True
        assert _wait_for_execution(db_path, task_id)["approval_id"] == data["approval"]["id"]
        out = cli(cli_db, "approve", task_id, "--specs", str(specs_cli))
        assert out == "".join(f"{line}\n" for line in data["output"]).replace(
            str(specs), str(specs_cli)
        )
        v1, v1_cli = specs / task_id / "v1.md", specs_cli / task_id / "v1.md"
        assert _sealed(v1) == _sealed(v1_cli)
        assert _comparable(db_path, str(specs_cli), str(specs)) == _comparable(
            cli_db, str(specs_cli), str(specs)
        )
        _assert_dashboard_execution_event(db_path, data["approval"]["id"])
        assert "execution.reserved" not in [row["kind"] for row in _rows(cli_db, "events")]

        # h) the unchanged CLI surface sees the approval; a replay writes nothing
        replay = _approve(server, _tokens(server, task_id))
        assert replay[0] == 200 and replay[1]["created"] is False

    def check(controller):
        store = spec_store(controller, task_id, str(specs))
        return require_approval(controller, task_id, store, sample_repo).id

    [row] = _rows(db_path, "approvals")
    assert _with(db_path, check) == row["id"] == data["approval"]["id"]
    resumed = cli(db_path, "resume", task_id, "--specs", str(specs))
    assert resumed.startswith(f"task {task_id} · phase approval")
    assert "next: harness execution reconnect " in resumed
    assert "v1 ok" in cli(db_path, "spec", "verify", task_id, "--specs", str(specs))
    assert sorted(p.name for p in (specs / task_id).iterdir()) == ["v1.md"]
