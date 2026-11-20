"""Pending proof, bounded reassessment and gate recovery compose without a deadlock."""
import copy
import json

import pytest
import al_artifacts as A
import al_gate_recovery as Gates
import al_store as Store
from evidence_helpers import plan_na_gates
from test_rework import fast, load, record_red
from test_rework_recovery import decision


def user_continuation(raw, state_file, tmp_path):
    import al_rework_recovery_contract as Contract
    state = load(state_file)
    binding = Contract.binding(state)
    auth = {'schema_version': 1, 'kind': 'explicit-user-continuation-v1',
            'request_id': 'user-continue', 'binding': binding,
            'message': 'Continue this bounded correction.', 'provenance': 'Synthetic user turn'}
    auth_file = tmp_path / 'user-auth.json'
    auth_file.write_text(json.dumps(auth))
    result = raw('artifact', 'record', '--file', str(auth_file), '--kind', 'user-authorization')
    assert result[0] == 0, result
    ref = json.loads(result[1])
    request = {'schema_version': 1, 'kind': 'rework-recovery-continuation-v1',
               'request_id': auth['request_id'], 'checkpoint_id': state['stopping_checkpoint'],
               'binding': binding, 'user_authorization': ref.get('artifact', ref),
               'next_action': 'Repair the already defined source defect.', 'grant_size': 2}
    path = tmp_path / 'continue.json'
    path.write_text(json.dumps(request))
    return path


def pending(cli, state_file, tmp_path):
    # Use the unwrapped public fixture: planning proof intentionally remains pending.
    raw = cli.raw
    for args in (
        ('start', '--spec', 'Preserve authority while repairing a scoped source defect.'),
        ('spec-gate', 'SPEC_READY'),
        ('classify', 'STANDARD', '--designer', 'no', '--implementers', 'al-engineer'),
    ):
        result = raw(*args)
        assert result[0] == 0, result
    plan_na_gates(raw, tmp_path)
    state = load(state_file)
    sid = state['session_id']
    from al_recovery import checkpoint
    ref = checkpoint(state, sid, 'Synthetic retained gate recovery', 'Repair pending proof')
    state['gate_recovery'] = {
        'checkpoint': ref['id'], 'phase': state['phase'], 'superseded_manifest': None,
        'reasons': ['planning proof remains pending'],
        'replacement_manifest': state['gate_manifests'][-1]['sha256'],
    }
    Store.save(sid, state)
    for phase in ('IMPLEMENTATION', 'FINALIZATION'):
        result = raw('phase', phase)
        assert result[0] == 0, result
    record_red(raw)
    for _ in range(2):
        assert raw('rework', '--source', 'finalization', *fast(raw))[0] == 0
        assert raw('phase', 'FINALIZATION')[0] == 0
    before = load(state_file)
    assert before['proof_admission']['status'] == 'pending'
    assert raw('rework', '--source', 'finalization', *fast(raw))[0] == 10
    current = load(state_file)
    assert current['phase'] == 'REWORK_REASSESSMENT'
    assert current['rework'] == before['rework']
    return raw, ref


def test_exhaustion_preserves_a_valid_gate_checkpoint_chain(cli, state_file, tmp_path):
    _, ref = pending(cli, state_file, tmp_path)
    state = load(state_file)
    value = Gates.marker(state)
    assert value['checkpoint'] == ref['id']
    assert value['continuation_phase'] == 'REWORK_REASSESSMENT'
    assert value['continuations'][-1]['to'] == state['phase']
    assert not any(step['to'] == 'REWORK' for step in value['continuations'][-1:])


