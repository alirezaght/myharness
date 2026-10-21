"""Read-only diagnostics are not acknowledgement or deployment authority."""
import json
from pathlib import Path

import pytest
from al import activity_pilot as P
from al.installer.activity_inventory import inventory_activity
from al.tests.activity_helpers import SESSION, scenario, write_state


def prepare(tmp_path, monkeypatch):
    case = scenario(tmp_path, monkeypatch)
    value = json.loads(case['current_path'].read_text())
    value.update(status='RUNNING', phase='IMPLEMENTATION')
    write_state(case['runtime'], value)
    monkeypatch.setattr(P, 'SOURCE', Path(case['invoking']['repository']['root']))
    return case


def snapshot(case):
    return {str(p): (p.read_bytes(), p.stat().st_mode) for root in case['roots']
            for p in Path(root['runtime_root']).rglob('*') if p.is_file()}


def test_public_pilot_preserves_runtime_and_indexes_without_acknowledgements(tmp_path, monkeypatch):
    case = prepare(tmp_path, monkeypatch)
    before = snapshot(case)
    report = tmp_path / 'report.json'
    history = case['request']['history']
    result = P.main(['--scope-file', history['scope']['path'], '--states-file', history['states']['path'],
                     '--nonstate-file', history['nonstate']['path'], '--session', SESSION,
                     '--expect-run', 'actual-test-run', '--report', str(report)])
    value = json.loads(report.read_text())
    assert result == 0 and value['status'] == 'PASS'
    assert value['records'] == value['sessions'] == 2 and value['unknown'] == 0
    assert value['expected_current_run_blocker']
    assert not any(value[key] for key in ('eligibility_granted', 'acknowledgements_published', 'runtime_mutations_authorized'))
    assert value['indexes_before'] == value['indexes_after']
    assert before == snapshot(case)
    assert not Path(case['request']['directory']).exists()


@pytest.mark.parametrize('mutation', ['write', 'mkdir', 'remove'])
def test_pilot_audit_refuses_runtime_mutations(tmp_path, mutation):
    root = tmp_path / 'runtime';root.mkdir()
    path = root / 'state.json';path.write_bytes(b'unchanged')
    with P.readonly([root]), pytest.raises(PermissionError, match='read-only activity pilot'):
        if mutation == 'write':path.write_bytes(b'changed')
        elif mutation == 'mkdir':(root / 'new').mkdir()
        else:path.unlink()
    assert path.read_bytes() == b'unchanged' and sorted(root.iterdir()) == [path]


def test_pilot_rejects_changed_history_and_cannot_attest_terminal_eligibility(tmp_path, monkeypatch):
    case = prepare(tmp_path, monkeypatch)
    history = case['request']['history']
    value = json.loads(case['old_path'].read_text());value['task'] += ' changed'
    write_state(case['old_runtime'], value)
    result = P.inspect_history(history['scope'], history, session=SESSION, run='actual-test-run')
    assert result['status'] == 'FAIL' and any('historical' in error for error in result['errors'])
    assert not result['eligibility_granted']


def test_pilot_does_not_ignore_noncurrent_pending_ownership(tmp_path, monkeypatch):
    case = prepare(tmp_path, monkeypatch)
    original = P.observe_activity
    def pending(*args, **kwargs):
        value = original(*args, **kwargs)
        value['operations'][1].update(result='unknown', reason='uncommitted execution outbox operations')
        return value
    monkeypatch.setattr(P, 'observe_activity', pending)
    history = case['request']['history']
    result = P.inspect_history(history['scope'], history, session=SESSION, run='actual-test-run')
    assert result['status'] == 'FAIL' and result['historical_operation_blockers']
    assert not result['eligibility_granted']
