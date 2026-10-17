"""P2-03: the spec & decisions view — rendered spec, version history with diffs, decisions."""

import json
from pathlib import Path

import pytest

from harness.controller import HarnessController
from harness.dashboard.markdown import inline_spans, parse_markdown
from harness.dashboard.server import DashboardServer
from harness.db import connect
from harness.interview import InterviewEngine
from harness.interview_report import ChecklistState
from harness.spec_record import SpecStatus, render_spec
from harness.spec_store import SpecStore
from tests.complete_task import _design, _interview
from tests.sample_repo import tree_snapshot
from tests.test_dashboard import (
    _db_dump,
    _every_route,
    api,
    cli,
    fetch,
    needs_git,
    serve,
    start_task,
)
from tests.test_dashboard_overview import _answered, _investigated, _propose, _with_controller
from tests.test_dashboard_stream import frame, stream

REPO = Path(__file__).resolve().parents[1]
REAL_SPEC = REPO / "docs" / "specs" / "t-a3752edf" / "v1.md"
VERDICTS = "accept|reject|modify"
BODY = "## Requirements\n\n- Q1 [scope] keep **rows** in `csv`\n- ++x\n"


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(DashboardServer, "poll_seconds", 0.05)


def _spec_text(task_id: str, status: str, version: int | None, body: str, day: int = 1) -> str:
    supersedes = version - 1 if version and version > 1 else None
    stamp = f"2026-01-0{day}T00:00:00+00:00"
    return render_spec(
        task_id, "csv export", SpecStatus(status), version, stamp, "trivial (developer)",
        "abc123", "clean", supersedes, body,
    )  # fmt: skip


def _write(specs: Path, task_id: str, name: str, text: str) -> Path:
    path = specs / task_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _versions(specs: Path, task_id: str, *bodies: str) -> SpecStore:
    """v1..vN with these bodies; each earlier version superseded by the next, as approve does."""
    store = SpecStore(specs / task_id)
    for number, body in enumerate(bodies, start=1):
        if number > 1:
            store.supersede_header(store.records()[-1], number)
        _write(
            specs, task_id, f"v{number}.md", _spec_text(task_id, "approved", number, body, number)
        )
    return store


def _markdown_back(blocks: list[dict]) -> list[str]:
    """The source lines from the tree, re-adding the markers each span style stands for."""
    marks = {None: "", "strong": "**", "code": "`"}

    def line(spans: list[dict]) -> str:
        return "".join(f"{marks[s['style']]}{s['text']}{marks[s['style']]}" for s in spans)

    lines: list[str] = []
    for block in blocks:
        if block["type"] == "heading":
            lines.append("#" * block["level"] + " " + line(block["spans"]))
        elif block["type"] == "list":
            for item in block["items"]:
                first, *rest = item["lines"]
                lines += [f"- {line(first)}", *(f"  {line(more)}" for more in rest)]
        else:
            lines += [line(spans) for spans in block["lines"]]
    return lines


# T1 Markdown subset


def test_inline_spans_code_bold_and_literal_markers():
    assert inline_spans("- **Task:** t-1") == [
        {"text": "- ", "style": None},
        {"text": "Task:", "style": "strong"},
        {"text": " t-1", "style": None},
    ]
    assert inline_spans("run `**not bold**` then ``a`b``") == [
        {"text": "run ", "style": None},
        {"text": "**not bold**", "style": "code"},
        {"text": " then ", "style": None},
        {"text": "a`b", "style": "code"},
    ]
    for literal in ("a ** b ** c", "**", "`open", "2 * 3", "<img src=x onerror=alert(1)>"):
        assert inline_spans(literal) == [{"text": literal, "style": None}]
    assert inline_spans("") == []


