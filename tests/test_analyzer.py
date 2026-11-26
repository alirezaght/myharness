import json
import os
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from harness import repo_scan
from harness.analyzer import analyze
from harness.investigation import FactCategory
from tests.sample_repo import (
    FAKE_SECRET,
    make_index_stale,
    make_sample_repo,
    tree_snapshot,
    write_files,
)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _facts(report, category):
    return [f.text for f in report.facts if f.category is category]


def _questions(report):
    return [q.text for q in report.questions]


def test_report_keeps_facts_assumptions_and_questions_separate(sample_repo):
    report = analyze(sample_repo, "add csv export")
    assert report.facts and report.assumptions and report.questions
    assert all(f.evidence.strip() for f in report.facts)
    assert all(a.basis.strip() for a in report.assumptions)
    rendered = report.render(full=True).splitlines()
    headings = [line.split(" (")[0] for line in rendered if not line.startswith(" ")][2:]
    assert headings == [
        "approved decisions",
        "verified facts",
        "assumptions",
        "unresolved questions",
    ]
    data = report.to_dict()
    assert data["counts"] == {
        "facts": len(report.facts),
        "assumptions": len(report.assumptions),
        "questions": len(report.questions),
        "decisions": 1,
    }
    json.dumps(data)


def test_report_covers_every_probe(sample_repo):
    report = analyze(sample_repo, "add csv export")
    deps = _facts(report, FactCategory.DEPENDENCIES)
    assert "runtime dependencies: requests" in deps
    assert "dependency-groups `dev`: pytest" in deps
    assert "ruff line-length 88" in _facts(report, FactCategory.CONVENTIONS)
    assert "pytest testpaths: tests" in _facts(report, FactCategory.TESTS)
    assert any("demo/api/routes.py" in t for t in _facts(report, FactCategory.INTERFACES))
    schemas = _facts(report, FactCategory.SCHEMAS)
    assert "schema file `migrations/0001_init.sql`" in schemas
    assert "migrations directory `migrations/` (1 files)" in schemas
    assert "config file `config/settings.yaml`" in _facts(report, FactCategory.CONFIG)
    assert "test location `tests/` (1 files)" in _facts(report, FactCategory.TESTS)
    assert "CI workflow `.github/workflows/ci.yml`" in _facts(report, FactCategory.CONSTRAINTS)
    assert any("`demo/` is a Python package" in t for t in _facts(report, FactCategory.COMPONENTS))


def test_only_approved_decisions_are_presented_as_approved(sample_repo):
    report = analyze(sample_repo, "add csv export")
    assert [d.id for d in report.decisions] == ["DEC-0001"]
    decisions = _facts(report, FactCategory.DECISIONS)
    assert "DEC-0002 is superseded" in decisions
    assert report.facts.index(
        next(f for f in report.facts if f.category is FactCategory.DECISIONS)
    ) < report.facts.index(next(f for f in report.facts if f.category is FactCategory.FILES))


def test_malformed_decision_becomes_a_question(sample_repo):
    (sample_repo / "docs/decisions/DEC-0003-bad.md").write_text("# DEC-0003 — Bad\n")
    report = analyze(sample_repo, "add csv export")
    [question] = [q for q in report.questions if q.topic == "decisions"]
    assert "DEC-0003-bad.md" in question.text
    assert [d.id for d in report.decisions] == ["DEC-0001"]


def test_missing_decisions_dir_is_a_fact(tmp_path):
    root = write_files(tmp_path / "r", {"app.py": "print(1)\n"})
    report = analyze(root, "print things")
    assert report.decisions == ()
    assert any("no decision records" in t for t in _facts(report, FactCategory.DECISIONS))


def test_relevant_files_are_ranked_by_request_keywords(sample_repo):
    report = analyze(sample_repo, "add csv export")
    files = _facts(report, FactCategory.FILES)
    assert files[0].startswith("`demo/export.py` matches 2 request keywords")
    assert not any("keyword density" in a.basis for a in report.assumptions)