@pytest.mark.parametrize('condition', ['pending', 'declined', 'cap'])
def test_degraded_writer_continues_without_granting_or_clearing_proof(cli, state_file, tmp_path, condition):
    raw, ref = pending(cli, state_file, tmp_path)
    if condition == 'declined':
        path, _ = decision(raw, tmp_path, decision='decline', basis='no-useful-next-step')
        assert raw('rework-recovery', 'decide', '--file', str(path))[0] == 0
    elif condition == 'cap':
        for number in (1, 2):
            path, _ = decision(raw, tmp_path, request_id='allow-' + str(number))
            assert raw('rework-recovery', 'decide', '--file', str(path))[0] == 0
            assert raw('phase', 'FINALIZATION')[0] == 0
            assert raw('rework', '--source', 'finalization', *fast(raw))[0] == 0
            assert raw('phase', 'FINALIZATION')[0] == 0
            assert raw('rework', '--source', 'finalization', *fast(raw))[0] == 10
    before = load(state_file)
    checkpoint_bytes = A.read(ref)
    result = raw('writer', 'start', '--id', 'bounded-fix', '--role', 'al-engineer',
                 '--files', 'svc/app.py', '--degraded', '--reason', 'Repair the defined defect')
    assert result[0] == 0, result
    current = load(state_file)
    assert current['phase'] == 'IMPLEMENTATION'
    assert current['rework'] == before['rework']
    assert current['rework_recovery'] == before['rework_recovery']
    assert current['proof_admission'] == before['proof_admission']
    assert current['implementation_readiness']['obligations']
    assert Gates.marker(current)['checkpoint'] == ref['id']
    assert A.read(ref) == checkpoint_bytes
    assert raw('writer', 'finish', '--id', 'bounded-fix', '--outcome', 'success')[0] == 0
    assert raw('approved')[0] != 0


def test_bound_decision_can_grant_only_budget_while_proof_is_pending(cli, state_file, tmp_path):
    raw, ref = pending(cli, state_file, tmp_path)
    path, _ = decision(raw, tmp_path)
    before = load(state_file)
    result = raw('rework-recovery', 'decide', '--file', str(path))
    assert result[0] == 0, result
    current = load(state_file)
    assert current['phase'] == 'REWORK'
    assert current['rework']['total'] == 3
    assert current['rework']['history'][:2] == before['rework']['history']
    assert current['rework_recovery']['automatic_authorizations'] == 1
    assert current['proof_admission'] == before['proof_admission']
    assert Gates.marker(current)['checkpoint'] == ref['id']
    saved = state_file().read_bytes()
    assert raw('rework-recovery', 'decide', '--file', str(path))[:2] == result[:2]
    assert state_file().read_bytes() == saved
    assert raw('approved')[0] != 0


def test_planning_authorship_remains_available_during_reassessment(cli, state_file, tmp_path):
    raw, _ = pending(cli, state_file, tmp_path)
    before = load(state_file)
    result = raw('planning', 'packet')
    assert result[0] == 0, result
    assert json.loads(result[1])['report_template']
    current = load(state_file)
    assert current['phase'] == before['phase']
    assert current['rework_recovery'] == before['rework_recovery']
    assert current['proof_admission'] == before['proof_admission']


def test_planning_repair_preserves_pending_reassessment_and_checkpoint(cli, state_file, tmp_path):
    from launcher_context import admit_planning
    raw, ref = pending(cli, state_file, tmp_path)
    before = load(state_file)
    checkpoint_bytes = A.read(ref)
    plan_na_gates(raw, tmp_path)
    admit_planning(raw, tmp_path)
    current = load(state_file)
    assert current['proof_admission']['status'] == 'admitted'
    assert current['phase'] == 'REWORK_REASSESSMENT'
    assert current['rework'] == before['rework']
    assert current['rework_recovery'] == before['rework_recovery']
    assert Gates.marker(current)['checkpoint'] == ref['id']
    assert A.read(ref) == checkpoint_bytes


@pytest.mark.parametrize('mutation', ['command', 'edge', 'checkpoint', 'occurrence'])
def test_gate_continuation_corruption_is_still_rejected(cli, state_file, tmp_path, mutation):
    _, _ = pending(cli, state_file, tmp_path)
    altered = copy.deepcopy(load(state_file))
    value = altered['gate_recovery']
    if mutation == 'command':
        value['continuations'][-1]['command'] = 'unscoped-bypass'
    elif mutation == 'edge':
        value['continuations'][-1]['to'] = 'DONE'
    elif mutation == 'checkpoint':
        value['checkpoint'] = 'f' * 64
    else:
        value['continuations'][-1]['rework_occurrence'] = 'RR999'
    with pytest.raises(SystemExit):
        Gates.marker(altered)


