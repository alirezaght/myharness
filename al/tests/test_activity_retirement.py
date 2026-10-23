"""Workspace history facts never manufacture approval or waive live operations."""
import copy
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from al.installer import activity_retirement as A
from al.installer.activity_inventory import inventory_activity
from al.installer.activity_observation import locked_activity, observe_activity, quiescence_context
from al.installer.activity_profiles import classify_artifact
from al.installer.registry import canonical, digest
from al.tests.activity_helpers import SESSION, legacy, modern, planned, scenario, write_state


def test_real_inventory_profiles_and_exact_history_acknowledgement_preserve_bytes(tmp_path, monkeypatch):
    case = scenario(tmp_path, monkeypatch)
    assert case['inventory']['stable']
    assert [r['classification'] for root in case['inventory']['roots'] for r in root['records']] == ['current-session', 'legacy-session']
    before = {p: p.read_bytes() for root in case['inventory']['roots'] for p in Path(root['runtime_root']).rglob('*') if p.is_file()}
    plan = planned(case, tmp_path)
    with quiescence_context(plan) as check:
        result = check()['assessment']
        assert result['eligible']
        assert {r['assessment'] for r in result['records']} == {'ordinary-terminal', 'acknowledged-inactive-history'}
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert json.loads(case['old_path'].read_text())['status'] == 'RUNNING'


@pytest.mark.parametrize('mutation', ['new', 'changed', 'mode', 'new-intent-new-directory'])
def test_original_facts_never_cover_future_history_even_with_new_intent(tmp_path, monkeypatch, mutation):
    case = scenario(tmp_path, monkeypatch)
    planned(case, tmp_path)
    if mutation == 'new':write_state(case['old_runtime'], legacy('new-session'))
    elif mutation == 'mode':case['old_path'].chmod(0o600)
    else:
        value = json.loads(case['old_path'].read_text());value['task'] += ' Changed history.'
        write_state(case['old_runtime'], value)
    fresh = inventory_activity(case['roots'], invoking_run=case['invoking'])
    request = dict(case['request'], deployment_intent_digest='c' * 64, directory=str(tmp_path / 'different-acknowledgements'))
    with pytest.raises(ValueError, match='historical|facts|factual'):
        A.plan_acknowledgements(fresh, request, excluded_run=case['invoking'])


def test_same_facts_and_history_are_create_once_across_intents(tmp_path, monkeypatch):
    case = scenario(tmp_path, monkeypatch)
    first = A.plan_acknowledgements(case['inventory'], case['request'], excluded_run=case['invoking'])
    assert A.plan_acknowledgements(case['inventory'], case['request'], excluded_run=case['invoking']) == first
    second = A.plan_acknowledgements(case['inventory'], dict(case['request'], deployment_intent_digest='c' * 64), excluded_run=case['invoking'])
    assert second != first
    old = A.read_reference(first['artifacts'][0]);new = A.read_reference(second['artifacts'][0])
    assert old['reviewed_history'] == new['reviewed_history']


@pytest.mark.parametrize('mutation', ['unknown-key', 'hybrid', 'unknown-status', 'duplicate', 'nonfinite', 'missing-task', 'bad-counter'])
def test_closed_legacy_profiles_refuse_unknown_or_corrupt_state(tmp_path, monkeypatch, mutation):
    case = scenario(tmp_path, monkeypatch)
    value = legacy()
    if mutation == 'unknown-key':value['live_lease'] = {'pid': os.getpid()}
    elif mutation == 'hybrid':value['writers'] = {}
    elif mutation == 'unknown-status':value['status'] = 'DEAD'
    elif mutation == 'missing-task':value.pop('task')
    elif mutation == 'bad-counter':value['review_iteration'] = True
    raw = canonical(value)
    if mutation == 'duplicate':raw = raw[:-1] + b',"status":"RUNNING"}'
    elif mutation == 'nonfinite':raw = raw.replace(b'100.0', b'NaN')
    result = classify_artifact(case['old_path'], raw, provenance={})
    assert result['classification'] == 'unknown' and result['errors']


