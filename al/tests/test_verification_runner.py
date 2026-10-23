"""Actual children retain isolation, failure aggregation and cancellation receipts."""
import os
from pathlib import Path
import signal
import threading

from al.verification_plan import build_plan
from al.verification_runner import run, validate_child_report
from al.tests.verification_helpers import source, declarations, collected


def test_real_children_isolate_conflicting_home_and_runtime_mutations(tmp_path):
    body = '''import os
from pathlib import Path

def test_a():
    for name in ('HOME', 'AL_RUNTIME_DIR', 'AL_DISTRIBUTION_STATE'):
        file = Path(os.environ[name]) / 'conflicting-write'
        assert not file.exists()
        file.write_text('owned')
'''
    root = source(tmp_path, {suite: body for suite in ('installer', 'codex', 'claude')})
    collections, binding = collected(root, tmp_path)
    plan = build_plan(collections, declarations(root), tier='full', changed_paths=[], binding=binding)
    result = run(plan, workers=3, artifact_root=tmp_path / 'workers', cache_mode='cold')
    assert result['reconciliation']['complete'] and result['reconciliation']['executed_passed'] == 3
    assert len(result['shards']) == 3 and result['cleanup']['complete']


def test_real_failure_does_not_hide_other_suites(tmp_path):
    root = source(tmp_path, {'installer': 'def test_a():\n    assert False\n'})
    collections, binding = collected(root, tmp_path)
    result = run(build_plan(collections, declarations(root), tier='full', changed_paths=[], binding=binding), workers=3, artifact_root=tmp_path / 'workers', cache_mode='cold')
    assert not result['reconciliation']['complete']
    assert result['reconciliation']['executed_passed'] == 2 and result['reconciliation']['executed_failed'] == 1
    assert len(result['shards']) == 3


def test_actual_skip_and_crash_cannot_satisfy_required_obligations(tmp_path):
    root = source(tmp_path, {'installer': 'import pytest\ndef test_a():\n    pytest.skip("conditional fixture")\n',
                             'claude': 'import os\ndef test_a():\n    os._exit(2)\n'})
    collections, binding = collected(root, tmp_path)
    result = run(build_plan(collections, declarations(root), tier='full', changed_paths=[], binding=binding), workers=3, artifact_root=tmp_path / 'workers', cache_mode='cold')
    assert not result['reconciliation']['complete'] and result['reconciliation']['unaccounted']
    assert result['reconciliation']['executed_passed'] == 1 and result['reconciliation']['skipped'] == 1


def test_owned_cancellation_records_incomplete_and_reaps_children(tmp_path):
    root = source(tmp_path, {s: 'import time\ndef test_a():\n    time.sleep(60)\n' for s in ('installer', 'codex', 'claude')})
    collections, binding = collected(root, tmp_path)
    plan = build_plan(collections, declarations(root), tier='full', changed_paths=[], binding=binding)
    timer = threading.Timer(0.7, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    try:result = run(plan, workers=3, artifact_root=tmp_path / 'workers', cache_mode='cold')
    finally:timer.cancel();timer.join()
    assert result['cause']['kind'] == 'KeyboardInterrupt'
    assert result['cleanup']['complete'] and not result['reconciliation']['complete']


def test_missing_and_forged_child_reports_fail_accounting():
    assignment = {'schema_version': 1, 'run_id': 'one', 'shard_id': 'two', 'suite': 'codex',
                  'assignment_digest': 'bound', 'source_digest': 'source', 'nodes': [{'nodeid': 'test_a'}]}
    report = {**assignment, 'finalized': True, 'exitstatus': 0, 'nodes': assignment['nodes'],
              'reports': {'test_a': {'call': {'outcome': 'passed'}}}}
    checked = validate_child_report(assignment, report, {'returncode': 0})
    assert checked['outcomes']['codex:test_a'] == 'error' and checked['errors']


def test_regrouped_assigned_nodes_are_not_drift_but_changed_content_is():
    nodes = [{'nodeid': 'test_a[x]', 'source': 's'}, {'nodeid': 'test_b', 'source': 's'}, {'nodeid': 'test_a[y]', 'source': 's'}]
    assignment = {'schema_version': 1, 'run_id': 'one', 'shard_id': 'two', 'suite': 'codex',
                  'assignment_digest': 'bound', 'source_digest': 'source', 'nodes': nodes}
    passed = {'call': {'outcome': 'passed'}, 'setup': {'outcome': 'passed'}, 'teardown': {'outcome': 'passed'}}
    report = {**assignment, 'finalized': True, 'exitstatus': 0, 'nodes': [nodes[0], nodes[2], nodes[1]],
              'reports': {n['nodeid']: passed for n in nodes}}
    assert 'assigned collection/source/fixture drift' not in validate_child_report(assignment, report, {'returncode': 0})['errors']
    for changed in ([nodes[0], dict(nodes[2], source='other'), nodes[1]], [nodes[0], nodes[1]], [nodes[0], nodes[0], nodes[1]]):
        errors = validate_child_report(assignment, dict(report, nodes=changed), {'returncode': 0})['errors']
        assert 'assigned collection/source/fixture drift' in errors
