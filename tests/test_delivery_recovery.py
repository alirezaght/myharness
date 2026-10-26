"""Durable operation claims are exclusive and never reclaimed after uncertainty."""

import pytest

from harness.delivery_models import DeliveryConfig
from harness.delivery_store import (
    claim_operation,
    configure_delivery,
    finish_operation,
    get_delivery,
)
from tests.execution_helpers import approved_execution


def test_durable_operation_exclusion_and_confirmation(controller, sample_repo, monkeypatch):
    _, eid = approved_execution(controller, sample_repo, monkeypatch)
    configure_delivery(
        controller,
        eid,
        DeliveryConfig("https://gitlab.example", "group/project", "candidate", "main", "staging"),
    )
    delivery = get_delivery(controller.conn, eid)
    operation, prior = claim_operation(
        controller.conn, delivery, "merge_request", "a" * 40, {"marker": "exact"}
    )
    assert prior is None
    with pytest.raises(ValueError):
        claim_operation(controller.conn, delivery, "merge_request", "a" * 40, {"marker": "exact"})
    finish_operation(controller.conn, operation, "Uncertain")
    with pytest.raises(ValueError):
        claim_operation(controller.conn, delivery, "merge_request", "a" * 40, {"marker": "exact"})
    finish_operation(controller.conn, operation, "Confirmed", {"iid": 7})
    assert claim_operation(
        controller.conn, delivery, "merge_request", "a" * 40, {"marker": "exact"}
    )[1] == {"iid": 7}
