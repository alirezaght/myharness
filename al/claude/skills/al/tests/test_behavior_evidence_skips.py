"""Issue #19: skipped and expected-failure results are unverified, never a refusal."""

import pytest

import al_behavior_observation_items as Items
import al_ledger as L


def ledger_with(tmp_path, monkeypatch, counts, log_text, cwd="."):
    log = tmp_path / "E1.log"
    log.write_text(log_text)
    entry = {"id": "E1", "status": "PASS", "counts": counts, "log": str(log), "cwd": cwd,
             "scope": ["svc"]}
    monkeypatch.setattr(L, "entry_verdict", lambda st, e: ("VALID", ""))
    return {"evidence": {"entries": [entry]}}


PYTEST_RS = """$ pytest -q -rs tests
..s.s
=========================== short test summary info ============================
SKIPPED [1] tests/test_integration.py:12: needs credentials
SKIPPED [1] tests/test_orders.py:40: unconditional skip
3 passed, 2 skipped in 0.10s
"""


def test_preexisting_unrelated_skips_do_not_refuse(tmp_path, monkeypatch):
    st = ledger_with(tmp_path, monkeypatch, {"passed": 3, "skipped": 2}, PYTEST_RS)
    assert [e["id"] for e in Items.evidence(st, ["E1"])] == ["E1"]
    rows = Items.unverified_results(st, ["E1"], {"tests/test_api.py"}, changed_paths=["svc/api.py"])
    assert rows == [{
        "evidence_id": "E1", "counts": {"skipped": 2}, "related_paths": [], "located": True,
        "note": "only in files unrelated to the change or its assertions; informational"}]


def test_skips_in_asserted_or_changed_files_are_reported_not_refused(tmp_path, monkeypatch):
    st = ledger_with(tmp_path, monkeypatch, {"passed": 3, "skipped": 2}, PYTEST_RS)
    Items.evidence(st, ["E1"])
    rows = Items.unverified_results(
        st, ["E1"], {"tests/test_orders.py"}, changed_paths=["tests/test_integration.py"])
    assert rows[0]["related_paths"] == ["tests/test_integration.py", "tests/test_orders.py"]
    assert "unverified" in rows[0]["note"]


def test_verbose_and_xfail_locations_resolve_from_the_check_cwd(tmp_path, monkeypatch):
    text = ("svc/tests/test_a.py::test_one PASSED\n"
            "tests/test_b.py::test_two SKIPPED (slow)\n"
            "XFAIL tests/test_c.py::test_three - known\n"
            "1 passed, 1 skipped, 1 xfailed in 0.1s\n")
    st = ledger_with(tmp_path, monkeypatch, {"passed": 1, "skipped": 1}, text, cwd="svc")
    assert Items.unverified_locations(L.find(st, "E1")) == {"svc/tests/test_b.py", "svc/tests/test_c.py"}
    rows = Items.unverified_results(st, ["E1"], {"svc/tests/test_b.py"}, changed_paths=())
    assert rows[0]["related_paths"] == ["svc/tests/test_b.py"]


def test_unlocated_skips_are_counted_and_do_not_refuse(tmp_path, monkeypatch):
    st = ledger_with(tmp_path, monkeypatch, {"passed": 3, "skipped": 7}, "3 passed, 7 skipped\n")
    Items.evidence(st, ["E1"])
    rows = Items.unverified_results(st, ["E1"], set(), changed_paths=())
    assert rows[0]["located"] is False and rows[0]["counts"] == {"skipped": 7}


def test_clean_runs_report_nothing_and_failures_still_refuse(tmp_path, monkeypatch):
    st = ledger_with(tmp_path, monkeypatch, {"passed": 3}, "3 passed\n")
    assert Items.unverified_results(st, ["E1"], set(), changed_paths=()) == []
    L.find(st, "E1")["counts"] = {"passed": 3, "failed": 1}
    with pytest.raises(SystemExit):
        Items.evidence(st, ["E1"])
