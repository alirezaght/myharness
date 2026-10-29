from pathlib import Path

from harness.command_discovery import INSPECTED_PIN, attribution_compatibility
from tests.test_execution_operations import operations  # noqa: F401


def test_metadata_access_is_not_complete_attribution():
    value = attribution_compatibility(INSPECTED_PIN)
    assert value["status"] == "unsupported" and value["metadata_only"] is False
    assert value["discovery_coverage"] == "unverified"
    assert "content hashing" in value["message"] and "retained source bytes" in value["message"]


def test_readonly_diagnostic_dispatch(operations):  # noqa: F811
    operation, principal, _, _, _, _, _ = operations
    result = operation.dispatch(
        principal, "diagnostic", {"request": {"kind": "attribution-compatibility"}}
    )
    assert result == attribution_compatibility(Path(operation.initialization["pin_root"]).name)
    assert operation.assignments == {}


def test_unknown_pin_and_no_candidate_are_unverified():
    for pin in (None, "future-pin"):
        value = attribution_compatibility(pin)
        assert value["status"] == "unverified" and value["metadata_only"] is None
        assert value["inspected_contract"]["metadata_only_compatibility"] == "unsupported"