def test_parse_markdown_blocks():
    text = (
        "# Title\r\n\r\n#no heading\r\n- **A:** one\r\n  more `x`\r\n- two\r\nafter\r\n"
        "\r\n###   Deep   \r\npara one\r\n  indented\r\n"
    )
    assert parse_markdown(text) == [
        {"type": "heading", "level": 1, "spans": [{"text": "Title", "style": None}]},
        {"type": "paragraph", "lines": [[{"text": "#no heading", "style": None}]]},
        {
            "type": "list",
            "items": [
                {
                    "lines": [
                        [{"text": "A:", "style": "strong"}, {"text": " one", "style": None}],
                        [{"text": "more ", "style": None}, {"text": "x", "style": "code"}],
                    ]
                },
                {"lines": [[{"text": "two", "style": None}]]},
            ],
        },
        {"type": "paragraph", "lines": [[{"text": "after", "style": None}]]},
        {"type": "heading", "level": 3, "spans": [{"text": "Deep", "style": None}]},
        {
            "type": "paragraph",
            "lines": [
                [{"text": "para one", "style": None}],
                [{"text": "  indented", "style": None}],
            ],
        },
    ]


def test_the_real_phase_2_spec_loses_no_text():
    text = REAL_SPEC.read_text(encoding="utf-8")
    blocks = parse_markdown(text)
    assert _markdown_back(blocks) == [line for line in text.splitlines() if line.strip()]
    styles = {s["style"] for b in blocks if b["type"] == "list" for i in b["items"]
              for spans in i["lines"] for s in spans}  # fmt: skip
    assert styles == {None, "strong", "code"}
    assert [b["level"] for b in blocks if b["type"] == "heading"][:2] == [1, 2]


# T2 shown spec and empty cases


def test_shown_spec_prefers_approved_then_draft_then_any_version(db_path, tmp_path):
    task_id = start_task(db_path, "csv", "export")
    specs = tmp_path / "specs"
    with serve(db_path, task_id, str(specs)) as server:
        empty = api(server)["task"]["spec"]
        _write(specs, task_id, "draft.md", _spec_text(task_id, "draft", None, BODY))
        draft = api(server)["task"]["spec"]
        store = _versions(specs, task_id, BODY)
        approved = api(server)["task"]["spec"]
        store.supersede_header(store.records()[0], 2)
        superseded_only = api(server)["task"]["spec"]
    assert (empty["shown"], empty["history"], empty["unavailable"]) == (None, [], None)
    assert empty["versions"] == [] and empty["draft"] is False
    assert (draft["shown"]["label"], draft["shown"]["status"]) == ("draft", "draft")
    assert draft["shown"]["version"] is None
    assert draft["shown"]["blocks"] == parse_markdown((specs / task_id / "draft.md").read_text())
    shown = approved["shown"]
    assert (shown["label"], shown["status"], shown["version"]) == ("v1", "approved", 1)
    assert shown["path"] == str(specs / task_id / "v1.md")
    assert shown["blocks"][0]["spans"] == [{"text": "Specification — csv export", "style": None}]
    assert superseded_only["shown"]["label"] == "draft"
    (specs / task_id / "draft.md").unlink()
    with serve(db_path, task_id, str(specs)) as server:
        last = api(server)["task"]["spec"]
    assert (last["shown"]["label"], last["shown"]["status"]) == ("v1", "superseded")


# T3 history and diffs


def test_history_lists_every_version_with_its_body_diff(db_path, tmp_path):
    task_id = start_task(db_path, "csv", "export")
    specs = tmp_path / "specs"
    second = BODY.replace("- ++x\n", "- --x\n- added\n")
    _versions(specs, task_id, BODY, second)
    _write(specs, task_id, "draft.md", _spec_text(task_id, "draft", None, second, 3))
    listed = json.loads(cli(db_path, "spec", "list", task_id, "--specs", str(specs), "--json"))
    with serve(db_path, task_id, str(specs)) as server:
        spec = api(server)["task"]["spec"]
    history = spec["history"]
    assert [e["label"] for e in history] == ["v1", "v2", "draft"]
    assert [{k: v for k, v in e.items() if k not in ("label", "diff")} for e in history] == listed
    assert history[0]["diff"] is None and history[0]["superseded_by"] == 2
    diff = history[1]["diff"]
    assert (diff["from"], diff["to"], diff["added"], diff["removed"]) == ("v1", "v2", 2, 1)
    assert diff["lines"] == [
        {"kind": "hunk", "text": "@@ -1,4 +1,5 @@"},
        {"kind": "context", "text": " ## Requirements"},
        {"kind": "context", "text": " "},
        {"kind": "context", "text": " - Q1 [scope] keep **rows** in `csv`"},
        {"kind": "remove", "text": "-- ++x"},
        {"kind": "add", "text": "+- --x"},
        {"kind": "add", "text": "+- added"},
    ]
    draft = history[2]["diff"]
    assert (draft["from"], draft["to"], draft["lines"]) == ("v2", "draft", [])
    assert (draft["added"], draft["removed"]) == (0, 0)
    assert spec["shown"]["label"] == "v2"


