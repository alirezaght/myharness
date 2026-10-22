"""Terminal admission and closed timing cannot be reopened by stopping commands."""
import json
import time
import pytest
import al_store
from hook_regression_helpers import begin,approve_route,invoke,manifest_for
from launcher_context import admit_planning


def state(cli):return json.loads(cli('show')[1])


def requests(tmp):
    p=tmp/'stop.json';p.write_text(json.dumps({'reason':'Await explicit input','resume_instructions':'Revalidate before resuming','recipient':'al-builder','artifact':str(tmp/'missing-transfer')}))
    return [('interrupt','--file',str(p)),('waiting','--file',str(p)),('transfer','--file',str(p)),
            ('checkpoint','--reason','Record current progress','--resume-instructions','Continue safely'),
            ('stopped','--reason','Cannot proceed'),('agent-failed','al-engineer','--reason','Runner failed','--retry-safe','yes')]


def artifacts(st):
    root=al_store.RUNTIME_DIR/st['session_id']/'artifacts'
    return {str(p.relative_to(root)):(p.read_bytes(),p.stat().st_mode) for p in root.rglob('*') if p.is_file()}


def test_approved_done_refuses_every_stopping_command_before_effects(cli,repo,tmp_path,state_file):
    begin(cli,tmp_path,repo);assert approve_route(cli,tmp_path)[0]==0
    st=state(cli);assert st['status']=='APPROVED' and st['phase']=='DONE'
    before=state_file().read_bytes();members=artifacts(st)
    for command in requests(tmp_path):
        invoke(cli,*command,ok=False)
        assert state_file().read_bytes()==before and artifacts(st)==members


@pytest.mark.parametrize('stop',['waiting','interrupt','transfer'])
def test_running_stop_resumes_once_and_repeated_stop_preserves_checkpoint(cli,tmp_path,state_file,stop):
    invoke(cli,'start','--spec','Retain timing and checkpoint identity across an explicit stop.')
    artifact=tmp_path/'transfer';artifact.write_text('Owned handoff')
    p=tmp_path/'request.json';p.write_text(json.dumps({'reason':'Await input','resume_instructions':'Resume after checking','recipient':'al-builder','artifact':str(artifact)}))
    invoke(cli,stop,'--file',str(p));st=state(cli);closed=st['phase_occurrences'][-1].copy()
    before=state_file().read_bytes();members=artifacts(st)
    for command in requests(tmp_path):
        invoke(cli,*command,ok=False);assert state_file().read_bytes()==before and artifacts(st)==members
    time.sleep(.01);invoke(cli,'resume','--checkpoint',st['stopping_checkpoint']);after=state(cli)
    assert after['phase_occurrences'][0]==closed and len(after['phase_occurrences'])==2
    assert closed['completed'] is False and after['completed_phases']==[]
    active=after['phase_occurrences'][-1]
    assert active['phase']=='PLANNING' and active['ended'] is None and active['started']>closed['ended']
    assert after['phase_timings']['PLANNING']=={'started':active['started'],'ended':None}


def test_failed_agent_retry_keeps_closed_occurrence_without_completion(cli):
    invoke(cli,'start','--spec','Retry a transient agent failure without changing closed timing.')
    invoke(cli,'agent-failed','al-engineer','--reason','Transient failure','--retry-safe','yes');st=state(cli);closed=st['phase_occurrences'][-1].copy()
    time.sleep(.01);invoke(cli,'retry','al-engineer','--reason','Runner confirmed stopped');after=state(cli)
    assert after['phase_occurrences'][0]==closed and len(after['phase_occurrences'])==2
    assert closed['completed'] is False and after['completed_phases']==[] and after['retries']['al-engineer']==1


def test_failed_agent_can_terminalize_without_reconciliation_or_timing_rewrite(cli,repo,tmp_path,state_file):
    invoke(cli,'start','--spec','Preserve an unfinished writer when terminalizing failed work.')
    invoke(cli,'spec-gate','SPEC_READY');invoke(cli,'classify','SMALL','--designer','no','--implementers','al-engineer')
    p=tmp_path/'gates.json';p.write_text(json.dumps(manifest_for(state(cli),repo,anticipated_paths=['svc/app.py'])))
    invoke(cli,'gates','plan','--file',str(p));invoke(cli,'gates','run','--stage','base');admit_planning(cli);invoke(cli,'phase','IMPLEMENTATION')
    invoke(cli,'writer','start','--id','pending','--role','al-engineer','--files','svc/app.py')
    invoke(cli,'agent-failed','al-engineer','--reason','Cannot complete writer','--retry-safe','yes');st=state(cli)
    invoke(cli,'stopped','--reason','Stop this run');after=state(cli)
    assert after['status']=='STOPPED_WITH_BLOCKERS'
    for name in ['failure','checkpoints','writers','phase_occurrences','phase_timings','completed_phases']:
        assert after[name]==st[name]
    before=state_file().read_bytes();invoke(cli,'stopped','--reason','Again',ok=False)
    invoke(cli,'retry','al-engineer','--reason','Try again',ok=False)
    assert state_file().read_bytes()==before


def test_recovery_to_different_phase_does_not_complete_closed_verification(cli,repo,tmp_path):
    begin(cli,tmp_path,repo);p=tmp_path/'wait.json';p.write_text(json.dumps({'reason':'Await policy','resume_instructions':'Replan changed rules'}))
    invoke(cli,'waiting','--file',str(p));st=state(cli);closed=st['phase_occurrences'][-1].copy()
    (repo/'Makefile').write_text('all:\n\ttrue\n');invoke(cli,'resume','--checkpoint',st['stopping_checkpoint']);after=state(cli)
    assert after['phase']=='PLANNING' and after['phase_occurrences'][-2]==closed
    assert 'VERIFICATION' not in after['completed_phases'] and closed['completed'] is False


def test_set_phase_cannot_complete_an_already_closed_occurrence(cli,repo,tmp_path):
    invoke(cli,'start','--spec','Only open occurrences may complete when the phase advances.')
    invoke(cli,'spec-gate','SPEC_READY');invoke(cli,'classify','SMALL','--designer','no','--implementers','al-engineer')
    p=tmp_path/'gates.json';p.write_text(json.dumps(manifest_for(state(cli),repo,anticipated_paths=['svc/app.py'])))
    invoke(cli,'gates','plan','--file',str(p));invoke(cli,'gates','run','--stage','base');admit_planning(cli);invoke(cli,'phase','IMPLEMENTATION')
    p=tmp_path/'stop.json';p.write_text(json.dumps({'reason':'Pause unfinished work','resume_instructions':'Resume safely'}))
    invoke(cli,'interrupt','--file',str(p));st=state(cli);closed=st['phase_occurrences'][-1].copy()
    al_store.set_phase(st,'VERIFICATION')
    assert st['phase_occurrences'][-2]==closed and 'IMPLEMENTATION' not in st['completed_phases']


def test_corrupt_running_done_cannot_create_agent_failure_artifacts(cli,repo,tmp_path,state_file):
    begin(cli,tmp_path,repo);assert approve_route(cli,tmp_path)[0]==0
    st=state(cli);st['status']='RUNNING';state_file().write_text(json.dumps(st))
    before=state_file().read_bytes();members=artifacts(st)
    invoke(cli,'agent-failed','al-engineer','--reason','Must not reopen DONE','--retry-safe','yes',ok=False)
    assert state_file().read_bytes()==before and artifacts(st)==members
