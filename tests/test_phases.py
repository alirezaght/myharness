import itertools

import pytest

from harness.phases import (
    PHASE_ORDER,
    InvalidTransition,
    Phase,
    is_backward,
    next_phase,
    validate_transition,
)


def test_phase_order_is_exactly_the_specified_sequence():
    assert [str(p) for p in PHASE_ORDER] == [
        "investigation",
        "interview",
        "design",
        "test_plan",
        "final_review",
        "approval",
    ]


def test_next_phase_steps_one_forward_and_ends_at_approval():
    assert [next_phase(p) for p in PHASE_ORDER] == [*PHASE_ORDER[1:], None]


@pytest.mark.parametrize("src,dst", list(itertools.product(PHASE_ORDER, repeat=2)))
def test_transition_rule_over_every_pair(src, dst):
    i, j = PHASE_ORDER.index(src), PHASE_ORDER.index(dst)
    if j == i + 1 or j < i:
        validate_transition(src, dst)
        assert is_backward(src, dst) == (j < i)
    else:
        with pytest.raises(InvalidTransition) as exc:
            validate_transition(src, dst)
        assert (exc.value.from_phase, exc.value.to_phase) == (src, dst)


def test_invalid_transition_is_a_value_error():
    with pytest.raises(ValueError):
        validate_transition(Phase.INVESTIGATION, Phase.DESIGN)
