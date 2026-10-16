import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from harness.cli import main
from harness.controller import HarnessController
from harness.db import connect
from harness.spec_document import SECTIONS
from harness.spec_record import parse_spec, split_body
from harness.spec_store import SpecStore
from tests.sample_repo import make_sample_repo, tree_snapshot

REPO_ROOT = Path(__file__).resolve().parents[1]
OWN_DOCS = REPO_ROOT / "docs"
OWN_SNAPSHOT = tree_snapshot(OWN_DOCS)
TASK_ID = re.compile(r"task (t-[0-9a-f]{8}) created")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
GIT = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]


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
    _ok(run, "phase", task, "next")
    out = _ok(run, "interview", "ask", task, "acceptance_criteria", "When", "is", "it", "done?")
    qid = re.match(r"Q(\d+) added", out).group(1)
    _ok(run, "interview", "answer", task, qid, "rows", "export", "as", "csv")
    return task


def _to_approval(run, task):
    for _ in range(4):
        _ok(run, "phase", task, "next")


def _db(db_path, task):
    conn = connect(db_path)
    try:
        ws = HarnessController(conn).workspace(task)
        return ws.events(), ws.spec_versions(), ws.checkpoints()
    finally:
        conn.close()


def _head(repo):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


@needs_git
def test_scripted_draft_and_approval(run, sample_repo, db_path):
    task = _task(run, sample_repo)
    specs = sample_repo / "docs" / "specs" / task
    before, events = tree_snapshot(sample_repo), len(_db(db_path, task)[0])
    shown = _ok(run, "spec", "show", task)
    assert (tree_snapshot(sample_repo), len(_db(db_path, task)[0])) == (before, events)
    headings = [line[3:] for line in shown.splitlines() if line.startswith("## ")]
    assert headings == list(SECTIONS)
    assert re.search(
        r"^## Acceptance criteria\n\n"
        r"- Q\d+ \[acceptance_criteria\] rows export as csv \(developer\)$",
        shown,
        re.M,
    )
    assert "## Test plan\n\n- (no approved test plan recorded)\n" in shown
    assert "- **Status:** draft\n" in shown and f"- **Revision:** {_head(sample_repo)}\n" in shown
    assert "- DEC-0001 v1 — Storage engine (Persistence)" in shown

    assert _ok(run, "spec", "draft", task).strip() == f"draft -> {specs / 'draft.md'}"
    assert [p.name for p in specs.iterdir()] == ["draft.md"]
    snapshot = tree_snapshot(sample_repo)
    code, _, err = run("spec", "approve", task)
    assert code == 1 and f"is in phase interview; run: harness phase {task} next" in err
    assert tree_snapshot(sample_repo) == snapshot and _db(db_path, task)[1] == []

    _to_approval(run, task)
    out = _ok(run, "spec", "approve", task)
    record = parse_spec(specs / "v1.md")
    assert out.strip() == (
        f"approved v1 -> {specs / 'v1.md'} (revision {_head(sample_repo)}, {record.working_tree})"
    )
    assert [p.name for p in specs.iterdir()] == ["v1.md"]
    assert (record.status, record.version, record.revision) == ("approved", 1, _head(sample_repo))
    body = split_body((specs / "v1.md").read_bytes().decode())[1]
    assert record.content_hash == "sha256:" + hashlib.sha256(body.encode()).hexdigest()
    events, rows, checkpoints = _db(db_path, task)
    assert [(r.version, r.revision, r.content_hash, r.status) for r in rows] == [
        (1, record.revision, record.content_hash, "approved")
    ]
    kinds = [e.kind for e in events]
    assert kinds.count("spec.drafted") == 1 and kinds[-1] == "spec.approved"
    assert events[-1].payload["content_hash"] == record.content_hash
    assert checkpoints[-1].label == "spec" and checkpoints[-1].payload["version"] == 1
    assert checkpoints[-1].phase == "approval"


