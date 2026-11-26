"""Timing diagnostics retain thresholds and exemptions independently of functional verdicts."""
import pytest

from al.verification_budget import load, violations

BUDGET = {'schema_version': 1, 'test_seconds': 5, 'file_seconds': 60,
          'acceptance': ['installer:al/tests/test_journey.py::*', 'codex:tests/test_x.py::test_real_run']}


def test_fast_tests_and_files_are_within_budget():
    assert violations({'codex:tests/test_a.py::t1': 4.9, 'codex:tests/test_a.py::t2': 4.9}, BUDGET) == []


def test_a_slow_test_and_a_slow_file_are_violations():
    found = violations({'codex:tests/test_a.py::slow': 5.5,
                        **{'codex:tests/test_b.py::t%d' % i: 4.0 for i in range(16)}}, BUDGET)
    assert found == ['file 64.0 s > 60 s: codex:tests/test_b.py', 'test 5.5 s > 5 s: codex:tests/test_a.py::slow']


def test_only_listed_acceptance_journeys_are_exempt():
    assert violations({'installer:al/tests/test_journey.py::anything': 200.0,
                       'codex:tests/test_x.py::test_real_run': 90.0}, BUDGET) == []
    assert violations({'codex:tests/test_x.py::test_other': 6.0}, BUDGET) == ['test 6.0 s > 5 s: codex:tests/test_x.py::test_other']


def test_the_checked_in_budget_is_valid_and_small():
    value = load()
    assert value['test_seconds'] == 5 and value['file_seconds'] == 60
    # At most ~15 real end-to-end journeys are exempt (al/TESTS.md lists them).
    assert len(value['acceptance']) <= 15
    # Named exemptions stay few and each says why it needs real processes.
    assert len(value.get('exempt', {})) <= 10


def test_invalid_budget_is_refused(tmp_path):
    path = tmp_path / 'budget.json'
    path.write_text('{"schema_version": 1, "test_seconds": "5", "file_seconds": 60, "acceptance": []}')
    with pytest.raises(ValueError):
        load(path)


def test_every_acceptance_journey_names_the_risk_it_covers():
    value = load()
    assert set(value['acceptance']) == set(value.get('acceptance_reasons', {}))
    assert all(reason.strip() for reason in value['acceptance_reasons'].values())


def test_reported_durations_and_diagnostics_preserve_original_values():
    from al.verification_budget import observed

    durations = {'codex:tests/test_a.py::slow': 75.0,
                 'installer:al/tests/test_journey.py::anything': 200.0}
    result = {'shards': [{'durations': {**durations, 'unavailable': None}}]}
    assert observed(result) == durations
    assert violations(observed(result), BUDGET) == [
        'file 75.0 s > 60 s: codex:tests/test_a.py',
        'test 75.0 s > 5 s: codex:tests/test_a.py::slow',
    ]
    assert result['shards'][0]['durations'] == {**durations, 'unavailable': None}