def test_serialized_observation_and_empty_sets_cannot_claim_quiescence(tmp_path, monkeypatch):
    case = scenario(tmp_path, monkeypatch);plan = planned(case, tmp_path)
    with locked_activity(case['inventory']) as (locks, limitations):
        observed = observe_activity(case['inventory'], locks=locks, limitations=limitations)
        forged = json.loads(canonical(observed))
        for key in ('processes', 'operations', 'locks', 'leases'):forged[key] = []
        forged = A.seal({k: v for k, v in forged.items() if k != 'digest'})
        with pytest.raises(ValueError, match='internal operational observation'):
            A.require_workspace_quiescent(plan, plan['activity']['acknowledgements'], observation=forged)
        assert A.require_workspace_quiescent(plan, plan['activity']['acknowledgements'], observation=observed)['eligible']
        with pytest.raises(ValueError, match='new internal'):
            A.require_workspace_quiescent(plan, plan['activity']['acknowledgements'], observation=observed)


@pytest.mark.parametrize('operation', ['writer', 'check', 'interrupted', 'lease', 'invoking-running'])
def test_facts_never_waive_actual_unresolved_operations(tmp_path, monkeypatch, operation):
    case = scenario(tmp_path, monkeypatch)
    state = json.loads(case['current_path'].read_text())
    if operation == 'writer':state['writers']['w'] = {'id': 'w', 'state': 'ACTIVE'}
    elif operation in {'check', 'interrupted'}:
        state['evidence'] = {'seq': 1, 'plan': {}, 'entries': [{'id': 'E1', 'argv': ['true'], 'run': state['run_id'],
            'reservation': 'token', 'status': 'RUNNING' if operation == 'check' else 'INFRA', 'interrupted': operation == 'interrupted'}]}
    elif operation == 'lease':(case['runtime'] / 'owned.lease').write_text('unknown owner')
    else:state.update(status='RUNNING', phase='IMPLEMENTATION')
    write_state(case['runtime'], state)
    case['inventory'] = inventory_activity(case['roots'], invoking_run=case['invoking'])
    plan = planned(case, tmp_path)
    with pytest.raises(ValueError, match='blocked'):
        with quiescence_context(plan):pass


def test_real_runtime_lock_contention_refuses_without_runtime_writes(tmp_path, monkeypatch):
    case = scenario(tmp_path, monkeypatch);plan = planned(case, tmp_path)
    path = case['runtime'] / ('.' + SESSION + '.lock')
    ready = tmp_path / 'ready'
    code = 'import fcntl,time;from pathlib import Path;f=open('+repr(str(path))+',"rb");fcntl.flock(f,fcntl.LOCK_EX);Path('+repr(str(ready))+').write_text("ready");time.sleep(60)'
    child = subprocess.Popen([sys.executable, '-c', code])
    import time
    try:
        until = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < until:time.sleep(.01)
        assert ready.exists()
        before = path.read_bytes()
        with pytest.raises(BlockingIOError):
            with quiescence_context(plan):pass
        assert path.read_bytes() == before
    finally:child.terminate();child.wait(timeout=3)


def test_runtime_pointer_and_symlink_conflicts_remain_incomplete(tmp_path, monkeypatch):
    case = scenario(tmp_path, monkeypatch)
    (case['runtime'] / 'current').write_text('missing-session')
    result = inventory_activity(case['roots'], invoking_run=case['invoking'])
    assert not result['stable'] and any(root['errors'] for root in result['roots'])
    (case['runtime'] / 'current').unlink();(case['runtime'] / 'current').symlink_to(case['old_path'])
    assert not inventory_activity(case['roots'], invoking_run=case['invoking'])['stable']


