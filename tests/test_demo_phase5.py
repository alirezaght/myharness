"""The repeatable Phase 5 demo labels its provider and browser fixtures honestly."""

from scripts.demo_phase5 import run


def test_isolated_demo_marks_fixture_provenance_and_live_acceptance_outstanding():
    result = run()
    assert result["status"] == "demo_complete"
    assert result["provider_origin"].startswith("test_double")
    assert result["driver_origin"].startswith("test_double")
    assert result["capability_origin"].startswith("test-double")
    assert result["browser_status"] == "Completed"
    assert result["browser_evidence_origin"] == ["test_double"]
    assert result["live_acceptance"] == "outstanding"
    assert result["report"]["live_acceptance"] == "outstanding"
