"""Historical CI declarations require actual immutable task amendment prefixes."""
import json
from pathlib import Path
import pytest
import al_ci_context as CC
import al_gate_currency as C
import al_task as T
from hook_regression_helpers import invoke
from batch14_helpers import prepare_context,context,state,plan,resolved,amendment
from launcher_context import admit_planning


def test_amend_interrupt_resume_requires_new_context_and_base_evidence(cli,repo,tmp_path,state_file):
    ctx,old=prepare_context(cli,repo,tmp_path);old_hash=T.task_hash(state(cli))
    amendment(cli,tmp_path)
    pause=tmp_path/'pause.json';pause.write_text(json.dumps({'reason':'Owned amended task checkpoint','resume_instructions':'Replace context and gates'}))
    checkpoint=invoke(cli,'interrupt','--file',str(pause))[1].strip()
    invoke(cli,'resume','--checkpoint',checkpoint)
    st=state(cli);assert st['gate_recovery']['phase']=='PLANNING' and old_hash!=T.task_hash(st)
    assert CC.binding(st,ctx,allow_historical=True)['current'] is False
    assert C.check(st)['current'] is False
    before=state_file().read_bytes()
    file=tmp_path/'old.json';file.write_text(json.dumps(old))
    invoke(cli,'gates','plan','--file',str(file),ok=False);assert state_file().read_bytes()==before
    invoke(cli,'spec-gate','SPEC_READY');replacement=resolved(state(cli),['svc/app.py'],context(state(cli)))
    admit_planning(cli)
    plan(cli,tmp_path,replacement)
    invoke(cli,'gates','run','--stage','base');admit_planning(cli);invoke(cli,'phase','IMPLEMENTATION')
    st=state(cli);assert not st.get('gate_recovery') and st['gate_recovery_history'][-1]['evidence']


@pytest.mark.parametrize('defect',['arbitrary-task','foreign-run','foreign-session','tamper','amendment-tamper'])
def test_corrupt_or_foreign_context_is_never_historical_currency(cli,repo,tmp_path,state_file,defect):
    ctx,m=prepare_context(cli,repo,tmp_path);amendment(cli,tmp_path);st=state(cli)
    if defect=='tamper':Path(ctx['source']['stored_path']).write_text('{}')
    elif defect=='amendment-tamper':Path(st['amendments'][0]['artifact']['stored_path']).write_text('{}')
    else:
        changes={'task_hash':'f'*64} if defect=='arbitrary-task' else {'run_id':'foreign'} if defect=='foreign-run' else {'session_id':'foreign'}
        m['ci_context']=context(st,**changes)
        import al_artifacts as A
        st['gate_manifests'][-1]=A.snapshot_bytes(st['session_id'],json.dumps(m).encode(),'gate-manifest','owned invalid context')
    before=state_file().read_bytes()
    with pytest.raises(SystemExit):C.check(st)
    assert state_file().read_bytes()==before


def test_each_real_amendment_prefix_is_intact_historical_provenance(cli,repo,tmp_path):
    original,_=prepare_context(cli,repo,tmp_path);amendment(cli,tmp_path);first=context(state(cli))
    amendment(cli,tmp_path,'A2');st=state(cli)
    assert CC.binding(st,original,allow_historical=True)['current'] is False
    assert CC.binding(st,first,allow_historical=True)['current'] is False
    assert CC.binding(st,context(st),allow_historical=True)['current'] is True


def test_fabricated_nonprefix_amendment_hash_is_not_historical(cli,repo,tmp_path):
    prepare_context(cli,repo,tmp_path);amendment(cli,tmp_path);amendment(cli,tmp_path,'A2');st=state(cli)
    import hashlib
    fake=hashlib.sha256(json.dumps({'original':st['task_package']['sha256'],'amendments':[st['amendments'][1]['artifact']['sha256']]},sort_keys=True).encode()).hexdigest()
    declaration=context(st,task_hash=fake)
    with pytest.raises(SystemExit):CC.binding(st,declaration,allow_historical=True)
