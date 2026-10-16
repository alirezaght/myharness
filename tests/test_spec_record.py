import pytest

from harness.design_report import build_document
from harness.interview_report import InterviewChecklist, InterviewSummary, Provenance, Requirement
from harness.md_record import mark_superseded
from harness.spec_document import SECTIONS, build_body
from harness.spec_record import (
    SpecParseError,
    SpecStatus,
    content_hash,
    parse_spec,
    parse_spec_text,
    render_spec,
    split_body,
)
from harness.spec_store import SpecStore

BODY = "## Requirements\n\n- Q1 [goal] export rows (developer)\n\n## Test plan\n\n- x\n"


def _render(status=SpecStatus.APPROVED, version=1, supersedes=None, body=BODY, title="Add  csv"):
    return render_spec(
        "t-0000abcd", title, status, version, "2026-01-01T00:00:00+00:00",
        "simple (proposed)", "abc123", "clean", supersedes, body,
    )  # fmt: skip


def test_round_trip_of_every_field(tmp_path):
    path = tmp_path / "v2.md"
    path.write_bytes(_render(version=2, supersedes=1).encode())
    record = parse_spec(path)
    assert (record.task_id, record.title, record.status, record.version) == (
        "t-0000abcd",
        "Add csv",
        SpecStatus.APPROVED,
        2,
    )
    assert (record.revision, record.working_tree, record.supersedes) == ("abc123", "clean", 1)
    assert record.complexity == "simple (proposed)" and record.superseded_by is None
    assert record.body == BODY and record.content_hash == content_hash(BODY)
    draft = parse_spec_text(_render(SpecStatus.DRAFT, None), None)
    assert (draft.version, draft.content_hash, draft.sealed) == (None, None, False)
    no_rev = render_spec("t", "x", "draft", None, "now", "c", None, "unknown", None, BODY)
    assert "- **Revision:** none\n" in no_rev and parse_spec_text(no_rev, None).revision is None


def test_header_only_supersede_keeps_the_sealed_body(tmp_path):
    store = SpecStore(tmp_path / "t-0000abcd")
    text = _render()
    path = store.write_version(1, text)
    record = parse_spec(path)
    before = store.supersede_header(record, 2)
    assert before == text
    after = path.read_bytes().decode()
    assert split_body(after)[1] == split_body(text)[1] == BODY
    superseded = parse_spec(path)
    assert (superseded.status, superseded.superseded_by) == (SpecStatus.SUPERSEDED, 2)
    assert superseded.content_hash == content_hash(superseded.body)
    changed = [line for line in after.splitlines() if line not in text.splitlines()]
    assert changed == ["- **Status:** superseded", "- **Superseded by:** v2"]
    with pytest.raises(ValueError, match="only approved"):
        store.supersede_header(superseded, 3)
    with pytest.raises(ValueError, match="refusing to overwrite"):
        store.write_version(1, text)
    assert path.read_bytes().decode() == after


def test_lossy_or_malformed_input_is_refused():
    with pytest.raises(ValueError, match="title"):
        _render(title="  ")
    with pytest.raises(ValueError, match="single-line"):
        render_spec("t\nx", "t", "approved", 1, "now", "c", "r", "clean", None, BODY)
    with pytest.raises(ValueError, match="'## ' section"):
        _render(body="text\n")
    with pytest.raises(ValueError, match="draft"):
        _render(SpecStatus.DRAFT, 1)
    with pytest.raises(SpecParseError, match="missing field Task"):
        parse_spec_text("# x\n\n- **Status:** draft\n", None)
    with pytest.raises(SpecParseError, match="does not fit"):
        parse_spec_text(_render().replace("**Version:** 1", "**Version:** draft"), None)


def test_developer_text_never_opens_a_section():
    nasty = "first\n## Injected\n ## Also"
    requirement = Requirement(1, "goal", "q", nasty, Provenance.DEVELOPER)
    summary = InterviewSummary((requirement,), (), (), InterviewChecklist((), 0, 0, 0))
    body = build_body(summary, build_document([]), [], [])
    headings = [line[3:] for line in body.splitlines() if line.startswith("## ")]
    assert headings == list(SECTIONS)
    assert "- Q1 [goal] first\n  ## Injected\n  \n  ## Also (developer)" in body
    text = _render(body=body)
    assert parse_spec_text(text, None).body == body
    assert "- **Superseded by:** v2" in mark_superseded(text, "v2")
