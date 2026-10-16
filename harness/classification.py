"""Task complexity classification and the interview depth it implies (SPEC §2, §3 Phase 2)."""

from dataclasses import dataclass
from enum import StrEnum


class Complexity(StrEnum):
    TRIVIAL = "trivial"
    SIMPLE = "simple"
    STANDARD = "standard"
    COMPLEX = "complex"


INTERVIEW_TOPICS: tuple[str, ...] = (
    "business_requirements",
    "functional_behavior",
    "inputs_outputs",
    "edge_cases",
    "component_responsibilities",
    "component_communication",
    "data_schemas",
    "api_contracts",
    "configuration",
    "error_handling",
    "performance",
    "security",
    "observability",
    "acceptance_criteria",
)

_TRIVIAL = frozenset({"functional_behavior", "acceptance_criteria"})
_SIMPLE = _TRIVIAL | {"inputs_outputs", "edge_cases", "error_handling"}
_STANDARD = _SIMPLE | {
    "business_requirements",
    "component_responsibilities",
    "component_communication",
    "data_schemas",
    "api_contracts",
    "configuration",
    "security",
}
_TOPICS_BY_COMPLEXITY = {
    Complexity.TRIVIAL: _TRIVIAL,
    Complexity.SIMPLE: _SIMPLE,
    Complexity.STANDARD: _STANDARD,
    Complexity.COMPLEX: frozenset(INTERVIEW_TOPICS),
}

_COMPLEX_MARKERS = (
    "architecture",
    "migration",
    "new service",
    "concurrency",
    "distributed",
    "multi-tenant",
    "redesign",
    "security model",
)
_TRIVIAL_MARKERS = ("typo", "rename", "wording", "comment", "log message")
_SIMPLE_MARKERS = ("bug", "fix", "crash", "regression")
_TRIVIAL_MAX_WORDS = 40


@dataclass(frozen=True)
class InterviewDepth:
    complexity: Complexity
    topics: tuple[str, ...]


def interview_depth(complexity: Complexity) -> InterviewDepth:
    selected = _TOPICS_BY_COMPLEXITY[Complexity(complexity)]
    return InterviewDepth(
        Complexity(complexity), tuple(t for t in INTERVIEW_TOPICS if t in selected)
    )


def classify_request(text: str) -> Complexity:
    """Propose a complexity for a request; the developer may override it."""
    lowered = text.lower()
    if any(marker in lowered for marker in _COMPLEX_MARKERS):
        return Complexity.COMPLEX
    if any(m in lowered for m in _TRIVIAL_MARKERS) and len(lowered.split()) < _TRIVIAL_MAX_WORDS:
        return Complexity.TRIVIAL
    if any(marker in lowered for marker in _SIMPLE_MARKERS):
        return Complexity.SIMPLE
    return Complexity.STANDARD