@pytest.mark.parametrize('route', ['decision', 'implementation'])
def test_new_gate_edges_require_retained_authority(cli, state_file, tmp_path, route):
    raw, _ = pending(cli, state_file, tmp_path)
    if route == 'decision':
        path, _ = decision(raw, tmp_path)
        assert raw('rework-recovery', 'decide', '--file', str(path))[0] == 0
    else:
        assert raw('writer', 'start', '--id', 'fix', '--role', 'al-engineer', '--files',
                   'svc/app.py', '--degraded', '--reason', 'Defined repair')[0] == 0
        assert raw('writer', 'finish', '--id', 'fix', '--outcome', 'success')[0] == 0
    altered = copy.deepcopy(load(state_file))
    if route == 'decision':
        altered['rework_recovery']['decisions'].clear()
    else:
        altered['gate_recovery']['continuations'][-1]['continuity_hash'] = 'f' * 64
    with pytest.raises(SystemExit):
        Gates.marker(altered)


@pytest.mark.parametrize('condition', ['declined', 'cap'])
def test_explicit_user_continuation_keeps_gate_history_valid(cli, state_file, tmp_path, condition):
    raw, ref = pending(cli, state_file, tmp_path)
    if condition == 'declined':
        path, _ = decision(raw, tmp_path, decision='decline', basis='no-useful-next-step')
        assert raw('rework-recovery', 'decide', '--file', str(path))[0] == 0
    else:
        for number in (1, 2):
            path, _ = decision(raw, tmp_path, request_id='allow-' + str(number))
            assert raw('rework-recovery', 'decide', '--file', str(path))[0] == 0
            assert raw('phase', 'FINALIZATION')[0] == 0
            assert raw('rework', '--source', 'finalization', *fast(raw))[0] == 0
            assert raw('phase', 'FINALIZATION')[0] == 0
            assert raw('rework', '--source', 'finalization', *fast(raw))[0] == 10
    before = load(state_file)
    checkpoint_bytes = A.read(ref)
    path = user_continuation(raw, state_file, tmp_path)
    result = raw('rework-recovery', 'continue', '--file', str(path))
    assert result[0] == 0, result
    current = load(state_file)
    assert current['phase'] == 'FINALIZATION'
    assert current['rework']['total'] == before['rework']['total']
    assert current['rework']['used'] == 0
    assert current['proof_admission'] == before['proof_admission']
    assert current['gate_recovery']['continuations'][:len(before['gate_recovery']['continuations'])] == before['gate_recovery']['continuations']
    assert Gates.marker(current)['checkpoint'] == ref['id']
    assert A.read(ref) == checkpoint_bytes


@pytest.mark.parametrize('stop', ['interrupt', 'waiting'])
@pytest.mark.parametrize('route', ['decision', 'writer'])
def test_reassessment_departure_accepts_authenticated_resume(cli, state_file, tmp_path, stop, route):
    raw, ref = pending(cli, state_file, tmp_path)
    path = tmp_path / 'pause.json'
    path.write_text(json.dumps({'reason': 'Synthetic pause', 'resume_instructions': 'Resume same task'}))
    before = load(state_file)
    for _ in range(2):
        assert raw(stop, '--file', str(path))[0] == 0
        checkpoint = load(state_file)['stopping_checkpoint']
        assert raw('resume', '--checkpoint', checkpoint)[0] == 0
    if route == 'decision':
        path, _ = decision(raw, tmp_path)
        result = raw('rework-recovery', 'decide', '--file', str(path))
    else:
        result = raw('writer', 'start', '--id', 'resumed-fix', '--role', 'al-engineer',
                     '--files', 'svc/app.py', '--degraded', '--reason', 'Defined repair')
    assert result[0] == 0, result
    current = load(state_file)
    assert Gates.marker(current)['checkpoint'] == ref['id']
    assert current['proof_admission'] == before['proof_admission']
    if route == 'writer':
        assert current['rework_recovery'] == before['rework_recovery']
        assert raw('writer', 'finish', '--id', 'resumed-fix', '--outcome', 'success')[0] == 0


