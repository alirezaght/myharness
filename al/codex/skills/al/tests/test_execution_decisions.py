import copy

import al_execution_contract as C
import al_execution_decisions as D
import al_store
import pytest

from execution_helpers import bound, register_task, report


def test_foreign_decision_and_unproven_revalidation_are_refused(cli, tmp_path, repo, monkeypatch):
    controller, _, sid = bound(cli, tmp_path, repo, monkeypatch)
    assert register_task(cli, tmp_path, 'A', ['svc'])[0] == 0
    st = al_store.load(sid)
    foreign = copy.deepcopy(st['integration']['binding']); foreign['approval_id'] += 1
    delivery = {'schema_version': 1, 'id': 'decision-one', 'binding': foreign, 'blocker_id': 'design', 'blocker_revision': 1, 'verdict': 'accept', 'text': 'Explicitly choose the alternative architecture for this task.', 'tasks': ['A'], 'criteria': ['AC-01'], 'amendment': False, 'registry': {'id': 'DEC-0001', 'path': '/tmp/foreign.md', 'sha256': 'hash'}, 'base_revision': 'base'}
    with pytest.raises(SystemExit, match='foreign'):
        D.apply_delivery(st, sid, delivery)
    code, _, _ = report(cli, tmp_path, 'revalidate', {'id': 'missing', 'agent': 'architect'})
    assert code != 0
    controller.conn.close()


def test_global_amendment_cannot_bypass_developer_delivery(cli, tmp_path, repo, monkeypatch):
    controller, _, sid = bound(cli, tmp_path, repo, monkeypatch)
    path = tmp_path / 'amendment.json'; path.write_text('{"id":"bypass","text":"Silently change approved requirements and continue anyway","source":"model"}')
    code, _, _ = cli('task', 'amend', '--file', str(path))
    assert code != 0 and al_store.load(sid)['amendments'] == []
    controller.conn.close()


def test_real_registry_rejection_amendment_ack_and_scoped_revalidation(cli, tmp_path, repo, monkeypatch, launcher):
    from harness.execution_decisions import record_execution_decision
    from harness.execution_projection import ingest_event
    from harness.execution_store import get_execution
    import json
    controller, eid, sid = bound(cli, tmp_path, repo, monkeypatch)
    assert cli('spec-gate', 'SPEC_READY')[0] == 0
    assert cli('classify', 'STANDARD', '--designer', 'no', '--implementers', 'al-engineer')[0] == 0
    assert register_task(cli, tmp_path, 'A', ['svc'])[0] == 0
    assert register_task(cli, tmp_path, 'B', ['docs'])[0] == 0
    assert report(cli, tmp_path, 'blocker', {'id':'design', 'kind': 'developer_engineering_decision','tasks':['A'],'question':'Choose architecture','context':'An explicit decision is required','alternatives':['one','two'],'recommendation':'one'})[0] == 0
    def sync():
        state = al_store.load(sid)
        cursor = get_execution(controller.conn, eid)['cursor']
        for event in state['integration']['outbox'][cursor:]:
            ingest_event(controller.conn, eid, event)
    sync()
    rejected = record_execution_decision(controller, eid, 'design', blocker_revision=1, request_id='reject-one', verdict='reject', decision_text='Reject the recommendation and keep this architecture decision open.', tasks=['A'], criteria=['AC-01'])
    delivery = json.loads(rejected['delivery'])
    from pathlib import Path
    from harness.execution_worker import dispatch_pending
    dispatch_pending(controller, eid, {'script': str(Path(al_store.__file__).parent / 'al_state.py'),
                                       'launcher': launcher.alctl_double(tmp_path)})
    sync()
    assert al_store.load(sid)['integration']['blockers']['design']['state'] == 'Open'
    # Lost acknowledgement: retry exact immutable delivery, with no new Registry ID.
    before = al_store.load(sid)['integration']['sequence']
    assert report(cli,tmp_path,'decision',delivery)[0] == 0
    assert al_store.load(sid)['integration']['sequence'] == before
    decided = record_execution_decision(controller, eid, 'design', blocker_revision=1, request_id='amend-one', verdict='alternative', decision_text='Use the alternate architecture for task A, with explicit scope limited to its acceptance criterion.', tasks=['A'], criteria=['AC-01'], amendment=True)
    code,out,err=report(cli,tmp_path,'decision',json.loads(decided['delivery']))
    assert code == 0,(code,out,err)
    state=al_store.load(sid)
    assert state['phase'] == 'PLANNING' and state['integration']['tasks']['B']['revision'] == 1
    assert state['integration']['tasks']['A']['revision'] == 2
    assert cli('integration','task-admit','--task','A')[0] != 0
    obligation=state['integration']['revalidations'][decided['id']]
    assert report(cli,tmp_path,'agent-register',{'id':'architect-review','name':'Scope architect','role':'al-architect'})[0] == 0
    assert report(cli,tmp_path,'agent-update',{'id':'architect-review','status':'Running'})[0] == 0
    revalidation={'id':decided['id'],'agent':'architect-review','packet_hash':obligation['packet_hash'],'criteria':['AC-01'],'applicable':True,'findings':[],'rationale':'Inspected the complete scoped amendment and verified decision applicability and acceptance coverage.'}
    code,out,err=report(cli,tmp_path,'revalidate',revalidation)
    assert code == 0,(code,out,err)
    sync()
    assert cli('integration','task-admit','--task','A')[0] == 0
    assert al_store.load(sid)['integration']['blockers']['design']['state'] == 'Resolved'
    controller.conn.close()


