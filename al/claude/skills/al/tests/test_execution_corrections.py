"""Correction crosses terminal boundary only after real guarded completion."""

import json
import sys
import subprocess
import pytest

import al_store
from execution_helpers import bound, register_task, report
from hook_regression_helpers import invoke, manifest_for, approve_route
from launcher_context import admit_planning


def completed_bound(cli, tmp_path, repo, monkeypatch):
    controller, eid, sid = bound(cli, tmp_path, repo, monkeypatch)
    invoke(cli, 'spec-gate', 'SPEC_READY')
    invoke(cli, 'classify', 'SMALL', '--designer', 'no', '--implementers', 'al-engineer')
    path = tmp_path / 'gates.json'
    path.write_text(json.dumps(manifest_for(al_store.load(sid), repo, ['svc', 'docs'])))
    invoke(cli, 'gates', 'plan', '--file', str(path))
    invoke(cli, 'gates', 'run', '--stage', 'base')
    admit_planning(cli)
    invoke(cli, 'phase', 'IMPLEMENTATION')
    assert register_task(cli, tmp_path, 'A', ['svc', 'docs', 'web', 'pyproject.toml', '.gitignore'])[0] == 0
    assert report(cli, tmp_path, 'agent-register', {'id':'engineer','name':'Actual test engineer','role':'al-engineer','task':'A'})[0] == 0
    assert report(cli, tmp_path, 'agent-update', {'id':'engineer','status':'Running'})[0] == 0
    invoke(cli, 'integration', 'task-start', '--task', 'A')
    invoke(cli, 'writer', 'start', '--id', 'engineer', '--role', 'al-engineer', '--files', 'svc', '--task', 'A', '--agent', 'engineer')
    (repo / 'svc/app.py').write_text('x = 2\n')
    invoke(cli, 'writer', 'finish', '--id', 'engineer', '--outcome', 'success')
    invoke(cli, 'evidence', 'plan', '--area', 'unit', '--tier', 'broad', '--scope', 'svc', '--', sys.executable, '-c', 'print("verified")')
    invoke(cli, 'evidence', 'run', '--area', 'unit')
    invoke(cli, 'integration', 'task-finish', '--task', 'A')
    assert report(cli, tmp_path, 'agent-update', {'id':'engineer','status':'Completed'})[0] == 0
    invoke(cli, 'gates', 'run', '--stage', 'candidate')
    invoke(cli, 'phase', 'VERIFICATION')
    result = approve_route(cli, tmp_path)
    assert result[0] == 0, result
    from harness.execution_projection import ingest_event
    for event in al_store.load(sid)['integration']['outbox']:
        ingest_event(controller.conn, eid, event)
    return controller, eid, sid


