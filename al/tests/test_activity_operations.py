"""Explicit unresolved cleanup survives reconciliation and PID-reuse classification."""
import json
from pathlib import Path

import pytest
from al import activity_pilot as Pilot
from al.installer.activity_observation import quiescence_context
from al.installer import activity_retirement as A
from al.tests.activity_helpers import SESSION, write_state
from al.tests.test_activity_pid_reuse import GOOD, case_with_pid, ps_rows


@pytest.mark.parametrize('cleanup', [
    {'exit': None, 'errors': [], 'unresolved': True},
    {'exit': -9, 'errors': ['PermissionError: signal denied'], 'unresolved': False},
    {'exit': -9, 'errors': [], 'unresolved': 0},
    {'exit': True, 'errors': [], 'unresolved': False},
    {'exit': -9, 'errors': [], 'unresolved': False, 'unknown': True},
    {'complete': False}, [],
])
@pytest.mark.parametrize('receipt', [None, 'supported'])
def test_uncertain_or_unsupported_cleanup_blocks_even_reconciled(tmp_path, monkeypatch, cleanup, receipt):
    case, _ = case_with_pid(tmp_path, monkeypatch, cleanup=cleanup, receipt=receipt)
    ps_rows(monkeypatch, [GOOD])
    projected = case['inventory']['roots'][1]['records'][0]['operation_facts']['checks'][0]['cleanup']
    assert projected == cleanup
    with pytest.raises(ValueError, match='cleanup'):
        with quiescence_context(case['plan']):pass


@pytest.mark.parametrize('receipt', [None, 'supported'])
def test_exact_empty_cleanup_and_original_guarded_receipt_remain_supported(tmp_path, monkeypatch, receipt):
    case, _ = case_with_pid(tmp_path, monkeypatch, cleanup={'exit': -15, 'errors': [], 'unresolved': False}, receipt=receipt)
    ps_rows(monkeypatch, [GOOD])
    with quiescence_context(case['plan']) as check:assert check()['assessment']['eligible']
    if receipt:
        validation = case['inventory']['roots'][1]['records'][0]['operation_facts']['check_reconciliations']['E1']
        assert validation['supported'] and validation['artifact_sha256']
        assert validation['payload_semantics'].endswith('no descendant cleanup waiver.')


@pytest.mark.parametrize('receipt', ['boolean', 'wrong-kind', 'corrupt', 'wrong-note'])
def test_truthy_or_unbound_reconciliation_cannot_waive_interruption(tmp_path, monkeypatch, receipt):
    case, _ = case_with_pid(tmp_path, monkeypatch, receipt=receipt);ps_rows(monkeypatch, [GOOD])
    with pytest.raises(ValueError, match='reconciliation|incomplete'):
        with quiescence_context(case['plan']):pass


def test_pilot_reports_actual_historical_cleanup_uncertainty(tmp_path, monkeypatch):
    case, _ = case_with_pid(tmp_path, monkeypatch, cleanup={'exit': None, 'errors': [], 'unresolved': True}, receipt='supported')
    ps_rows(monkeypatch, [GOOD]);monkeypatch.setattr(Pilot, 'SOURCE', Path(case['invoking']['repository']['root']))
    value = json.loads(case['current_path'].read_text());value.update(status='RUNNING', phase='IMPLEMENTATION')
    write_state(case['runtime'], value)
    refs = case['request']['history']
    result = Pilot.inspect_history(refs['scope'], refs, session=SESSION, run='actual-test-run')
    assert result['status'] == 'FAIL' and not result['eligibility_granted'] and result['expected_current_run_blocker']
    assert any(r['reason'] == 'unresolved owned-process cleanup' for r in result['historical_operation_blockers'])


def test_invoking_completed_reference_can_resolve_without_retiring_nonterminal_run(tmp_path, monkeypatch):
    from al.installer.activity_inventory import inventory_activity
    from al.installer.activity_observation import locked_activity, observe_activity
    from al.installer.activity_retirement import require_workspace_quiescent
    from al.tests.activity_helpers import planned
    from al.tests.test_activity_pid_reuse import BASE, PID
    case, _ = case_with_pid(tmp_path, monkeypatch);ps_rows(monkeypatch, [GOOD])
    value = json.loads(case['current_path'].read_text())
    value.update(status='RUNNING', phase='IMPLEMENTATION', updated_at=BASE + 50,
                 evidence={'seq': 1, 'plan': {}, 'entries': [{'id': 'E1', 'argv': ['completed-check'],
                     'run': value['run_id'], 'reservation': 'token', 'status': 'PASS', 'pid': PID,
                     'started_at': BASE - 200, 'ended_at': BASE - 100}]})
    write_state(case['runtime'], value)
    case['inventory'] = inventory_activity(case['roots'], invoking_run=case['invoking'])
    case['request']['directory'] = str(tmp_path / 'next-acks');plan = planned(case, tmp_path / 'next-plan')
    with locked_activity(case['inventory']) as (locks, limitations):
        observation = observe_activity(case['inventory'], locks=locks, limitations=limitations)
        row = next(p for p in observation['processes'] if p['pid'] == PID and p['record_identity'] == case['invoking'])
        assert row['result'] == 'different-process' and not row['evidence']['independent_start_observation']['signal_authority']
        with pytest.raises(ValueError, match='invoking run is not independently APPROVED/DONE'):
            require_workspace_quiescent(plan, plan['activity']['acknowledgements'], observation=observation)
    assert all(case['invoking'] != r['identity'] for ref in plan['activity']['acknowledgements']
               for r in A.read_reference(ref)['covered_records'])