def test_diff_markers_in_the_body_are_kept(db_path, tmp_path):
    task_id = start_task(db_path, "csv", "export")
    specs = tmp_path / "specs"
    old, new = "## A\n++x\n--y\n", "## A\n--x\n++y\n"
    _versions(specs, task_id, old, new)
    with serve(db_path, task_id, str(specs)) as server:
        diff = api(server)["task"]["spec"]["history"][1]["diff"]
    kinds = [(line["kind"], line["text"]) for line in diff["lines"]]
    assert kinds == [
        ("hunk", "@@ -1,3 +1,3 @@"),
        ("context", " ## A"),
        ("remove", "-++x"),
        ("remove", "---y"),
        ("add", "+--x"),
        ("add", "+++y"),
    ]


def test_a_version_gap_diffs_against_the_previous_file(db_path, tmp_path):
    task_id = start_task(db_path, "csv", "export")
    specs = tmp_path / "specs"
    _write(specs, task_id, "v1.md", _spec_text(task_id, "approved", 1, BODY))
    _write(specs, task_id, "v3.md", _spec_text(task_id, "approved", 3, BODY, 3))
    with serve(db_path, task_id, str(specs)) as server:
        spec = api(server)["task"]["spec"]
    assert [e["label"] for e in spec["history"]] == ["v1", "v3"]
    diff = spec["history"][1]["diff"]
    assert (diff["from"], diff["lines"]) == ("v1", [])
    assert spec["shown"]["label"] == "v3"


def test_a_draft_without_versions_has_nothing_to_compare(db_path, tmp_path):
    task_id = start_task(db_path, "csv", "export")
    specs = tmp_path / "specs"
    _write(specs, task_id, "draft.md", _spec_text(task_id, "draft", None, BODY))
    with serve(db_path, task_id, str(specs)) as server:
        [entry] = api(server)["task"]["spec"]["history"]
    assert (entry["label"], entry["version"], entry["diff"]) == ("draft", None, None)


# T5 isolation


def test_a_malformed_draft_marks_the_spec_section_only(db_path, tmp_path):
    task_id = start_task(db_path, "csv", "export")
    specs = tmp_path / "specs"
    _versions(specs, task_id, BODY)
    draft = _write(specs, task_id, "draft.md", "not a specification\n")
    with serve(db_path, task_id, str(specs)) as server:
        spec = api(server)["task"]["spec"]
        draft.unlink()
        _write(specs, task_id, "v2.md", "not a specification\n")
        status, _, body = fetch(server, "/api/dashboard")
    assert spec["unavailable"].startswith(str(draft)) and "missing field" in spec["unavailable"]
    assert spec["draft"] is True and spec["shown"]["label"] == "v1"
    assert [e["label"] for e in spec["history"]] == ["v1"]
    assert status == 500 and set(json.loads(body)) == {"error"}


@needs_git
def test_a_malformed_decision_marks_the_approved_list_only(db_path, sample_repo):
    task_id = _investigated(db_path, sample_repo)
    _propose(db_path, task_id)
    bad = sample_repo / "docs" / "decisions" / "DEC-0009-bad.md"
    bad.write_text("not a decision\n", encoding="utf-8")
    with serve(db_path, task_id) as server:
        data = api(server)
    decisions = data["task"]["decisions"]
    assert decisions["approved"] == [] and decisions["unavailable"].startswith(str(bad))
    assert [i["ref"] for i in decisions["open"]] == _checklist_refs(db_path, task_id)
    assert f"harness interview decide {task_id} 1 {VERDICTS}" in [
        i["command"] for i in decisions["open"]
    ]
    assert data["task"]["status"] == "awaiting approval"