@pytest.mark.parametrize('kind', ['task-package', 'user-amendment', 'gate-manifest'])
def test_nonstate_requires_real_matching_immutable_reference_not_filename(tmp_path, monkeypatch, kind):
    case = scenario(tmp_path, monkeypatch)
    state = json.loads(case['current_path'].read_text())
    if kind == 'task-package':value = {'accepted_spec': 'A real referenced input.'};field = 'task_package'
    elif kind == 'user-amendment':
        value = {'id': 'AM1', 'text': 'User adds a requirement.', 'source': 'Explicit user request in active conversation.', 'affected_criteria': ['AC1']};field = 'amendments'
    else:
        value = {'schema_version': 1, 'id': 'G1', 'revision': 1, 'gates': [], 'rules': [], 'units': [], 'paths': [], 'base_oid': 'a' * 40};field = 'gate_manifests'
    input_path = case['runtime'] / 'input.json';input_path.write_text(json.dumps(value, indent=2))
    stored = case['runtime'] / SESSION / 'artifacts/nonstate';stored.write_bytes(canonical(value))
    reference = {'kind': kind, 'source': str(input_path), 'stored_path': str(stored), 'sha256': digest(stored.read_bytes())}
    if field == 'task_package':state[field] = reference
    elif field == 'amendments':state[field] = [{'artifact': reference}]
    else:state[field] = [reference]
    write_state(case['runtime'], state)
    inventory = inventory_activity(case['roots'], invoking_run=case['invoking'])
    assert inventory['stable']
    record = next(r for r in inventory['roots'][0]['records'] if r['identity']['state_path'] == str(input_path))
    assert record['classification'] == 'nonstate-artifact'
    assert record['sha256'] == digest(input_path.read_bytes()) != reference['sha256']
    input_path.write_text(json.dumps({'accepted_spec': 'Different unreferenced input.'}))
    assert not inventory_activity(case['roots'], invoking_run=case['invoking'])['stable']


def test_modern_artifact_corruption_and_unknown_operation_fields_remain_blocking(tmp_path, monkeypatch):
    case = scenario(tmp_path, monkeypatch)
    state = json.loads(case['current_path'].read_text())
    Path(state['task_package']['stored_path']).write_text('corrupt immutable task')
    assert not inventory_activity(case['roots'], invoking_run=case['invoking'])['stable']


def test_held_lock_capability_cannot_be_replayed_after_context_exit(tmp_path, monkeypatch):
    case = scenario(tmp_path, monkeypatch);plan = planned(case, tmp_path)
    with locked_activity(case['inventory']) as (locks, limitations):
        observation = observe_activity(case['inventory'], locks=locks, limitations=limitations)
    with pytest.raises(ValueError, match='lock capability is not live'):
        A.require_workspace_quiescent(plan, plan['activity']['acknowledgements'], observation=observation)


def test_real_explicit_session_process_overrides_historical_inactivity_fact(tmp_path, monkeypatch):
    case = scenario(tmp_path, monkeypatch);plan = planned(case, tmp_path)
    script = tmp_path / 'al_state.py';script.write_text('import time;time.sleep(60)')
    child = subprocess.Popen([sys.executable, str(script), '--session', 'historical'], cwd=case['old_runtime'].parents[1])
    try:
        with pytest.raises(ValueError, match='live or unknown relevant process'):
            with quiescence_context(plan):pass
    finally:child.terminate();child.wait(timeout=3)


def test_typed_json_value_change_is_not_nonstate_reference_equality(tmp_path):
    from al.installer.activity_nonstate import recognize
    value = {'accepted_spec': 'Exact values matter.', 'flag': 1}
    stored = tmp_path / 'snapshot';stored.write_bytes(canonical(value))
    path = tmp_path / 'input.json'
    reference = {'kind': 'task-package', 'stored_path': str(stored), 'sha256': digest(stored.read_bytes())}
    assert recognize(path, canonical(dict(value, flag=True)), [{'path': str(path), 'reference': reference}]) is None