@needs_git
def test_approved_file_is_never_overwritten_and_tampering_is_detected(
    run, sample_repo, db_path, monkeypatch
):
    task = _task(run, sample_repo)
    _to_approval(run, task)
    _ok(run, "spec", "approve", task)
    v1 = sample_repo / "docs" / "specs" / task / "v1.md"
    sealed, rows = v1.read_bytes(), _db(db_path, task)[1]
    assert _ok(run, "spec", "verify", task) == "v1 ok\n"

    monkeypatch.setattr(SpecStore, "next_version", lambda self: 1)
    code, _, err = run("spec", "approve", task)
    assert code == 1 and "already exists; refusing to overwrite it" in err
    monkeypatch.undo()
    assert v1.read_bytes() == sealed and _db(db_path, task)[1] == rows
    assert [p.name for p in v1.parent.iterdir()] == ["v1.md"]

    v1.write_bytes(sealed.replace(b"rows export as csv", b"rows export as xml"))
    code, out, _ = run("spec", "verify", task)
    assert (code, out) == (1, "v1 MODIFIED (content hash mismatch)\n")
    v1.write_bytes(sealed.replace(rows[0].revision.encode(), b"0" * 40))
    code, out, _ = run("spec", "verify", task)
    assert (code, out) == (1, "v1 MODIFIED (differs from the local approval record)\n")
    v1.write_bytes(sealed)
    assert _ok(run, "spec", "verify", task) == "v1 ok\n"
    v1.write_bytes(sealed.replace(b"- **Task:**", b"- Task:"))
    code, out, _ = run("spec", "verify", task)
    assert code == 1 and out.startswith("v1 MODIFIED (cannot be parsed:") and out.count("\n") == 1
    v1.unlink()
    code, out, _ = run("spec", "verify", task)
    assert (code, out) == (1, "v1 MODIFIED (file missing; recorded locally)\n")


@needs_git
def test_superseding_keeps_the_chain(run, sample_repo, db_path):
    task = _task(run, sample_repo)
    _to_approval(run, task)
    _ok(run, "spec", "approve", task)
    specs = sample_repo / "docs" / "specs" / task
    first_body = parse_spec(specs / "v1.md").body
    assert _ok(run, "spec", "approve", task).splitlines()[-1] == "v1 superseded"
    v1, v2 = parse_spec(specs / "v1.md"), parse_spec(specs / "v2.md")
    assert (v1.status, v1.superseded_by, v1.body) == ("superseded", 2, first_body)
    assert (v2.status, v2.supersedes) == ("approved", 1)
    assert _ok(run, "spec", "verify", task) == "v1 ok\nv2 ok\n"
    listed = _ok(run, "spec", "list", task).splitlines()
    assert [line.split()[:2] for line in listed] == [["v1", "superseded"], ["v2", "approved"]]
    data = json.loads(_ok(run, "spec", "list", task, "--json"))
    assert [(d["version"], d["superseded_by"]) for d in data] == [(1, 2), (2, None)]
    rows = _db(db_path, task)[1]
    assert [(r.version, r.status, r.superseded_by) for r in rows] == [
        (1, "superseded", 2),
        (2, "approved", None),
    ]


def test_no_commit_means_no_approval(run, tmp_path, db_path):
    repo = make_sample_repo(tmp_path / "plain", with_git=False)
    task = _task(run, repo)
    specs = repo / "docs" / "specs" / task
    _ok(run, "spec", "draft", task)
    assert parse_spec(specs / "draft.md").revision is None
    _to_approval(run, task)
    snapshot, events = tree_snapshot(repo), len(_db(db_path, task)[0])
    code, _, err = run("spec", "approve", task)
    assert code == 1 and "the spec cannot be tied to a commit" in err
    assert (tree_snapshot(repo), len(_db(db_path, task)[0])) == (snapshot, events)
    assert _db(db_path, task)[1] == [] and [p.name for p in specs.iterdir()] == ["draft.md"]
    assert _ok(run, "spec", "verify", task) == "draft (not sealed)\n"