# T4 approved decisions


@needs_git
def test_approved_decisions_match_the_registry(db_path, sample_repo):
    task_id = _investigated(db_path, sample_repo)
    registry = str(sample_repo / "docs" / "decisions")
    with serve(db_path, task_id) as server:
        before = api(server)["task"]["decisions"]
        cli(
            db_path, "decision", "change", task_id, "DEC-0001", "--decision", "Store in SQLite",
            "--rationale", "Queries", "--impact", "A migration",
        )  # fmt: skip
        cli(db_path, "decision", "decide", task_id, "1", "accept")
        cli(
            db_path, "decision", "record", task_id, "--title", "Export format",
            "--decision", "CSV only", "--rationale", "Asked for",
        )  # fmt: skip
        after = api(server)["task"]["decisions"]
    listed = json.loads(cli(db_path, "decision", "list", "--decisions", registry, "--json"))
    assert before["directory"] == registry and before["unavailable"] is None
    assert [(r["id"], r["version"], r["recorded_by_task"]) for r in before["approved"]] == [
        ("DEC-0001", 1, False)
    ]
    approved = [r for r in listed if r["status"] == "approved"]
    assert (
        [(r["id"], r["version"]) for r in after["approved"]]
        == [(r["id"], r["version"]) for r in approved]
        == [("DEC-0001", 2), ("DEC-0003", 1)]
    )
    first, second = after["approved"]
    assert (first["supersedes"], first["decision"], first["recorded_by_task"]) == (
        "DEC-0001 v1",
        "Store in SQLite",
        True,
    )
    assert (second["title"], second["decision"], second["recorded_by_task"]) == (
        "Export format",
        "CSV only",
        True,
    )
    for shown, cli_record in zip(after["approved"], approved, strict=True):
        keys = ("id", "title", "status", "version", "timestamp", "scope", "supersedes", "path")
        assert {k: shown[k] for k in (*keys, "decision")} == {
            k: cli_record[k] for k in (*keys, "decision")
        }
    assert "DEC-0002" not in [r["id"] for r in after["approved"]]


# T6 open decisions


def _checklist_refs(db_path: Path, task_id: str) -> list[str]:
    conn = connect(db_path)
    try:
        items = InterviewEngine(HarnessController(conn), task_id).checklist().items
    finally:
        conn.close()
    return [
        i.ref for i in items if i.state is ChecklistState.AWAITING_DECISION or i.kind == "decision"
    ]


def _open_and_pending(server) -> tuple[list[dict], set[str]]:
    task = api(server)["task"]
    pending = {
        p["command"]
        for p in task["pending_approvals"]
        if p["kind"] not in ("test plan", "final approval")
    }
    return task["decisions"]["open"], pending