@pytest.mark.parametrize('mutation', ['resume-record', 'resume-checkpoint', 'user-receipt', 'user-checkpoint'])
def test_checkpoint_restore_edges_reject_missing_authority(cli, state_file, tmp_path, mutation):
    raw, ref = pending(cli, state_file, tmp_path)
    if mutation.startswith('resume'):
        path = tmp_path / 'interrupt.json'
        path.write_text(json.dumps({'reason': 'Synthetic interruption', 'resume_instructions': 'Resume'}))
        assert raw('interrupt', '--file', str(path))[0] == 0
        assert raw('resume', '--checkpoint', load(state_file)['stopping_checkpoint'])[0] == 0
    else:
        path, _ = decision(raw, tmp_path, decision='decline', basis='no-useful-next-step')
        assert raw('rework-recovery', 'decide', '--file', str(path))[0] == 0
        path = user_continuation(raw, state_file, tmp_path)
        assert raw('rework-recovery', 'continue', '--file', str(path))[0] == 0
    altered = copy.deepcopy(load(state_file))
    if mutation == 'resume-record':
        altered['resumes'].clear()
    elif mutation == 'user-receipt':
        altered['rework_recovery']['continuations'].clear()
    else:
        altered['gate_recovery']['continuations'][-1]['checkpoint'] = ref['id']
    with pytest.raises(SystemExit):
        Gates.marker(altered)


@pytest.mark.parametrize('phase,after', [
    ('IMPLEMENTATION', None), ('FINALIZATION', None),
    ('REWORK_REASSESSMENT', 'decision'), ('REWORK_REASSESSMENT', 'writer'),
])
def test_guarded_agent_retry_preserves_real_checkpoint_authority(cli, state_file, tmp_path, phase, after):
    raw, ref = pending(cli, state_file, tmp_path)
    if phase != 'REWORK_REASSESSMENT':
        assert raw('writer', 'start', '--id', 'continue', '--role', 'al-engineer', '--files',
                   'svc/app.py', '--degraded', '--reason', 'Defined work')[0] == 0
        assert raw('writer', 'finish', '--id', 'continue', '--outcome', 'success')[0] == 0
        if phase == 'FINALIZATION':
            assert raw('phase', 'FINALIZATION')[0] == 0
    role = 'al-architect' if phase == 'REWORK_REASSESSMENT' else 'al-engineer'
    before = load(state_file)
    assert raw('agent-failed', role, '--reason', 'transient assertion failure', '--retry-safe', 'yes')[0] == 0
    failed = load(state_file)
    failure_bytes = A.read(failed['failure']['checkpoint'])
    result = raw('retry', role, '--reason', 'Reconciled safe implementation retry')
    assert result[0] == 0, result
    current = load(state_file)
    assert Gates.marker(current)['checkpoint'] == ref['id']
    assert current['phase'] == phase
    assert current['failure'] is None and current['retries'][role] == 1
    assert current.get('resumes', []) == before.get('resumes', [])
    assert current['proof_admission'] == before['proof_admission']
    assert current['rework'] == before['rework']
    assert current['rework_recovery'] == before['rework_recovery']
    assert A.read(failed['failure']['checkpoint']) == failure_bytes
    corrupted = copy.deepcopy(current)
    corrupted['retry_history'].clear()
    with pytest.raises(SystemExit):
        Gates.marker(corrupted)
    if after == 'decision':
        path, _ = decision(raw, tmp_path)
        assert raw('rework-recovery', 'decide', '--file', str(path))[0] == 0
        assert Gates.marker(load(state_file))['checkpoint'] == ref['id']
    elif after == 'writer':
        assert raw('writer', 'start', '--id', 'after-retry', '--role', 'al-engineer', '--files',
                   'svc/app.py', '--degraded', '--reason', 'Defined work')[0] == 0
        assert raw('writer', 'finish', '--id', 'after-retry', '--outcome', 'success')[0] == 0
        assert Gates.marker(load(state_file))['checkpoint'] == ref['id']
