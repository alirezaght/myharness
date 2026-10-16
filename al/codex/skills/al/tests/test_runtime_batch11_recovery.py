"""Amendments supersede immutable recovery checkpoints without authorizing progress."""
import json
from pathlib import Path
import pytest
import al_artifacts as A
import al_task as T
from hook_regression_helpers import invoke
from batch5_helpers import state
from batch6_helpers import install_replacement
from test_runtime_batch6_recovery import changed_recovery


def amend(cli,tmp,identifier='AM-1',ok=True):
    file=tmp/(identifier+'.json');file.write_text(json.dumps({'id':identifier,'source':'user','text':'User adds a substantive authorized requirement for this task.'}))
    return invoke(cli,'task','amend','--file',str(file),ok=ok)


def test_repeated_amendments_rebind_checkpoints_preserve_provenance_and_require_ready_base(cli,tmp_path,repo,state_file):
    changed_recovery(cli,tmp_path,repo,'IMPLEMENTATION')
    original=state(cli);old_marker=original['gate_recovery'];refs={r['id']:A.read(r) for r in original['checkpoints']}
    for number in (1,2):
        prior=state(cli);amend(cli,tmp_path,'AM-'+str(number));st=state(cli)
        marker=st['gate_recovery'];ref=next(r for r in st['checkpoints'] if r['id']==marker['checkpoint']);data=A.read_json(ref)
        assert st['phase']=='PLANNING' and marker['phase']=='PLANNING' and marker['replacement_manifest'] is None
        assert data['task_hash']==T.task_hash(st) and data['prior_phase']=='PLANNING'
        assert data['identity']==st['identity'] and data['run_id']==st['run_id']
        assert marker['reasons']==old_marker['reasons'] and marker['superseded_manifest']==old_marker['superseded_manifest']
        history=st['gate_recovery_supersessions'];assert len(history)==number
        event=history[-1];assert event['status']=='SUPERSEDED_BY_AMENDMENT'
        assert event['old_checkpoint']['id']==prior['gate_recovery']['checkpoint'] and event['new_checkpoint']==ref
        assert event['marker']==prior['gate_recovery'] and event['amendment']['id']=='AM-'+str(number)
        assert st['completed_phases']==original['completed_phases'] and st['review'] is None
        assert set(refs)<={r['id'] for r in st['checkpoints']}
        for previous in st['checkpoints']:
            if previous['id'] in refs:assert A.read(previous)==refs[previous['id']]
        invoke(cli,'spec-gate','SPEC_READY');before=state_file().read_bytes()
        invoke(cli,'phase','IMPLEMENTATION',ok=False);assert state_file().read_bytes()==before
        if number==1:
            import sys
            install_replacement(cli,tmp_path,repo,[sys.executable,'-c',"print('base pending')"])
            assert state(cli)['gate_recovery']['replacement_manifest']
    import sys
    install_replacement(cli,tmp_path,repo,[sys.executable,'-c',"print('base verified')"])
    before=state_file().read_bytes();invoke(cli,'phase','IMPLEMENTATION',ok=False);assert state_file().read_bytes()==before
    invoke(cli,'gates','run','--stage','base','--id','loc');invoke(cli,'phase','IMPLEMENTATION')
    st=state(cli);assert 'gate_recovery' not in st and len(st['gate_recovery_supersessions'])==2
    assert st['gate_recovery_history'][-1]['evidence']


@pytest.mark.parametrize('defect',['phase','checkpoint','artifact','identity','task','manifest','writer','check','empty'])
def test_invalid_pre_amendment_recovery_refuses_without_state_mutation(cli,tmp_path,repo,state_file,defect):
    changed_recovery(cli,tmp_path,repo,'IMPLEMENTATION')
    if defect=='check':
        import sys
        invoke(cli,'evidence','plan','--area','pending','--tier','focused','--kind','check','--scope','svc/app.py','--',sys.executable,'-c',"print('check completed')")
        invoke(cli,'evidence','run','--area','pending')
    st=state(cli)
    marker=st['gate_recovery'];ref=next(r for r in st['checkpoints'] if r['id']==marker['checkpoint'])
    if defect=='phase':marker['phase']='PSEUDOCODE'
    elif defect=='checkpoint':marker['checkpoint']='absent'
    elif defect=='artifact':Path(ref['stored_path']).write_text('tampered')
    elif defect in {'identity','task'}:
        data=A.read_json(ref)
        if defect=='identity':data['identity']=dict(data['identity'],head='wrong')
        else:data['task_hash']='0'*64
        replacement=A.snapshot_bytes(st['session_id'],json.dumps(data).encode(),'checkpoint','invalid test checkpoint')
        st['checkpoints'].append(replacement);marker['checkpoint']=replacement['id']
    elif defect=='manifest':marker['superseded_manifest']='0'*64
    elif defect=='writer':st['writers']['pending']={'id':'pending','state':'UNRESOLVED'}
    elif defect=='check':
        st['evidence']['entries'][-1].update(status='RUNNING')
    else:st['gate_recovery']={}
    state_file().write_text(json.dumps(st));before=state_file().read_bytes()
    amend(cli,tmp_path,ok=False);assert state_file().read_bytes()==before