def test_guarded_correction_ack_before_restart_and_fresh_review_required(cli, tmp_path, repo, monkeypatch):
    controller, eid, sid = completed_bound(cli, tmp_path, repo, monkeypatch)
    from harness.delivery_models import DeliveryConfig
    from harness.delivery_store import configure_delivery
    from harness.execution_contract import canonical, digest
    from harness.execution_store import get_execution
    from harness.execution_projection import ingest_event
    execution = get_execution(controller.conn, eid)
    configure_delivery(controller, eid, DeliveryConfig('https://gitlab.example', 'group/project', 'candidate', 'main', 'staging'))
    observation = {'pipeline': {'id': 42, 'sha': 'a'*40, 'status':'failed'}}
    controller.conn.execute("UPDATE deliveries SET candidate_sha=?,snapshot=?,retries=1 WHERE execution_id=?", ('a'*40,canonical(observation),eid))
    request = {'schema_version':1,'id':'correction-test','binding':al_store.load(sid)['integration']['binding'],
               'generation':1,'candidate_sha':'a'*40,'pipeline_id':42,'prior_completion_digest':digest(execution['result']),
               'observation_digest':digest(observation),'observation':observation,'tasks':['A']}
    from pathlib import Path
    directory = Path(execution['handoff_path']).parent / 'corrections';directory.mkdir()
    path = directory / 'correction-test.json';path.write_text(canonical(request))
    controller.conn.execute("INSERT INTO delivery_corrections VALUES (?,?,?,?,?,?,?,NULL)",
                            ('correction-test',eid,1,'a'*40,42,canonical(request),'Admitted'))
    invoke(cli,'integration','delivery-correction','--file',str(path))
    changed = al_store.load(sid)
    assert changed['status']=='RUNNING' and changed['phase']=='PLANNING'
    assert 'approval' not in changed and 'review' not in changed
    assert changed['integration']['tasks']['A']['revision']==2
    old_cursor = execution['cursor']
    facts = changed['integration']['outbox'][old_cursor:]
    assert facts[0]['kind']=='delivery.correction.acknowledged'
    assert facts[1]['kind']=='run.started'
    # Restart gap: AL committed but Harness still terminal until canonical outbox replay.
    assert get_execution(controller.conn,eid)['status']=='Completed'
    for event in facts:ingest_event(controller.conn,eid,event)
    assert get_execution(controller.conn,eid)['status']=='Running'
    assert controller.conn.execute('SELECT count(*) FROM execution_completion_history').fetchone()[0]==1
    invoke(cli,'integration','delivery-correction','--file',str(path))
    assert al_store.load(sid)['integration']['correction_generation']==1
    assert cli('approved')[0] != 0
    controller.conn.close()


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_failed_pipeline_routes_through_guarded_al_then_reports_bounded_result(cli,tmp_path,repo,monkeypatch,launcher,provider):
    controller,eid,sid=completed_bound(cli,tmp_path,repo,monkeypatch)
    from harness.delivery_models import DeliveryConfig
    from harness.delivery_store import configure_delivery,get_delivery
    from harness.delivery_routing import route_failure
    from harness.execution_contract import canonical
    from harness.execution_store import get_execution
    from harness.execution_projection import ingest_event
    import uuid
    configure_delivery(controller,eid,DeliveryConfig('https://gitlab.example','group/project',
        'candidate','main','staging',retry_limit=1,provider=provider))
    repro=[sys.executable,'-I','-B','-c',"import runpy; assert runpy.run_path('svc/app.py')['x']==3, 'fixable pipeline error'"]
    external=subprocess.run(repro,cwd=repo,capture_output=True,text=True,timeout=10)
    assert external.returncode==1 and 'fixable pipeline error' in external.stderr
    failed={'id':42,'sha':'a'*40,'status':'failed'}
    snapshot={'pipeline':failed,'blockers':[]}
    controller.conn.execute('UPDATE deliveries SET candidate_sha=?,snapshot=? WHERE execution_id=?',
                            ('a'*40,canonical(snapshot),eid))
    controller.conn.execute('UPDATE executions SET native_id=? WHERE id=?',(str(uuid.uuid4()),eid))
    monkeypatch.setattr('harness.delivery_routing.installed_package',lambda *a:{'launcher':'fixture'})
    monkeypatch.setattr('harness.delivery_routing.al_argv',lambda l,h,r,s,*args,**kw:
                        launcher.child_argv(r,s,False,args))
    resumes=[]
    monkeypatch.setattr('harness.execution_recovery.resume_execution',lambda c,e:resumes.append(e))
    jobs=[{'id':7,'status':'failed','failure_reason':'script_failure'}]
    route_failure(controller,eid,failed,jobs)
    assert resumes==[eid]
    changed=al_store.load(sid)
    assert changed['status']=='RUNNING' and changed['phase']=='PLANNING'
    assert get_execution(controller.conn,eid)['status']=='Running'
    assert controller.conn.execute('SELECT state FROM delivery_corrections').fetchone()[0]=='Acknowledged'
    assert get_delivery(controller.conn,eid)['retries']==1
    invoke(cli,'approved',ok=False)
    # Scripted engineer repairs the deliberately broken implementation through a fresh writer.
    manifest=tmp_path/'corrected-gates.json'
    manifest.write_text(json.dumps(manifest_for(changed,repo,['svc','docs'])))
    invoke(cli,'gates','plan','--file',str(manifest))
    invoke(cli,'gates','run','--stage','base')
    admit_planning(cli)
    invoke(cli,'evidence','plan','--area','ci-repro','--kind','check','--tier','broad','--scope','svc','--',*repro)
    red=invoke(cli,'evidence','run','--area','ci-repro',ok=False)
    assert red[0]==1 and al_store.load(sid)['evidence']['entries'][-1]['status']=='FAIL'
    invoke(cli,'phase','IMPLEMENTATION')
    registration=report(cli,tmp_path,'agent-register',{'id':'repair-agent','name':'Correction engineer','role':'al-engineer','task':'A'})
    assert registration[0]==0,registration
    running=report(cli,tmp_path,'agent-update',{'id':'repair-agent','status':'Running'})
    assert running[0]==0,running
    invoke(cli,'integration','task-start','--task','A')
    invoke(cli,'writer','start','--id','repair','--role','al-engineer','--files','svc','--task','A','--agent','repair-agent')
    (repo/'svc/app.py').write_text('x = 3\n')
    invoke(cli,'writer','finish','--id','repair','--outcome','success')
    invoke(cli,'evidence','run','--area','unit')
    invoke(cli,'evidence','run','--area','ci-repro')
    invoke(cli,'integration','task-finish','--task','A')
    assert report(cli,tmp_path,'agent-update',{'id':'repair-agent','status':'Completed'})[0]==0
    invoke(cli,'gates','run','--stage','candidate')
    invoke(cli,'phase','VERIFICATION')
    assert approve_route(cli,tmp_path)[0]==0
    for event in al_store.load(sid)['integration']['outbox']:
        ingest_event(controller.conn,eid,event)
    assert get_execution(controller.conn,eid)['status']=='Completed'
    # A second failed pipeline cannot restart an unbounded repair loop.
    result=route_failure(controller,eid,failed|{'id':43},jobs)
    assert result['status']=='Blocked' and 'retry limit' in str(result['blockers'])
    assert resumes==[eid]
    controller.conn.close()
