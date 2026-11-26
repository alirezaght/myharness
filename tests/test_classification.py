from pathlib import Path

import pytest

from harness.classification import (
    INTERVIEW_TOPICS,
    Complexity,
    classify_request,
    interview_depth,
)
from harness.interview_catalogue import catalogue_for


def test_complexity_levels_in_order():
    assert [str(c) for c in Complexity] == ["trivial", "simple", "standard", "complex"]


# docs/SPEC.md "Phase 2 — Requirements Interview" topic bullets and the topic each names.
SPEC_TOPICS = {
    "Business requirements": "business_requirements",
    "Functional behavior": "functional_behavior",
    "Expected inputs and outputs": "inputs_outputs",
    "Edge cases": "edge_cases",
    "Component responsibilities": "component_responsibilities",
    "Component communication": "component_communication",
    "Data schemas": "data_schemas",
    "API contracts": "api_contracts",
    "Configuration": "configuration",
    "Error handling": "error_handling",
    "Performance": "performance",
    "Security": "security",
    "Observability": "observability",
    "Acceptance criteria": "acceptance_criteria",
}


def _spec_phase_two_bullets() -> list[str]:
    spec = (Path(__file__).resolve().parents[1] / "docs" / "SPEC.md").read_text(encoding="utf-8")
    section = spec.split("### Phase 2 — Requirements Interview", 1)[1]
    listed = section.split("Topics include, when relevant:", 1)[1].split("\n\n", 2)[1]
    return [line.removeprefix("- ") for line in listed.splitlines()]


def test_interview_topics_cover_the_fourteen_spec_topics():
    # The SPEC's own list, in its order, names exactly these topics; dropping or renaming a
    # required concern (security, error handling, ...) fails here, not only a count.
    assert _spec_phase_two_bullets() == list(SPEC_TOPICS)
    assert INTERVIEW_TOPICS == tuple(SPEC_TOPICS.values())
    # A complex task's interview is generated for every one of them.
    complex_topics = interview_depth(Complexity.COMPLEX).topics
    assert complex_topics == INTERVIEW_TOPICS
    assert {entry.topic for entry in catalogue_for(complex_topics)} == set(INTERVIEW_TOPICS)


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
