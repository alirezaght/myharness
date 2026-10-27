import pytest

from harness.classification import (
    INTERVIEW_TOPICS,
    Complexity,
    classify_request,
    interview_depth,
)


def test_complexity_levels_in_order():
    assert [str(c) for c in Complexity] == ["trivial", "simple", "standard", "complex"]


def test_interview_topics_cover_the_fourteen_spec_topics():
    assert len(INTERVIEW_TOPICS) == 14
    assert len(set(INTERVIEW_TOPICS)) == 14


def test_interview_depth_is_strictly_nested_and_complex_covers_all():
    sets = [set(interview_depth(c).topics) for c in Complexity]
    for smaller, larger in zip(sets, sets[1:], strict=False):
        assert smaller < larger
    assert sets[-1] == set(INTERVIEW_TOPICS)
    assert {"functional_behavior", "acceptance_criteria"} <= sets[0]


def test_interview_depth_preserves_spec_topic_order():
    for c in Complexity:
        depth = interview_depth(c)
        assert depth.complexity is c
        assert list(depth.topics) == [t for t in INTERVIEW_TOPICS if t in depth.topics]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Fix a typo in the README", Complexity.TRIVIAL),
        ("Rename the config flag", Complexity.TRIVIAL),
        ("Fix the crash when the list is empty", Complexity.SIMPLE),
        ("Add CSV export to the reports page", Complexity.STANDARD),
        ("Redesign the storage architecture", Complexity.STANDARD),
        ("Rename things as part of a distributed queue rollout", Complexity.TRIVIAL),
        ("rename " + "word " * 45, Complexity.STANDARD),
    ],
)
def test_classify_request_proposals(text, expected):
    assert classify_request(text) is expected
