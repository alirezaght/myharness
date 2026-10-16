"""Harness phase state machine: forward one step at a time, back to any earlier phase."""

from enum import StrEnum


class Phase(StrEnum):
    INVESTIGATION = "investigation"
    INTERVIEW = "interview"
    DESIGN = "design"
    TEST_PLAN = "test_plan"
    FINAL_REVIEW = "final_review"
    APPROVAL = "approval"


PHASE_ORDER: tuple[Phase, ...] = tuple(Phase)


class InvalidTransition(ValueError):
    def __init__(self, from_phase: Phase, to_phase: Phase, message: str | None = None):
        self.from_phase = from_phase
        self.to_phase = to_phase
        super().__init__(message or f"invalid transition {from_phase} -> {to_phase}")


def next_phase(phase: Phase) -> Phase | None:
    index = PHASE_ORDER.index(phase)
    return PHASE_ORDER[index + 1] if index + 1 < len(PHASE_ORDER) else None


def is_backward(from_phase: Phase, to_phase: Phase) -> bool:
    return PHASE_ORDER.index(to_phase) < PHASE_ORDER.index(from_phase)


def validate_transition(from_phase: Phase, to_phase: Phase) -> None:
    if to_phase is next_phase(from_phase) or is_backward(from_phase, to_phase):
        return
    raise InvalidTransition(from_phase, to_phase)