def _r4_escalation_fixture(cli, tmp_path, repo, monkeypatch):
    controller, eid, sid = bound(cli, tmp_path, repo, monkeypatch)
    assert cli('spec-gate', 'SPEC_READY')[0] == 0
    assert cli('classify', 'STANDARD', '--designer', 'no', '--implementers', 'al-engineer')[0] == 0
    assert register_task(cli, tmp_path, 'A', ['svc'])[0] == 0
    blocker = {'id': 'design', 'kind': 'developer_engineering_decision', 'tasks': ['A'],
               'question': 'Choose architecture', 'context': 'An explicit decision is required',
               'alternatives': ['one', 'two'], 'recommendation': 'one'}
    result = report(cli, tmp_path, 'blocker', blocker)
    assert result[0] == 0, result
    return controller, eid, sid, blocker


def _r4_sync(controller, eid, sid):
    from harness.execution_projection import ingest_event
    from harness.execution_store import get_execution
    st = al_store.load(sid)
    for event in st['integration']['outbox'][get_execution(controller.conn, eid)['cursor']:]:
        ingest_event(controller.conn, eid, event)


def test_r4_escalation_repair_identical_registration_after_graph_growth(cli, tmp_path, repo, monkeypatch):
    controller, _, sid, blocker = _r4_escalation_fixture(cli, tmp_path, repo, monkeypatch)
    original = copy.deepcopy(al_store.load(sid)['integration']['blockers']['design'])
    original_contract = copy.deepcopy(al_store.load(sid)['agent_contracts']['escalation-request:design'])
    assert register_task(cli, tmp_path, 'B', ['docs'])[0] == 0
    before = al_store.load(sid)
    assert before['integration']['graph_revision'] == 2
    result = report(cli, tmp_path, 'blocker', blocker)
    assert result[0] == 0, result
    current = al_store.load(sid)
    assert current['integration']['blockers']['design'] == original
    assert current['agent_contracts']['escalation-request:design'] == original_contract
    assert current['integration']['sequence'] == before['integration']['sequence']
    import al_artifacts as A
    initial = A.read_json(current['escalation_observations']['design'][0])
    assert initial['record'] == original and initial['contract'] == original_contract
    assert len(current['escalation_observations']['design']) == 1
    assert current['integration']['tasks']['B']['status'] == 'Idle'
    controller.conn.close()