def test_incomplete_internal_process_enumeration_cannot_approve(tmp_path, monkeypatch):
    from al.installer import activity_observation as O
    case = scenario(tmp_path, monkeypatch);plan = planned(case, tmp_path)
    with locked_activity(case['inventory']) as (locks, limitations):
        monkeypatch.setattr(O, '_processes', lambda _: [])
        observation = observe_activity(case['inventory'], locks=locks, limitations=limitations)
        with pytest.raises(ValueError, match='incomplete operational process'):
            A.require_workspace_quiescent(plan, plan['activity']['acknowledgements'], observation=observation)


def test_replaced_lock_inode_invalidates_actual_held_capability(tmp_path, monkeypatch):
    case = scenario(tmp_path, monkeypatch);plan = planned(case, tmp_path)
    with locked_activity(case['inventory']) as (locks, limitations):
        observation = observe_activity(case['inventory'], locks=locks, limitations=limitations)
        path = case['runtime'] / ('.' + SESSION + '.lock')
        path.unlink();path.write_bytes(b'')
        with pytest.raises(ValueError, match='lock identity changed'):
            A.require_workspace_quiescent(plan, plan['activity']['acknowledgements'], observation=observation)


def test_kernel_process_cwd_is_the_actual_current_directory():
    from al.installer.activity_process import process_cwd
    assert process_cwd(os.getpid()) == Path.cwd().resolve()


@pytest.mark.parametrize('empty_acknowledgements', [False, True])
def test_admission_cannot_omit_entire_historical_root_even_without_acknowledgements(tmp_path, monkeypatch, empty_acknowledgements):
    case = scenario(tmp_path, monkeypatch);plan = planned(case, tmp_path)
    subset = inventory_activity(case['roots'][:1], invoking_run=case['invoking'])
    plan['activity']['inventory'] = A.publish_once(tmp_path / 'subset.json', subset)
    if empty_acknowledgements:plan['activity']['acknowledgements'] = []
    with pytest.raises(ValueError, match='original historical runtime-root coverage'):
        with quiescence_context(plan):pass
    with pytest.raises(ValueError, match='original historical runtime-root coverage'):
        A.plan_acknowledgements(subset, dict(case['request'], directory=str(tmp_path / 'new-location'), deployment_intent_digest='d' * 64), excluded_run=case['invoking'])


def test_historical_membership_drift_cannot_gain_new_intent_acknowledgement(tmp_path, monkeypatch):
    case = scenario(tmp_path, monkeypatch)
    planned(case, tmp_path)
    (case['old_runtime'] / 'unreviewed-owner.log').write_text('new historical member')
    fresh = inventory_activity(case['roots'], invoking_run=case['invoking'])
    with pytest.raises(ValueError, match='different history'):
        A.plan_acknowledgements(fresh, dict(case['request'], deployment_intent_digest='d' * 64), excluded_run=case['invoking'])


@pytest.mark.parametrize('layout', ['legacy', 'session'])
def test_exact_current_run_files_are_excluded_without_excluding_unknown_history(tmp_path, monkeypatch, layout):
    case = scenario(tmp_path, monkeypatch)
    original = A.plan_acknowledgements(case['inventory'], case['request'], excluded_run=case['invoking'])
    state = json.loads(case['current_path'].read_text())
    log = case['runtime'] / (SESSION if layout == 'session' else '') / 'logs/current-owned.log'
    log.parent.mkdir();log.write_text('exact current run log')
    state['evidence'] = {'seq': 1, 'plan': {}, 'entries': [{'id': 'E1', 'run': state['run_id'], 'reservation': 'token',
        'argv': ['true'], 'status': 'PASS', 'log': str(log)}]}
    write_state(case['runtime'], state)
    fresh = inventory_activity(case['roots'], invoking_run=case['invoking'])
    updated = A.plan_acknowledgements(fresh, dict(case['request'], deployment_intent_digest='d' * 64), excluded_run=case['invoking'])
    assert updated['reviewed_history'] == original['reviewed_history']
    unknown = case['runtime'] / 'logs/unknown-owned.log';unknown.parent.mkdir(exist_ok=True)
    unknown.write_text('not attributed to the current run')
    fresh = inventory_activity(case['roots'], invoking_run=case['invoking'])
    with pytest.raises(ValueError, match='different history'):
        A.plan_acknowledgements(fresh, dict(case['request'], deployment_intent_digest='e' * 64), excluded_run=case['invoking'])


