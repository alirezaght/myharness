"""Test identifiers and parameter text cannot supply aggregate pass counts."""
import pytest

import al_ledger as L


@pytest.mark.parametrize('text', [
    'tests/test_review.py::test_schema4_never_silently_upgrades_legacy3 PASSED [ 14%]',
    'tests/test_demo.py::test_result[3 PASSED] PASSED [100%]',
    'tests/test_demo.py::test_result[label 17 passed] PASSED',
    'tests/test_demo.py::test_result[OK (3 tests)] PASSED [100%]',
    'tests/test_demo.py::test_result[3 examples, 0 failures] PASSED [100%]',
    'tests/test_demo.py::test_result[Tests run: 3, Failures: 0, Errors: 0] PASSED',
    'tests/test_demo.py::test_result[no tests ran] PASSED [100%]',
    '\x1b[32mtests/test_demo.py::test_result[7 passed] PASSED [100%]\x1b[0m',
    'tests/test_demo.py::TestV3::test_output2 SKIPPED [ 50%]',
    'identifier3 passed',
    'identifier_3 passed',
    'identifier-3 passed',
    '1.3 passed',
    '-3 passed',
])
def test_identifiers_are_not_runner_summaries(text):
    assert L.parse_counts(text) == {}


@pytest.mark.parametrize('summary', [
    '3 passed in 0.2s',
    '================== 3 passed in 0.2s ==================',
    '\x1b[32m3\x1b[0m passed in 0.2s',
    'Tests: 3 passed, 3 total',
    'Tests 3 passed (3)',
])
def test_actual_summary_remains_authoritative(summary):
    output = 'tests/test_demo.py::test_result[label 17 passed] PASSED [100%]\n'
    assert L.parse_counts(output + summary) == {'passed': 3}


@pytest.mark.parametrize('status,key', [('FAILED', 'failed'), ('ERROR', 'error')])
def test_failed_node_cannot_be_hidden_by_a_passing_summary(status, key):
    output = f'tests/test_demo.py::test_result[label 17 passed] {status} [100%]\n'
    counts = L.parse_counts(output + '3 passed in 0.2s')
    assert counts == {'passed': 3, key: 1}
    assert L.classify('test', 0, counts, False, 'same', 'same', output=output)[0] == 'FAIL'


def test_suite_counts_and_prior_failures_keep_their_existing_semantics():
    text = 'Test Suites: 2 failed, 2 total\nTests: 3 passed\nTests: 4 passed'
    assert L.parse_counts(text) == {'passed': 4, 'failed': 2}


@pytest.mark.parametrize('summary', [
    'OK (3 tests)',
    '3 examples, 0 failures',
    'Tests run: 3, Failures: 0, Errors: 0',
])
def test_actual_fallback_summary_is_not_filtered(summary):
    node = 'tests/test_demo.py::test_result[OK (17 tests)] PASSED [100%]\n'
    counts = L.parse_counts(node + summary)
    assert counts['passed'] == 3
    assert not counts.get('failed') and not counts.get('error')


def test_failed_node_is_preserved_with_fallback_summary():
    node = 'tests/test_demo.py::test_result[OK (17 tests)] FAILED [100%]\n'
    assert L.parse_counts(node + 'OK (3 tests)') == {'passed': 3, 'failed': 1}


@pytest.mark.parametrize('text', ['', ' \n\t'])
def test_truly_empty_output_retains_zero_test_semantics(text):
    assert L.parse_counts(text) == {'passed': 0}