def test_r4_escalation_repair_identical_registration_after_real_resolution(cli, tmp_path, repo, monkeypatch):
    import json
    from harness.execution_decisions import record_execution_decision
    controller, eid, sid, blocker = _r4_escalation_fixture(cli, tmp_path, repo, monkeypatch)
    original = copy.deepcopy(al_store.load(sid)['integration']['blockers']['design'])
    _r4_sync(controller, eid, sid)
    decided = record_execution_decision(
        controller, eid, 'design', blocker_revision=1, request_id='resolve-one', verdict='accept',
        decision_text='Use the recommended architecture with explicit acceptance limited to task A.',
        tasks=['A'], criteria=['AC-01'])
    delivery = json.loads(decided['delivery'])
    result = report(cli, tmp_path, 'decision', delivery)
    assert result[0] == 0, result
    _r4_sync(controller, eid, sid)
    before = al_store.load(sid)
    resolved = copy.deepcopy(before['integration']['blockers']['design'])
    assert original['state'] == 'Open' and resolved['state'] == 'Resolved'
    assert resolved['delivery_id'] == delivery['id']
    result = report(cli, tmp_path, 'blocker', blocker)
    assert result[0] == 0, result
    current = al_store.load(sid)
    import al_artifacts as A
    import al_agent_contracts as Contracts
    observations = [A.read_json(r) for r in current['escalation_observations']['design']]
    assert observations[0]['record'] == original
    assert observations[-1]['record'] == resolved
    assert len(observations) == 2
    assert current['agent_contracts']['escalation-request:design']['payload']['record_digest'] == Contracts.fingerprint(resolved)
    assert current['integration']['blockers']['design'] == resolved
    assert current['integration']['sequence'] == before['integration']['sequence']
    assert current['integration']['deliveries'][delivery['id']] == before['integration']['deliveries'][delivery['id']]
    assert report(cli, tmp_path, 'decision', delivery)[0] == 0
    unchanged = al_store.state_path(sid).read_bytes()
    assert report(cli, tmp_path, 'decision', dict(delivery, text='Conflicting forged decision reuse.'))[0] != 0
    assert al_store.state_path(sid).read_bytes() == unchanged
    from pathlib import Path
    proof = Path(observations[-1]['transition']['artifact']['stored_path'])
    proof.write_bytes(proof.read_bytes() + b' altered delivery proof')
    assert report(cli, tmp_path, 'blocker', blocker)[0] != 0
    assert al_store.state_path(sid).read_bytes() == unchanged
    controller.conn.close()


@pytest.mark.parametrize('tamper', ['registration', 'scope', 'revision', 'original', 'current',
                                   'unsupported', 'missing', 'authority', 'foreign-delivery', 'forged-delivery'])
def test_r4_escalation_repair_refuses_tampering_and_stale_authority(cli, tmp_path, repo, monkeypatch, tamper):
    import json
    from pathlib import Path
    import al_artifacts as A
    controller, eid, sid, blocker = _r4_escalation_fixture(cli, tmp_path, repo, monkeypatch)
    assert register_task(cli, tmp_path, 'B', ['docs'])[0] == 0
    st = al_store.load(sid)
    if tamper in {'registration', 'revision'}:
        st['integration']['blockers']['design'][tamper] = 'forged' if tamper == 'registration' else 2
        al_store.state_path(sid).write_text(json.dumps(st))
    elif tamper == 'scope':
        blocker = dict(blocker, tasks=['B'])
    elif tamper == 'current':
        st['agent_contracts']['escalation-request:design']['acceptance_criteria'] = ['forged']
        al_store.state_path(sid).write_text(json.dumps(st))
    elif tamper == 'original':
        path = Path(st['escalation_observations']['design'][0]['stored_path'])
        path.write_bytes(path.read_bytes() + b' changed original observation')
    elif tamper == 'missing':
        Path(st['escalation_observations']['design'][0]['stored_path']).unlink()
    elif tamper == 'unsupported':
        ref = st['escalation_observations']['design'][0]
        value = A.read_json(ref);value['schema_version'] = 99
        st['escalation_observations']['design'][0] = A.snapshot_bytes(sid, json.dumps(value).encode(), 'escalation-observation-v1', 'negative fixture')
        al_store.state_path(sid).write_text(json.dumps(st))
    elif tamper == 'authority':
        controller.conn.execute("UPDATE executions SET status='Completed' WHERE id=?", (eid,))
    else:
        from harness.execution_decisions import record_execution_decision
        _r4_sync(controller, eid, sid)
        decided = record_execution_decision(controller, eid, 'design', blocker_revision=1,
            request_id='foreign-one', verdict='reject' if tamper == 'forged-delivery' else 'accept', decision_text='Use the recommended architecture with explicit acceptance limited to task A.', tasks=['A'], criteria=['AC-01'])
        delivery = json.loads(decided['delivery'])
        if tamper == 'forged-delivery':
            assert delivery['verdict'] == 'reject'
            delivery['verdict'] = 'accept'
        else:
            delivery['binding']['approval_id'] += 1
        before = al_store.state_path(sid).read_bytes()
        assert report(cli, tmp_path, 'decision', delivery)[0] != 0
        assert al_store.state_path(sid).read_bytes() == before
        controller.conn.close()
        return
    before = al_store.state_path(sid).read_bytes()
    assert report(cli, tmp_path, 'blocker', blocker)[0] != 0
    assert al_store.state_path(sid).read_bytes() == before
    controller.conn.close()