@pytest.mark.parametrize('field', ['worker', 'owner'])
@pytest.mark.parametrize('condition', ['live', 'absent', 'reused-start', 'reused-after-end', 'denied', 'ambiguous'])
def test_known_non_al_worker_pid_requires_actual_kernel_identity(tmp_path, monkeypatch, field, condition):
    import time
    from al.installer import activity_observation as O
    from al.installer.activity_process import identity, process_started_at
    case = scenario(tmp_path, monkeypatch)
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
    try:
        observed = identity(child.pid)
        assert observed is not None
        recorded = copy.deepcopy(observed)
        if condition == 'reused-start':recorded['start'][0] -= 1
        state = json.loads(case['current_path'].read_text())
        entry = {'id': 'E1', 'argv': ['actual-child'], 'run': state['run_id'], 'reservation': 'token', 'status': 'PASS', 'ended_at': time.time() + 100}
        if field == 'worker':entry.update(pid=child.pid, process={'pid': child.pid, 'identity': recorded})
        else:entry.update(owner_pid=child.pid, owner_identity=recorded)
        if condition in {'ambiguous', 'reused-after-end'}:
            if field == 'worker':entry['process'] = {'pid': child.pid}
            else:entry['owner_identity'] = {}
        if condition == 'reused-after-end':entry['ended_at'] = process_started_at(observed) - 10
        if condition == 'absent':child.terminate();child.wait(timeout=3)
        if condition == 'denied':
            real = O.kernel_identity
            def denied(pid):
                if pid == child.pid:raise PermissionError('kernel PID observation denied')
                return real(pid)
            monkeypatch.setattr(O, 'kernel_identity', denied)
        state['evidence'] = {'seq': 1, 'plan': {}, 'entries': [entry]}
        write_state(case['runtime'], state)
        case['inventory'] = inventory_activity(case['roots'], invoking_run=case['invoking'])
        plan = planned(case, tmp_path)
        if condition in {'live', 'denied', 'ambiguous'}:
            with pytest.raises(ValueError, match='live or unknown relevant process'):
                with quiescence_context(plan):pass
        else:
            with quiescence_context(plan) as check:assert check()['assessment']['eligible']
    finally:
        if child.poll() is None:child.terminate()
        child.wait(timeout=3)


@pytest.mark.parametrize('mutation', ['valid', 'unknown', 'empty-model', 'empty-reason', 'nonfinite', 'not-list'])
def test_typed_historical_restoration_metadata(tmp_path, monkeypatch, mutation):
    case = scenario(tmp_path, monkeypatch)
    value = legacy();row = {'model': 'fable', 'reason': 'user explicitly restored model availability', 'at': 1790252755.698237}
    if mutation == 'unknown':row['approved'] = True
    elif mutation == 'empty-model':row['model'] = ''
    elif mutation == 'empty-reason':row['reason'] = ''
    elif mutation == 'nonfinite':row['at'] = float('nan')
    value['model_restorations'] = [row] if mutation != 'not-list' else {}
    result = classify_artifact(case['old_path'], json.dumps(value).encode(), provenance={})
    assert (result['classification'] == 'legacy-session') == (mutation == 'valid')