@needs_git
def test_open_decisions_match_the_engines(db_path, sample_repo):
    task_id = _investigated(db_path, sample_repo)
    cli(db_path, "interview", "ask", task_id, "scope", "Plain", "question?")
    qid = _propose(db_path, task_id)
    cli(
        db_path, "decision", "change", task_id, "DEC-0001", "--decision", "Store in SQLite",
        "--rationale", "Queries", "--impact", "A migration",
    )  # fmt: skip
    with serve(db_path, task_id) as server:
        items, pending = _open_and_pending(server)
        refs = [i["ref"] for i in items]
        assert refs == [*_checklist_refs(db_path, task_id), "C1"]
        [question] = [i for i in items if i["ref"] == f"Q{qid}"]
        assert question == {
            "area": "interview",
            "ref": f"Q{qid}",
            "state": "awaiting_decision",
            "text": "Who may export?",
            "recommendation": "P1 Export access: Admins only",
            "command": f"harness interview decide {task_id} 1 {VERDICTS}",
        }
        assert "Plain question?" not in [i["text"] for i in items]
        assert items[-1] == {
            "area": "decision",
            "ref": "C1",
            "state": "pending",
            "text": "DEC-0001 v1 -> v2: Storage engine",
            "recommendation": None,
            "command": f"harness decision decide {task_id} 1 {VERDICTS}",
        }
        assert {i["command"] for i in items if i["command"]} == pending

        cli(db_path, "decision", "decide", task_id, "1", "reject")
        cli(db_path, "interview", "decide", task_id, "1", "reject")
        items, pending = _open_and_pending(server)
        assert "C1" not in [i["ref"] for i in items] and pending == set()
        [question] = [i for i in items if i["ref"] == f"Q{qid}"]
        assert (question["state"], question["command"]) == ("recommendation_rejected", None)

        _with_controller(db_path, _interview, task_id)
        cli(db_path, "phase", task_id, "next")
        cli(
            db_path, "design", "propose", task_id, "components", "One", "module", "--rationale", "r"
        )
        items, pending = _open_and_pending(server)
        assert items == [
            {
                "area": "design",
                "ref": "E1",
                "state": "awaiting_decision",
                "text": "One module",
                "recommendation": None,
                "command": f"harness design decide {task_id} 1 {VERDICTS}",
            }
        ]
        assert {i["command"] for i in items} == pending
        cli(db_path, "design", "decide", task_id, "1", "reject")
        _with_controller(db_path, _design, task_id)
        cli(db_path, "phase", task_id, "next")
        assert api(server)["task"]["decisions"]["open"] == []
        covers = f"Q{_answered(db_path, task_id)}"
        scenario = ("unit", "Rows", "--rationale", "r", "--covers", covers)
        cli(db_path, "testplan", "propose", task_id, *scenario)
        items, pending = _open_and_pending(server)
    assert [(i["area"], i["ref"], i["text"], i["command"]) for i in items] == [
        ("testplan", "S1", "Rows", f"harness testplan decide {task_id} 1 {VERDICTS}")
    ]
    assert {i["command"] for i in items} == pending


# T7 live push


@needs_git
def test_spec_and_decision_changes_are_pushed_live(db_path, sample_repo, tmp_path):
    task_id = _investigated(db_path, sample_repo)
    specs = tmp_path / "specs"
    with serve(db_path, task_id, str(specs)) as server, stream(server) as response:
        frame(response)
        first = json.loads(frame(response)["data"])["task"]
        assert first["spec"]["shown"] is None
        _write(specs, task_id, "draft.md", _spec_text(task_id, "draft", None, BODY))
        pushed = json.loads(frame(response)["data"])
        assert pushed == api(server, "")
        assert pushed["task"]["spec"]["shown"]["label"] == "draft"
        cli(
            db_path, "decision", "record", task_id, "--title", "Export format",
            "--decision", "CSV only", "--rationale", "Asked for",
        )  # fmt: skip
        recorded = json.loads(frame(response)["data"])["task"]["decisions"]
    assert "DEC-0003" in [r["id"] for r in recorded["approved"]]


# T8 writes nothing


@needs_git
def test_the_spec_and_decisions_view_writes_nothing(db_path, sample_repo, isolated_home, tmp_path):
    task_id = _investigated(db_path, sample_repo)
    _propose(db_path, task_id)
    specs = tmp_path / "specs"
    _versions(specs, task_id, BODY, BODY + "- more\n")
    _write(specs, task_id, "draft.md", _spec_text(task_id, "draft", None, BODY, 3))
    snapshots = (tree_snapshot(sample_repo), tree_snapshot(specs), tree_snapshot(isolated_home))
    dump = _db_dump(db_path)
    with serve(db_path, task_id, str(specs)) as server:
        server.stream_lifetime = 0.3
        for path in _every_route(task_id):
            fetch(server, path)
        data = api(server)
    assert data["task"]["spec"]["shown"]["label"] == "v2"
    assert data["task"]["decisions"]["open"]
    assert _db_dump(db_path) == dump
    assert (tree_snapshot(sample_repo), tree_snapshot(specs), tree_snapshot(isolated_home)) == (
        snapshots
    )
