from pathlib import Path

import pytest

from harness.decisions import (
    DecisionParseError,
    DecisionStatus,
    decision_files,
    history_files,
    load_decisions,
    load_history,
    parse_decision,
)
from tests.sample_repo import APPROVED

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_repo_decision_records_parse_and_are_approved():
    records = load_decisions(REPO_ROOT / "docs" / "decisions")
    assert [r.id for r in records] == ["DEC-0001", "DEC-0002", "DEC-0003", "DEC-0004"]
    assert all(r.status is DecisionStatus.APPROVED and r.version == 1 for r in records)
    first = records[0]
    assert first.title == "Runtime and language"
    assert first.supersedes is None
    assert first.scope and first.decision and first.rationale and first.impact
    assert "Python 3.13" in first.decision


def test_readme_is_not_a_decision_file():
    names = [p.name for p in decision_files(REPO_ROOT / "docs" / "decisions")]
    assert "README.md" not in names


def test_missing_directory_has_no_decision_files(tmp_path):
    assert decision_files(tmp_path / "nope") == []
    assert load_decisions(tmp_path / "nope") == []


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (("- **Status:** approved\n", ""), "Status"),
        (("- **Version:** 1", "- **Version:** one"), "version"),
        (("- **Status:** approved", "- **Status:** pending"), "status"),
        (("## Decision\n\nStore exports as files.\n", ""), "Decision"),
    ],
)
def test_malformed_record_names_the_file(tmp_path, change, problem):
    path = tmp_path / "DEC-0009-bad.md"
    path.write_text(APPROVED.replace(*change), encoding="utf-8")
    with pytest.raises(DecisionParseError) as exc:
        parse_decision(path)
    assert exc.value.path == path
    assert str(path) in str(exc.value)
    assert problem.lower() in str(exc.value).lower()


def test_supersedes_value_is_kept(tmp_path):
    path = tmp_path / "DEC-0003-new.md"
    path.write_text(APPROVED.replace("- **Supersedes:** none", "- **Supersedes:** DEC-0001"))
    assert parse_decision(path).supersedes == "DEC-0001"


def test_superseded_by_and_history_files(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    old = APPROVED.replace("approved", "superseded").replace(
        "- **Supersedes:** none", "- **Supersedes:** none\n- **Superseded by:** DEC-0001 v2"
    )
    (history / "DEC-0001-v1-storage.md").write_text(old, encoding="utf-8")
    (tmp_path / "DEC-0001-storage.md").write_text(
        APPROVED.replace("- **Version:** 1", "- **Version:** 2"), encoding="utf-8"
    )
    assert [p.name for p in decision_files(tmp_path)] == ["DEC-0001-storage.md"]
    assert [p.name for p in history_files(tmp_path)] == ["DEC-0001-v1-storage.md"]
    [current] = load_decisions(tmp_path)
    [previous] = load_history(tmp_path)
    assert (current.version, current.superseded_by) == (2, None)
    assert (previous.version, previous.status, previous.superseded_by) == (
        1,
        DecisionStatus.SUPERSEDED,
        "DEC-0001 v2",
    )
    assert load_history(tmp_path, "DEC-0009") == []
    assert history_files(tmp_path / "nope") == []
