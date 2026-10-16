import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from harness.cli import main
from harness.controller import HarnessController
from harness.db import connect
from harness.decisions import DecisionStatus, load_decisions, load_history, parse_decision
from harness.registry import DecisionRegistry
from tests.sample_repo import APPROVED, make_sample_repo, tree_snapshot, write_files

REPO_ROOT = Path(__file__).resolve().parents[1]
OWN_DECISIONS = REPO_ROOT / "docs" / "decisions"
OWN_SNAPSHOT = tree_snapshot(OWN_DECISIONS)
TASK_ID = re.compile(r"task (t-[0-9a-f]{8}) created")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.fixture
def run(db_path, capsys):
    def _run(*argv: str) -> tuple[int, str, str]:
        code = main(["--db", str(db_path), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


@pytest.fixture
def task(run, sample_repo):
    code, out, _ = run("start", "--complexity", "trivial", "add", "csv", "export")
    assert code == 0
    task_id = TASK_ID.search(out).group(1)
    assert run("investigate", task_id, "--repo", str(sample_repo))[0] == 0
    assert run("phase", task_id, "next")[0] == 0
    return task_id


@pytest.fixture
def events(db_path):
    def _events(task_id: str) -> list:
        conn = connect(db_path)
        try:
            return HarnessController(conn).workspace(task_id).events()
        finally:
            conn.close()

    return _events


def _ask(run, task_id, *flags):
    code, out, _ = run("interview", "ask", task_id, "security", "Who", "may", "export?", *flags)
    assert code == 0
    return re.match(r"Q(\d+) added", out).group(1)


def _propose(run, task_id, qid, *extra):
    code, out, _ = run(
        "interview", "propose", task_id, qid,
        "--title", "Export access", "--recommendation", "Admins only",
        "--rationale", "Least privilege", *extra,
    )  # fmt: skip
    assert code == 0
    return re.match(r"P(\d+) pending", out).group(1)


def test_accepting_a_decision_recommendation_records_it(run, task, sample_repo, events):
    decisions = sample_repo / "docs" / "decisions"
    pid = _propose(
        run, task, _ask(run, task, "--decision"), "--impact", "Exports need a role check"
    )
    code, out, err = run("interview", "decide", task, pid, "accept")
    first, second = out.splitlines()
    path = decisions / "DEC-0003-export-access.md"
    assert (code, err, first) == (0, "", f"P{pid} accepted")
    assert second == f"recorded DEC-0003 v1 -> {path}"
    record = parse_decision(path)
    assert (record.status, record.version, record.scope, record.supersedes) == (
        DecisionStatus.APPROVED,
        1,
        "security",
        None,
    )
    assert (record.decision, record.rationale, record.impact) == (
        "Admins only",
        "Least privilege",
        "Exports need a role check",
    )
    [recorded] = [e for e in events(task) if e.kind == "decision.recorded"]
    assert recorded.payload == {
        "id": "DEC-0003",
        "version": 1,
        "path": str(path),
        "source": "interview",
        "proposal": int(pid),
    }


def test_only_approved_decision_questions_are_recorded(run, task, sample_repo):
    before = tree_snapshot(sample_repo)
    rejected = _propose(run, task, _ask(run, task, "--decision"))
    assert run("interview", "decide", task, rejected, "reject")[1] == f"P{rejected} rejected\n"
    plain = _propose(run, task, _ask(run, task))
    assert run("interview", "decide", task, plain, "accept")[1] == f"P{plain} accepted\n"
    assert tree_snapshot(sample_repo) == before

    modified = _propose(run, task, _ask(run, task, "--decision"))
    code, out, _ = run("interview", "decide", task, modified, "modify", "--response", "Owners")
    assert code == 0 and out.splitlines()[1].startswith("recorded DEC-0003 v1 -> ")
    record = DecisionRegistry(sample_repo / "docs" / "decisions").get("DEC-0003")
    assert record.decision == "Owners" and record.impact == "Not stated."
    assert record.rationale == (
        "Least privilege\n\nModified by the developer from the recommendation: Admins only"
    )


def test_unwritable_registry_keeps_the_proposal_pending(run, task, tmp_path, sample_repo):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    pid = _propose(run, task, _ask(run, task, "--decision"))
    code, _, err = run("interview", "decide", task, pid, "accept", "--decisions", str(blocker))
    assert code == 1 and err.startswith("harness interview: ")
    code, out, _ = run("interview", "decide", task, pid, "accept")
    assert code == 0 and "recorded DEC-0003 v1" in out


def test_developer_stated_decision_is_recorded(run, task, sample_repo, events):
    code, out, _ = run(
        "decision", "record", task, "--title", "Export format",
        "--decision", "CSV only", "--rationale", "Spreadsheet users", "--scope", "exports",
    )  # fmt: skip
    path = sample_repo / "docs" / "decisions" / "DEC-0003-export-format.md"
    assert (code, out) == (0, f"DEC-0003 v1 recorded -> {path}\n")
    assert parse_decision(path).scope == "exports"
    assert [e.payload["source"] for e in events(task) if e.kind == "decision.recorded"] == [
        "developer"
    ]


def _change(run, task, decision_id="DEC-0001", text="Store exports in SQLite"):
    return run(
        "decision", "change", task, decision_id, "--decision", text,
        "--rationale", "Queries", "--impact", "Exports need a migration",
    )  # fmt: skip


def test_change_shows_the_original_and_writes_nothing_until_approved(
    run, task, sample_repo, events
):
    decisions = sample_repo / "docs" / "decisions"
    qid = _ask(run, task)
    assert run("interview", "skip", task, qid, "--source", "decision", "--ref", "DEC-0001")[0] == 0
    before = tree_snapshot(sample_repo)
    code, out, _ = _change(run, task)
    assert code == 0
    assert out.splitlines() == [
        "DEC-0001 v1 — Storage engine (approved 2026-01-01)",
        "original decision: Store exports as files.",
        "original rationale: Simple.",
        "original impact: None.",
        f"referenced by: Q{qid} (skipped by DEC-0001)",
        "proposed v2: Store exports in SQLite",
        "proposed rationale: Queries",
        "consequences of the change: Exports need a migration",
        f"C1 pending — approve: harness decision decide {task} 1 accept|reject|modify",
    ]
    code, _, err = _change(run, task)
    assert code == 1 and "DEC-0001 already has a pending change C1; decide it first" in err
    code, _, err = _change(run, task, "DEC-0002")
    assert code == 1 and "DEC-0002 is superseded; only approved decisions can be changed" in err
    assert _change(run, task, "DEC-0099")[0] == 1
    assert run("decision", "decide", task, "1", "modify")[0] == 1
    assert tree_snapshot(sample_repo) == before

    code, out, _ = run("decision", "decide", task, "1", "accept")
    kept = decisions / "history" / "DEC-0001-v1-storage.md"
    assert (code, out) == (
        0,
        f"C1 accepted — DEC-0001 v2 recorded -> {decisions / 'DEC-0001-storage.md'};"
        f" v1 kept in {kept}\n",
    )
    assert parse_decision(kept).status is DecisionStatus.SUPERSEDED
    assert run("decision", "decide", task, "1", "reject")[2].endswith("C1 is already accepted\n")
    kinds = [e.kind for e in events(task) if e.kind.startswith("decision.")]
    assert kinds == ["decision.change_proposed", "decision.superseded"]

    after_accept = tree_snapshot(sample_repo)
    assert _change(run, task, text="Store exports in Postgres")[0] == 0
    assert run("decision", "decide", task, "2", "reject")[1] == "C2 rejected\n"
    assert tree_snapshot(sample_repo) == after_accept


def test_change_refused_when_the_record_moved_on(run, task, sample_repo):
    registry = DecisionRegistry(sample_repo / "docs" / "decisions")
    assert _change(run, task)[0] == 0
    registry.supersede("DEC-0001", "Storage engine", "Elsewhere", "r", "", "", 1)
    before = tree_snapshot(sample_repo)
    code, _, err = run("decision", "decide", task, "1", "accept")
    assert code == 1 and "DEC-0001 changed since the proposal (now v2)" in err
    assert tree_snapshot(sample_repo) == before
    assert run("decision", "decide", task, "1", "reject")[1] == "C1 rejected\n"


def test_latest_by_default_history_on_request(run, task, sample_repo):
    decisions = str(sample_repo / "docs" / "decisions")
    assert _change(run, task)[0] == 0
    assert run("decision", "decide", task, "1", "modify", "--response", "Use SQLite")[0] == 0
    code, out, _ = run("decision", "list", "--decisions", decisions)
    assert (code, out.splitlines()) == (
        0,
        [
            "DEC-0001 v2 approved — Storage engine (supersedes v1)",
            "DEC-0002 v1 superseded — Storage engine",
        ],
    )
    out = run("decision", "show", "DEC-0001", "--decisions", decisions)[1]
    assert "DEC-0001 v2" in out and "decision: Use SQLite" in out and "v1 —" not in out
    shown = json.loads(
        run("decision", "show", "DEC-0001", "--history", "--json", "--decisions", decisions)[1]
    )
    assert [(r["version"], r["status"], r["superseded_by"]) for r in shown] == [
        (1, "superseded", "DEC-0001 v2"),
        (2, "approved", None),
    ]
    assert shown[1]["rationale"].endswith("from the proposed change: Store exports in SQLite")
    listed = json.loads(run("decision", "list", "--json", "--decisions", decisions)[1])
    assert [(r["id"], r["version"]) for r in listed] == [("DEC-0001", 2), ("DEC-0002", 1)]
    assert run("decision", "show", "DEC-0099", "--decisions", decisions)[0] == 1
    assert run("decision", "list", "--decisions", str(sample_repo / "nope"))[1] == "no decisions\n"


@needs_git
def test_fresh_clone_reads_every_approved_decision_and_the_chain(run, task, sample_repo, tmp_path):
    assert _change(run, task)[0] == 0
    assert run("decision", "decide", task, "1", "accept")[0] == 0
    assert _change(run, task, text="Store exports in Postgres")[0] == 0
    assert run("decision", "decide", task, "2", "accept")[0] == 0
    assert (
        run(
            "decision", "record", task, "--title", "Format", "--decision", "CSV", "--rationale", "r"
        )[0]
        == 0
    )
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]
    for args in (["add", "-A"], ["commit", "-q", "-m", "decisions"]):
        subprocess.run([*git, *args], cwd=sample_repo, check=True, capture_output=True)
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(sample_repo), str(clone)], check=True, capture_output=True
    )

    def view(root: Path) -> list[tuple]:
        directory = root / "docs" / "decisions"
        records = [*load_decisions(directory), *load_history(directory)]
        return sorted((r.id, r.version, r.status, r.decision, r.supersedes) for r in records)

    assert view(clone) == view(sample_repo)
    approved = [r for r in load_decisions(clone / "docs" / "decisions") if r.status == "approved"]
    assert [(r.id, r.version) for r in approved] == [("DEC-0001", 3), ("DEC-0003", 1)]
    chain = DecisionRegistry(clone / "docs" / "decisions").chain("DEC-0001")
    assert [r.decision for r in chain] == [
        "Store exports as files.",
        "Store exports in SQLite",
        "Store exports in Postgres",
    ]