@needs_git
def test_fresh_clone_lists_and_verifies_the_approved_spec(run, sample_repo, tmp_path):
    task = _task(run, sample_repo)
    _to_approval(run, task)
    _ok(run, "spec", "approve", task)
    _ok(run, "spec", "approve", task)
    approved_at = _head(sample_repo)
    for args in (["add", "-A"], ["commit", "-q", "-m", "spec"]):
        subprocess.run([*GIT, *args], cwd=sample_repo, check=True, capture_output=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(sample_repo), str(clone)], check=True)
    fresh = tmp_path / "other" / "state.db"
    specs = str(clone / "docs" / "specs")
    assert main(["--db", str(fresh), "spec", "verify", task, "--specs", specs]) == 0
    assert main(["--db", str(fresh), "spec", "list", task, "--specs", specs]) == 0
    assert not fresh.exists() and not fresh.parent.exists()
    v2 = parse_spec(clone / "docs" / "specs" / task / "v2.md")
    assert (v2.status, v2.revision) == ("approved", approved_at)
    assert _head(clone) != approved_at


def test_unknown_task_and_this_repository_untouched(tmp_path, capsys):
    db = tmp_path / "absent" / "state.db"
    for action in ("show", "draft", "approve"):
        assert main(["--db", str(db), "spec", action, "t-00000000"]) == 1
    assert not db.parent.exists()
    assert main(["--db", str(db), "spec", "list", "../x", "--specs", str(tmp_path)]) == 1
    assert not (OWN_DOCS / "specs").exists()
    assert tree_snapshot(OWN_DOCS) == OWN_SNAPSHOT


def test_rationale_comes_only_from_approved_state(run, tmp_path):
    repo = make_sample_repo(tmp_path / "plain", with_git=False)
    task = _task(run, repo)
    out = _ok(run, "interview", "ask", task, "security", "Who?", "--decision")
    qid = re.match(r"Q(\d+) added", out).group(1)
    for verdict_title, rationale in (("Export access", "Least privilege"), ("Unused", "Nope")):
        _ok(
            run, "interview", "propose", task, qid, "--title", verdict_title,
            "--recommendation", "Admins only", "--rationale", rationale,
        )  # fmt: skip
    _ok(run, "interview", "decide", task, "1", "accept")
    _ok(run, "interview", "decide", task, "2", "reject")
    _ok(run, "phase", task, "next")
    _ok(run, "design", "propose", task, "data_flow", "rows", "to", "csv", "--rationale", "simple")
    _ok(run, "design", "decide", task, "1", "modify", "--response", "rows to RFC csv")
    shown = _ok(run, "spec", "show", task)
    rationale = shown.split("## Rationale\n\n")[1].split("\n\n## ")[0].splitlines()
    assert rationale == [
        "- P1 Export access: Least privilege (accepted)",
        "- E1 [Data flow] rows to RFC csv: simple (modified_recommendation)",
        "- DEC-0003 v1 — Export access: Least privilege",
    ]
    assert "### Data flow\n- rows to RFC csv (fixed; modified_recommendation)\n" in shown


@needs_git
def test_failed_approval_restores_every_file_and_the_db(run, sample_repo, db_path, monkeypatch):
    from harness.spec_manager import SpecificationManager

    task = _task(run, sample_repo)
    _to_approval(run, task)
    _ok(run, "spec", "approve", task)
    _ok(run, "spec", "draft", task)
    specs = sample_repo / "docs" / "specs" / task
    files = {p.name: p.read_bytes() for p in specs.iterdir()}
    state = _db(db_path, task)
    original = SpecificationManager._approve_in_transaction

    def failing(self, created, restores):
        original(self, created, restores)
        raise OSError("disk full")

    monkeypatch.setattr(SpecificationManager, "_approve_in_transaction", failing)
    code, _, err = run("spec", "approve", task)
    assert code == 1 and "disk full" in err
    assert {p.name: p.read_bytes() for p in specs.iterdir()} == files
    assert _db(db_path, task) == state