@pytest.mark.parametrize('mutation', ['valid', 'corrupt', 'missing', 'symlink', 'unknown-key', 'relative', 'non-object', 'wrong-position'])
def test_only_explicit_hash_bound_supporting_source_may_be_external(tmp_path, monkeypatch, mutation):
    case = scenario(tmp_path, monkeypatch)
    source = tmp_path / 'supporting.json';source.write_text('{"kind":"supporting-source","approval":false}')
    value = json.loads(case['current_path'].read_text())
    reference = {'stored_path': str(source), 'sha256': digest(source.read_bytes())}
    if mutation == 'corrupt':source.write_text('{"changed":true}')
    elif mutation == 'missing':source.unlink()
    elif mutation == 'symlink':
        target = tmp_path / 'actual.json';source.rename(target);source.symlink_to(target)
    elif mutation == 'unknown-key':reference['approved'] = True
    elif mutation == 'relative':reference['stored_path'] = 'supporting.json'
    elif mutation == 'non-object':source.write_text('[]');reference['sha256'] = digest(source.read_bytes())
    if mutation == 'wrong-position':value['task_package'] = reference
    else:value['amendments'] = [{'supporting_source_artifact': reference}]
    write_state(case['runtime'], value)
    result = inventory_activity(case['roots'], invoking_run=case['invoking'])
    record = next(r for root in result['roots'] for r in root['records'] if r['identity']['state_path'] == str(case['current_path']))
    assert (record['classification'] == 'current-session') == (mutation == 'valid')
    assert (not record['errors']) == (mutation == 'valid')
    if mutation == 'valid':
        assert record['historical_status'] == 'APPROVED' and not record['operation_facts']['writers']
        source.write_text('{"changed after initial observation":true}')
        assert not inventory_activity(case['roots'], invoking_run=case['invoking'])['stable']


def dated_request(case, tmp_path, statement='I just want al removed from all other repos; they will restart al'):
    path = tmp_path / 'dated-statement.json'
    path.write_bytes(canonical({'schema_version': 1, 'kind': A.DATED_KIND, 'source': 'exact user message in the installing conversation',
                                'recorded_at': '2026-10-23T18:00:00+00:00', 'question': 'Acknowledge the unfinished runs?',
                                'statement': statement, 'scope': A.DATED_SCOPE, 'limits': ['installation-time inactivity only']}))
    return dict(case['request'], source_artifact={'path': str(path), 'sha256': digest(path.read_bytes())}, history=None)


def test_dated_user_statement_acknowledges_every_other_session_exactly(tmp_path, monkeypatch):
    case = scenario(tmp_path, monkeypatch)
    request = dated_request(case, tmp_path)
    references = A.plan_acknowledgements(case['inventory'], request, excluded_run=case['invoking'])
    acknowledgement = A.read_reference(references['artifacts'][0])
    assert acknowledgement['scope'] == A.DATED_SCOPE and len(acknowledgement['covered_records']) == 1
    assert acknowledgement['covered_records'][0]['identity']['session_id'] == 'historical'
    plan = {'source': request['source_binding'],
            'activity': {'inventory': A.publish_once(tmp_path / 'inventory.json', case['inventory']),
                         'reviewed_history': references['reviewed_history'], 'acknowledgements': references['artifacts'],
                         'deployment_intent_digest': request['deployment_intent_digest']}}
    with quiescence_context(plan) as check:
        result = check()['assessment']
        assert result['eligible'] and {r['assessment'] for r in result['records']} == {'ordinary-terminal', 'acknowledged-inactive-history'}
    # The statement file is bound by exact bytes: editing it refuses.
    Path(request['source_artifact']['path']).write_text('{}')
    with pytest.raises(ValueError, match='dated user statement changed'):
        with quiescence_context(plan) as check:check()


def test_dated_statement_never_covers_a_changed_inventory(tmp_path, monkeypatch):
    case = scenario(tmp_path, monkeypatch)
    request = dated_request(case, tmp_path)
    references = A.plan_acknowledgements(case['inventory'], request, excluded_run=case['invoking'])
    write_state(Path(case['roots'][1]['runtime_root']), legacy(session='later-run'))
    fresh = inventory_activity(case['roots'], invoking_run=case['invoking'])
    plan = {'source': request['source_binding'],
            'activity': {'inventory': A.publish_once(tmp_path / 'fresh-inventory.json', fresh),
                         'reviewed_history': references['reviewed_history'], 'acknowledgements': references['artifacts'],
                         'deployment_intent_digest': request['deployment_intent_digest']}}
    with pytest.raises(ValueError):
        with quiescence_context(plan) as check:check()