def test_suggestions_never_apply(run, task, sample_repo, tmp_path, events):
    other = make_sample_repo(tmp_path / "other", with_git=False)
    write_files(
        other,
        {
            "docs/decisions/DEC-0007-csv.md": APPROVED.replace("DEC-0001", "DEC-0007").replace(
                "Storage engine", "CSV export encoding"
            ),
            "docs/decisions/DEC-0008-broken.md": "# DEC-0008 — broken\n",
        },
    )
    before = (tree_snapshot(sample_repo), tree_snapshot(other), len(events(task)))
    code, out, _ = run(
        "decision", "suggest", task, "--from", str(other), "--from", str(sample_repo)
    )
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "suggestions only — nothing is applied:"
    assert lines[1].startswith(f"  DEC-0007 v1 (from {other / 'docs' / 'decisions'}) — CSV export")
    assert lines[1].endswith("[keywords: csv, export]")
    assert any(line.startswith("note: skipped") and "DEC-0008-broken.md" in line for line in lines)
    assert any("it is this task's own registry" in line for line in lines)
    assert lines[-1].startswith("to adopt one, propose it in the interview")
    data = json.loads(run("decision", "suggest", task, "--from", str(other), "--json")[1])
    assert data["applied"] is False
    assert [s["id"] for s in data["suggestions"]] == ["DEC-0007", "DEC-0001"]
    assert (tree_snapshot(sample_repo), tree_snapshot(other), len(events(task))) == before
    assert run("decision", "suggest", task, "--from", str(tmp_path / "none"))[1].startswith("none")


def test_decision_commands_never_touch_this_repository():
    assert tree_snapshot(OWN_DECISIONS) == OWN_SNAPSHOT
