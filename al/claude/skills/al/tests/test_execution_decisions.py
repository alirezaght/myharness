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