def test_r4_escalation_repair_controlled_replacement_after_real_authority(cli, tmp_path, repo, monkeypatch):
    import json
    from pathlib import Path
    import al_execution_tasks as T
    from harness.execution_decisions import record_execution_decision
    controller, eid, sid, _ = _r4_escalation_fixture(cli, tmp_path, repo, monkeypatch)
    _r4_sync(controller, eid, sid)
    decided = record_execution_decision(controller, eid, 'design', blocker_revision=1,
        request_id='replace-one', verdict='reject',
        decision_text='Reject this proposal and keep the architecture decision open for review.',
        tasks=['A'], criteria=['AC-01'])
    actual = json.loads(decided['delivery'])
    forged = copy.deepcopy(actual);forged['verdict'] = 'accept'
    real_authority = T.authority
    witnessed = []
    def replace_after_authority(st, operation, task=None, delivery=None):
        receipt = real_authority(st, operation, task, delivery)
        if operation == 'decision':
            import hashlib
            assert receipt['allowed'] is True
            assert receipt['delivery_receipt'] == {'schema_version': 1, 'sha256': hashlib.sha256(decided['delivery'].encode()).hexdigest()}
            path = Path(st['integration']['handoff']).parent / 'inbox' / (delivery + '.json')
            assert json.loads(path.read_bytes()) == actual
            witnessed.append(receipt)
            path.write_text(json.dumps(forged))
        return receipt
    monkeypatch.setattr(T, 'authority', replace_after_authority)
    before = al_store.state_path(sid).read_bytes()
    result = report(cli, tmp_path, 'decision', forged)
    assert len(witnessed) == 1
    assert result[0] != 0, result
    assert al_store.state_path(sid).read_bytes() == before
    controller.conn.close()



@pytest.mark.parametrize('damage', ['missing', 'version', 'digest', 'extra'])
def test_r4_escalation_repair_closed_real_decision_receipt(cli, tmp_path, repo, monkeypatch, damage):
    import json
    import al_execution_tasks as T
    from harness.execution_decisions import record_execution_decision
    controller, eid, sid, _ = _r4_escalation_fixture(cli, tmp_path, repo, monkeypatch)
    _r4_sync(controller, eid, sid)
    decided = record_execution_decision(controller, eid, 'design', blocker_revision=1,
        request_id='receipt-one', verdict='accept',
        decision_text='Accept the recommended architecture with explicit scope limited to task A.',
        tasks=['A'], criteria=['AC-01'])
    real_loads = T.loads
    witnessed = []
    def damage_real_receipt(raw, *args, **kwargs):
        value = real_loads(raw, *args, **kwargs)
        if isinstance(value, dict) and value.get('allowed') is True:
            witnessed.append(True)
            if damage == 'missing':
                value.pop('delivery_receipt')
            elif damage == 'version':
                value['delivery_receipt']['schema_version'] = True
            elif damage == 'digest':
                value['delivery_receipt']['sha256'] = 'G' * 64
            else:
                value['delivery_receipt']['untrusted'] = True
        return value
    monkeypatch.setattr(T, 'loads', damage_real_receipt)
    before = al_store.state_path(sid).read_bytes()
    result = report(cli, tmp_path, 'decision', json.loads(decided['delivery']))
    assert result[0] != 0 and witnessed == [True], result
    assert al_store.state_path(sid).read_bytes() == before
    controller.conn.close()