def test_no_match_and_no_keyword_requests_raise_questions(sample_repo):
    report = analyze(sample_repo, "zebra quokka")
    assert any(t.startswith("No file matches the request keywords") for t in _questions(report))
    assert _facts(report, FactCategory.FILES) == []
    report = analyze(sample_repo, "add the new")
    assert any("no usable keywords" in t for t in _questions(report))


def test_secret_contents_never_appear(sample_repo):
    report = analyze(sample_repo, "add csv export token sekrit")
    assert FAKE_SECRET not in report.render()
    assert FAKE_SECRET not in json.dumps(report.to_dict())
    assert "secret-bearing file `.env` present (contents not read)" in _facts(
        report, FactCategory.CONFIG
    )
    assert any("`.env`" in t for t in _questions(report))
    assert not any(f.text.startswith("`.env` matches") for f in report.facts)


@needs_git
def test_analyze_leaves_repo_and_git_dir_untouched(sample_repo):
    (sample_repo / "untracked.py").write_text("x = 1\n")
    make_index_stale(sample_repo)
    before = tree_snapshot(sample_repo)
    assert any(path.startswith(".git/") for path in before[0])
    analyze(sample_repo, "add csv export")
    assert tree_snapshot(sample_repo) == before


@needs_git
def test_snapshot_detects_an_index_refresh(sample_repo):
    make_index_stale(sample_repo)
    before = tree_snapshot(sample_repo)
    env = {k: v for k, v in os.environ.items() if k != "GIT_OPTIONAL_LOCKS"}
    subprocess.run(["git", "status", "--porcelain"], cwd=sample_repo, env=env, capture_output=True)
    assert tree_snapshot(sample_repo) != before


@needs_git
def test_git_facts_for_a_committed_repo(sample_repo):
    report = analyze(sample_repo, "add csv export")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=sample_repo, capture_output=True, text=True
    ).stdout.strip()
    assert report.revision == head
    assert "working tree clean" in _facts(report, FactCategory.REPOSITORY)
    assert any("via git ls-files" in t for t in _facts(report, FactCategory.REPOSITORY))


def test_non_git_directory_uses_directory_walk(tmp_path):
    root = make_sample_repo(tmp_path / "plain", with_git=False)
    write_files(root, {"node_modules/x.js": "1", "demo/__pycache__/a.py": "1"})
    report = analyze(root, "add csv export")
    assert report.revision is None
    repo = _facts(report, FactCategory.REPOSITORY)
    assert "not a git repository (directory walk used)" in repo
    files, source = repo_scan.list_files(root.resolve())
    assert source == repo_scan.WALK_SOURCE
    names = {p.as_posix() for p in files}
    assert "demo/export.py" in names and ".env" in names
    assert not any("node_modules" in n or "__pycache__" in n for n in names)


def test_missing_git_binary_falls_back_to_walk(tmp_path, monkeypatch):
    root = make_sample_repo(tmp_path / "nogit", with_git=False)

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(repo_scan.subprocess, "run", no_git)
    report = analyze(root, "add csv export")
    assert report.revision is None
    assert any("via directory walk" in t for t in _facts(report, FactCategory.REPOSITORY))


def test_symlinked_file_outside_root_is_not_read(tmp_path, monkeypatch):
    outside = tmp_path / "outside.txt"
    outside.write_text("quokka OUTSIDE_CONTENT_CANARY")
    root = write_files(tmp_path / "r", {"app.py": "x = 1\n"})
    (root / "link.txt").symlink_to(outside)
    original_open = Path.open
    opened = []

    def confined_open(path, *args, **kwargs):
        assert path.resolve() != outside.resolve(), "opened an outside symlink target"
        opened.append(path.resolve())
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", confined_open)
    report = analyze(root, "quokka")
    assert root / "app.py" in opened
    assert _facts(report, FactCategory.FILES) == [
        "Repository inventory reached its bound; ownership and coverage remain uncertain."
    ]
    assert "OUTSIDE_CONTENT_CANARY" not in json.dumps(report.to_dict())
    assert "link.txt" not in "\n".join(f.text for f in report.facts)


def test_missing_tests_several_packages_and_bad_pyproject(tmp_path):
    root = write_files(
        tmp_path / "r",
        {
            "pyproject.toml": "[project\nname=",
            "web/package.json": '{"dependencies": {"left-pad": "1"}, "scripts": {"build": "x"}}',
            "web/index.js": "export const csv = 1;\n",
        },
    )
    report = analyze(root, "csv")
    questions = _questions(report)
    assert any(t.startswith("No tests found") for t in questions)
    assert any("outcome" in t and "optional" in t for t in questions)
    assert any("Several packages found" in fact.text for fact in report.facts)
    assert "pyproject.toml could not be parsed; which dependencies matter?" in questions
    assert "dependencies: left-pad" in _facts(report, FactCategory.DEPENDENCIES)
    assert len(questions) == len(set(questions))


def test_no_manifest_is_a_question(tmp_path):
    root = write_files(tmp_path / "r", {"main.go": "package main\n"})
    report = analyze(root, "main")
    assert any(t.startswith("No dependency manifest found") for t in _questions(report))


@pytest.mark.parametrize("request_text", ["", "   "])
def test_empty_request_is_rejected(sample_repo, request_text):
    with pytest.raises(ValueError):
        analyze(sample_repo, request_text)


def test_non_directory_is_rejected(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("x")
    with pytest.raises(ValueError, match="not a directory"):
        analyze(target, "anything")


def test_package_json_dev_dependencies_and_scripts_are_facts(tmp_path):
    root = write_files(
        tmp_path / "r",
        {
            "package.json": json.dumps(
                {
                    "dependencies": {"left-pad": "1"},
                    "devDependencies": {"vitest": "2", "eslint": "9"},
                    "scripts": {"build": "tsc", "test": "vitest"},
                }
            ),
            "index.js": "export {};\n",
        },
    )
    report = analyze(root, "build things")
    assert "devDependencies: eslint, vitest" in _facts(report, FactCategory.DEPENDENCIES)
    assert "dependencies: left-pad" in _facts(report, FactCategory.DEPENDENCIES)
    assert "npm scripts: build, test" in _facts(report, FactCategory.INTERFACES)


def test_interface_listing_is_capped_at_25(tmp_path):
    files = {f"svc/handler_{n:02d}.py": "x = 1\n" for n in range(30)}
    report = analyze(write_files(tmp_path / "r", files), "unrelated words")
    interfaces = _facts(report, FactCategory.INTERFACES)
    assert len([t for t in interfaces if "looks like an interface" in t]) == 25
    assert "5 more not listed" in interfaces


def test_oversized_and_binary_files_are_listed_but_not_scanned(tmp_path):
    root = write_files(tmp_path / "r", {"app.py": "x = 1\n"})
    (root / "huge.txt").write_text("quokka " * (repo_scan.MAX_SCAN_BYTES // 7 + 10))
    (root / "blob.bin").write_bytes(b"\x00quokka\x00")
    assert (root / "huge.txt").stat().st_size > repo_scan.MAX_SCAN_BYTES
    files, _ = repo_scan.list_files(root.resolve())
    assert {"huge.txt", "blob.bin"} <= {p.as_posix() for p in files}
    assert repo_scan.read_text(root.resolve(), PurePosixPath("huge.txt")) is None
    assert repo_scan.read_text(root.resolve(), PurePosixPath("blob.bin")) is None
    report = analyze(root, "quokka")
    assert _facts(report, FactCategory.FILES) == []
    assert any("outcome" in t and "optional" in t for t in _questions(report))
    assert any(t.startswith("No file matches") for t in _facts(report, FactCategory.COMPONENTS))


def test_env_example_is_config_not_a_secret(tmp_path):
    root = write_files(tmp_path / "r", {".env.example": "TOKEN=placeholder-value\n", "a.py": "1\n"})
    report = analyze(root, "placeholder")
    assert "config file `.env.example`" in _facts(report, FactCategory.CONFIG)
    assert not any("secret-bearing" in t for t in _facts(report, FactCategory.CONFIG))
    assert not any(q.topic == "config" for q in report.questions)
    assert any(t.startswith("`.env.example` matches") for t in _facts(report, FactCategory.FILES))


def test_keywords_ignore_ticket_ids_and_bare_numbers():
    from harness.repo_relevance import keywords

    assert keywords("ABC-1234 + ABC-1239") == []
    assert keywords("PROJ2-77 export csv rows 2048 for v2") == ["export", "csv", "rows"]
    assert keywords("abc-1234 item 42") == ["abc", "item"]


def test_ticket_only_detection_accepts_joiners_only():
    from harness.repo_relevance import ticket_ids, ticket_only

    assert ticket_only("ABC-1234 + ABC-1239")
    assert ticket_only("ABC-1, XY9-22 & ABC-3 and DEF-4")
    assert not ticket_only("ABC-1234 add csv export")
    assert not ticket_only("add csv export")
    assert not ticket_only("1234")
    assert ticket_ids("ABC-1234 + ABC-1239, ABC-1234") == ["ABC-1234", "ABC-1239"]


def test_ticket_id_numbers_are_not_matched_against_files(sample_repo):
    write_files(sample_repo, {"fixtures/postcodes.json": '{"abc": 1234, "n": 1239}\n'})
    report = analyze(sample_repo, "ABC-1234 + ABC-1239")
    assert _facts(report, FactCategory.FILES) == []
    assert not any("1234" in f.evidence or "abc" in f.evidence for f in report.facts)


def test_audit_leads_ignore_ticket_ids(sample_repo):
    from harness.repository_audit import audit

    write_files(sample_repo, {"demo/abc_1234.py": "ABC-1234 = 1\n"})
    assert audit(sample_repo, "ABC-1234 + ABC-1239")["existing_implementation_leads"] == []


def test_ticket_only_request_points_to_context_import_first(sample_repo):
    report = analyze(sample_repo, "ABC-1234 + ABC-1239")
    first = report.questions[0]
    assert first.topic == "scope"
    assert "ABC-1234, ABC-1239" in first.text
    assert "harness context import" in first.text and '"ticket"' in first.text
    assert not any("no usable keywords" in q.text for q in report.questions)
    rendered = report.render(task_id="t-1").splitlines()
    assert "only ticket IDs" in rendered[2]
    assert "harness context import t-1 --file" in rendered[2]
    assert rendered[3].startswith("unresolved questions (")


def test_mixed_request_keeps_keywords_without_ticket_notice(sample_repo):
    report = analyze(sample_repo, "ABC-1234 add csv export")
    assert _facts(report, FactCategory.FILES)[0].startswith("`demo/export.py` matches 2")
    assert not any("only ticket IDs" in q.text for q in report.questions)
    assert "only ticket IDs" not in report.render()


def test_default_render_is_compact_and_full_keeps_inventory(sample_repo):
    report = analyze(sample_repo, "add csv export")
    compact, full = report.render(), report.render(full=True)
    lines = compact.splitlines()
    headings = [line.split(" (")[0] for line in lines if not line.startswith(" ")][2:]
    assert headings[:3] == ["unresolved questions", "approved decisions", "request-relevant facts"]
    assert "`demo/export.py` matches 2 request keywords" in compact
    assert "runtime dependencies: requests" not in compact
    assert "runtime dependencies: requests" in full
    assert "--full" in lines[-1]
    assert len(compact) < len(full)
    # Storage and JSON keep every fact; only the default rendering is shorter.
    assert report.to_dict()["counts"]["facts"] == len(report.facts)


@pytest.mark.parametrize(
    "path",
    [
        ".gitlab-ci.yml",
        "Jenkinsfile",
        ".circleci/config.yml",
        "azure-pipelines.yml",
        ".gitlab/ci/test.yml",
    ],
)
def test_ci_definitions_of_every_provider_are_constraints(path):
    from harness.repo_probes import is_workflow

    assert is_workflow(PurePosixPath(path))
    assert not is_workflow(PurePosixPath("config/settings.yml"))
