"""Bounded recovery credits, immutable history and exact atomic replay."""
import copy
import json
from pathlib import Path
import pytest
import al_proof_json as J
import al_rework_recovery_contract as C
from test_rework import started,fast,load


def exhaust(cli):
    for _ in range(2):
        assert cli('rework','--source','finalization',*fast(cli))[0]==0
        assert cli('phase','FINALIZATION')[0]==0
    assert cli('rework','--source','finalization',*fast(cli))[0]==10


def decision(cli,tmp_path,*,request_id='decision-1',decision='allow',basis='revised-diagnosis'):
    code,out,err=cli('rework-recovery','packet')
    assert code==0,(code,out,err)
    packet=json.loads(out);v=packet['content'];attempt=v['attempt']
    red=next(e for e in reversed(v['evidence']['entries']) if e['status']=='FAIL')
    d={'schema_version':1,'kind':'rework-recovery-decision-v1','request_id':request_id,
       'occurrence_id':v['occurrence_id'],'binding':v['binding'],'packet_hash':packet['packet']['sha256'],
       **{k:attempt[k] for k in ('attempt_id','model','effort','policy_digest')},'invocation_id':'architect-'+request_id,
       'decision':decision,'basis':basis,'rationale':'Inspect the recorded red command and isolate the remaining defect.',
       'next_action':'Implement source-backed repair '+request_id,'remaining_criteria':v['task_package']['criteria'],
       'inspected_artifacts':[packet['packet']['sha256'],J.digest(attempt)],
       'evidence':[{'id':red['id'],'entry_hash':J.digest(red),'claim':'historical-red'}]}
    path=tmp_path/(request_id+'.json');path.write_text(json.dumps(d));return path,d


def test_six_returns_two_automatic_grants_then_precise_cap(cli,state_file,tmp_path):
    started(cli,tmp_path);exhaust(cli)
    first=load(state_file);assert first['rework']['total']==2
    for i in (1,2):
        path,_=decision(cli,tmp_path,request_id='decision-'+str(i))
        assert cli('rework-recovery','decide','--file',str(path))[0]==0
        current=load(state_file)
        assert current['rework']['total']==2*i+1
        assert current['rework']['history'][:2]==first['rework']['history']
        assert cli('phase','FINALIZATION')[0]==0
        assert cli('rework','--source','finalization',*fast(cli))[0]==0
        assert cli('phase','FINALIZATION')[0]==0
        assert cli('rework','--source','finalization',*fast(cli))[0]==10
    st=load(state_file)
    assert st['status']=='STOPPED_WITH_BLOCKERS' and st['rework']['total']==6
    assert st['rework_recovery']['automatic_authorizations']==2
    assert st['rework_recovery']['stop']['kind']=='cap'
    assert cli('resume','--checkpoint',st['stopping_checkpoint'])[0]!=0


def test_decision_replay_is_nonmutating_and_changed_request_refuses(cli,state_file,tmp_path):
    started(cli,tmp_path);exhaust(cli);path,data=decision(cli,tmp_path)
    code,out,_=cli('rework-recovery','decide','--file',str(path));assert code==0
    before=state_file().read_bytes()
    assert cli('rework-recovery','decide','--file',str(path))[:2]==(0,out)
    assert state_file().read_bytes()==before
    data['next_action']='changed';path.write_text(json.dumps(data))
    assert 'altered content' in cli('rework-recovery','decide','--file',str(path))[0]
    assert state_file().read_bytes()==before


@pytest.mark.parametrize('mutation',['no-progress','foreign','stale','attempt','empty','active'])
def test_invalid_authorization_never_consumes_or_mutates(cli,state_file,tmp_path,mutation):
    started(cli,tmp_path);exhaust(cli);path,data=decision(cli,tmp_path)
    if mutation=='no-progress':data['basis']='try-again'
    elif mutation=='foreign':data['evidence'][0]['entry_hash']='f'*64
    elif mutation=='stale':data['binding']['candidate_hash']='f'*64
    elif mutation=='attempt':data['attempt_id']='foreign'
    elif mutation=='empty':data['next_action']=''
    elif mutation=='active':
        # Ordinary writing continues, but an active writer cannot consume bounded credits.
        assert cli('writer','start','--id','ordinary-repair','--role','al-engineer','--files','svc/app.py')[0]==0
        before=state_file().read_bytes()
        assert cli('rework-recovery','decide','--file',str(path))[0]!=0
        assert state_file().read_bytes()==before
        return
    path.write_text(json.dumps(data));before=state_file().read_bytes()
    assert cli('rework-recovery','decide','--file',str(path))[0]!=0
    assert state_file().read_bytes()==before


def test_decline_and_explicit_two_credit_continuation_are_idempotent(cli,state_file,tmp_path):
    started(cli,tmp_path);exhaust(cli)
    path,_=decision(cli,tmp_path,decision='decline',basis='no-useful-next-step')
    assert cli('rework-recovery','decide','--file',str(path))[0]==0
    st=load(state_file);assert st['status']=='STOPPED_WITH_BLOCKERS'
    bound=C.binding(st)
    auth={'schema_version':1,'kind':'explicit-user-continuation-v1','request_id':'user-1','binding':bound,
          'message':'Continue with one bounded two-return batch.','provenance':'Explicit user message, test conversation turn 1.'}
    authfile=tmp_path/'user-message.json';authfile.write_text(json.dumps(auth))
    code,out,_=cli('artifact','record','--file',str(authfile),'--kind','user-authorization')
    assert code==0,(code,out)
    ref=json.loads(out)
    # artifact record may wrap its immutable reference in an envelope.
    if 'artifact' in ref:ref=ref['artifact']
    request={'schema_version':1,'kind':'rework-recovery-continuation-v1','request_id':'user-1',
             'checkpoint_id':st['stopping_checkpoint'],'binding':bound,'user_authorization':ref,
             'next_action':'Repair the diagnosed in-scope defect.','grant_size':2}
    path=tmp_path/'continue.json';path.write_text(json.dumps(request))
    code,out,err=cli('rework-recovery','continue','--file',str(path));assert code==0,(code,out,err)
    current=load(state_file);assert current['rework']['total']==2 and current['rework']['used']==0
    assert current['rework_recovery']['automatic_authorizations']==0
    before=state_file().read_bytes()
    assert cli('rework-recovery','continue','--file',str(path))[:2]==(0,out)
    assert state_file().read_bytes()==before


@pytest.mark.parametrize('mutation',['rollback','grant','boolean','remove','history'])
def test_append_only_state_refuses_authority_rewrites(cli,state_file,tmp_path,mutation):
    started(cli,tmp_path)
    assert cli('rework','--source','finalization',*fast(cli))[0]==0
    before=load(state_file);after=copy.deepcopy(before)
    if mutation=='rollback':after['rework']['total']=0
    elif mutation=='grant':after['rework_recovery']['grants'][0]['limit']=3
    elif mutation=='boolean':after['rework_recovery']['automatic_authorizations']=False
    elif mutation=='remove':after.pop('rework_recovery')
    else:after['rework']['history'][0]['label']='rewritten'
    with pytest.raises(SystemExit):C.prepare_save(after,before)


@pytest.mark.parametrize('point',['before-replace','after-replace'])
def test_interrupted_decision_commit_has_no_partial_credit_and_replays(cli,state_file,tmp_path,monkeypatch,point):
    import al_rework_recovery as Recovery
    started(cli,tmp_path);exhaust(cli);path,_=decision(cli,tmp_path)
    before=state_file().read_bytes();real=Recovery.Store.save
    def interrupted(sid,st):
        if point=='after-replace':real(sid,st)
        raise SystemExit('simulated lost response')
    with monkeypatch.context() as patch:
        patch.setattr(Recovery.Store,'save',interrupted)
        assert cli('rework-recovery','decide','--file',str(path))[0]=='simulated lost response'
    if point=='before-replace':assert state_file().read_bytes()==before
    else:
        st=load(state_file)
        assert st['rework']['total']==3 and len(st['rework_recovery']['decisions'])==1
    assert cli('rework-recovery','decide','--file',str(path))[0]==0
    st=load(state_file)
    assert st['rework']['total']==3 and st['rework_recovery']['automatic_authorizations']==1
    assert len(st['rework_recovery']['decisions'])==1


@pytest.mark.parametrize('operation',['packet','decide'])
def test_unresolved_operations_refuse_recovery_without_mutation(cli,state_file,tmp_path,monkeypatch,operation):
    import al_rework_recovery as Recovery
    started(cli,tmp_path);exhaust(cli);path,_=decision(cli,tmp_path)
    before=state_file().read_bytes()
    monkeypatch.setattr(Recovery,'operation_gaps',lambda st:['unresolved recorded check E1'])
    args=['rework-recovery',operation]
    if operation=='decide':args+=['--file',str(path)]
    assert 'reconciled operations' in cli(*args)[0]
    assert state_file().read_bytes()==before


@pytest.mark.parametrize('mutation',['missing','changed','extra'])
def test_factual_review_packet_rejects_missing_changed_or_extra_lineage(cli,state_file,tmp_path,monkeypatch,mutation):
    import al_review_behavior as Behavior
    import al_rework_recovery_contract as Contract
    from evidence_helpers import plan_and_run_broad,complete_review
    started(cli,tmp_path)
    # Complete a real review via the existing public evidence seams, then exercise its producer contract.
    from test_rework import REPRO
    Path('svc/fixed').write_text('fixed')
    assert cli('evidence','run','--area','repro','--tier','focused','--scope','svc','--',*REPRO)[0]==0
    assert cli('phase','VERIFICATION')[0]==0
    assert plan_and_run_broad(cli)==0
    complete_review(cli,tmp_path)
    st=load(state_file);packet=J.read(st['review']['packets']['fresh'])
    assert packet['rework_recovery_lineage']==Contract.lineage(st)
    # Reconstruct exactly the producer's pre-Behavior.packet input, without snapshotting a forged packet.
    data={k:copy.deepcopy(packet[k]) for k in ('attempt','schema_version','review_id','stage','candidate_hash','task_hash','task_package','amendments','candidate','evidence','gates','rework_recovery_lineage')}
    data['amendments']=st['amendments'];data['escapes']=[]
    if mutation=='missing':data.pop('rework_recovery_lineage')
    elif mutation=='changed':data['rework_recovery_lineage']['automatic_authorizations']=99
    else:data['implementer_summary']='unrelated input must remain forbidden'
    before=state_file().read_bytes()
    with pytest.raises(SystemExit):Behavior.packet(st,data)
    assert state_file().read_bytes()==before


def test_unavailable_attempt_requires_explicit_recorded_fallback_replacement(cli,state_file,tmp_path):
    started(cli,tmp_path);exhaust(cli);path,old=decision(cli,tmp_path)
    assert cli('model-unavailable',old['model'],'--reason','model is not supported by this provider')[0]==0
    before=state_file().read_bytes()
    assert 'unavailable' in cli('rework-recovery','decide','--file',str(path))[0]
    assert state_file().read_bytes()==before
    code,out,err=cli('rework-recovery','packet','--replace-unavailable-attempt')
    assert code==0,(code,out,err)
    packet=json.loads(out);new=packet['content']['attempt']
    assert new['attempt_id']!=old['attempt_id'] and new['fallback_event_reference']
    assert len(load(state_file)['rework_recovery']['occurrences'][0]['packets'])==2
    assert cli('rework-recovery','decide','--file',str(path))[0]!=0
    path,current=decision(cli,tmp_path,request_id='fallback')
    assert current['model']==new['model']
    assert cli('rework-recovery','decide','--file',str(path))[0]==0


@pytest.mark.parametrize('target',['receipt','report','packet','checkpoint'])
def test_corrupt_retained_bytes_refuse_without_grant(cli,state_file,tmp_path,target):
    started(cli,tmp_path);exhaust(cli);path,_=decision(cli,tmp_path)
    if target in {'receipt','report'}:assert cli('rework-recovery','decide','--file',str(path))[0]==0
    st=load(state_file);r=st['rework_recovery'];o=r['occurrences'][0]
    ref=r['decisions'][0][target] if target in {'receipt','report'} else o['checkpoint'] if target=='checkpoint' else o['packets'][0]['packet']
    Path(ref['stored_path']).write_bytes(b'corrupt')
    before=state_file().read_bytes()
    assert cli('show')[0]!=0
    assert state_file().read_bytes()==before


def test_current_success_requires_current_prerequisite_currency(cli,state_file,tmp_path,repo):
    from test_rework import REPRO
    started(cli,tmp_path)
    assert cli('evidence','plan','--area','support','--tier','focused','--scope','web','--',__import__('sys').executable,'-c',"print('1 passed')")[0]==0
    assert cli('evidence','run','--area','support')[0]==0
    assert cli('evidence','run','--area','green','--tier','focused','--scope','svc','--needs','support','--',__import__('sys').executable,'-c',"print('1 passed')")[0]==0
    exhaust(cli);path,data=decision(cli,tmp_path)
    st=load(state_file);green=next(e for e in st['evidence']['entries'] if e['area']=='green')
    data['evidence']=[{'id':green['id'],'entry_hash':J.digest(green),'claim':'current-success'}]
    (repo/'web/app.js').write_text('changed prerequisite')
    # Candidate drift also refuses; bind the unit seam to prove the stricter prerequisite check independently.
    import al_rework_recovery as Recovery
    with pytest.raises(SystemExit,match='VALID PASS'):Recovery.evidence(st,data['evidence'])


@pytest.mark.parametrize('mutation',['extra','version','counter','pending','disposition'])
def test_nested_state_contract_fails_closed(cli,state_file,tmp_path,mutation):
    started(cli,tmp_path);exhaust(cli);st=load(state_file)
    if mutation=='extra':st['rework_recovery']['grants'][0]['extra']=True
    elif mutation=='version':st['rework_recovery']['schema_version']=True
    elif mutation=='counter':st['rework']['history'][0]['n']=True
    elif mutation=='pending':st['rework_recovery']['pending']='foreign'
    else:st['rework_recovery']['occurrences'][0]['dispositions']=[{'kind':'cap'}]
    with pytest.raises(SystemExit):C.validate(st)



def real_correction(cli,state_file,tmp_path,repo):
    """Exercise actual evidence, independent review and the separate correction cap."""
    from evidence_helpers import plan_and_run_broad,complete_review
    from test_rework import REPRO
    (repo/'svc/fixed').write_text('fixed')
    assert cli('evidence','run','--area','repro','--tier','focused','--scope','svc','--',*REPRO)[0]==0
    assert cli('phase','VERIFICATION')[0]==0
    assert plan_and_run_broad(cli)==0
    def inspected_call(*args):
        if args[:1]==('review-start',):
            # complete_review changes the candidate after a prior rejection; rerun the actual repro on that source.
            assert cli('evidence','run','--area','repro','--tier','focused','--scope','svc','--',*REPRO)[0]==0
        if args and args[0] in {'review-fresh','review-result'} and '--file' in args:
            path=Path(args[args.index('--file')+1]);report=json.loads(path.read_bytes())
            hashes=C.required_hashes(load(state_file))
            # The actual reports explicitly record inspection of every retained decision and receipt.
            for ref in load(state_file)['rework_recovery']['decisions']:
                J.read(ref['report']);J.read(ref['receipt'])
            report['inspected_artifacts']=sorted(set(report['inspected_artifacts'])|hashes)
            path.write_text(json.dumps(report))
        return cli(*args)
    inspected_call.state=cli.state
    complete_review(inspected_call,tmp_path)
    before=load(state_file)['rework']
    assert cli('correction','--route','al-engineer')[0]==0
    assert load(state_file)['rework']==before
    assert cli('phase','FINALIZATION')[0]==0
    (repo/'svc/fixed').unlink()
    assert cli('evidence','run','--area','repro','--tier','focused','--scope','svc','--',*REPRO)[0]==1


def test_genuine_corrections_between_recovered_batches_never_create_credit(cli,state_file,tmp_path,repo):
    started(cli,tmp_path)
    for batch in range(3):
        if batch:
            path,_=decision(cli,tmp_path,request_id='recovered-'+str(batch))
            assert cli('rework-recovery','decide','--file',str(path))[0]==0
            assert cli('phase','FINALIZATION')[0]==0
        else:
            assert cli('rework','--source','finalization',*fast(cli))[0]==0
            assert cli('phase','FINALIZATION')[0]==0
        assert cli('rework','--source','finalization',*fast(cli))[0]==0
        assert cli('phase','FINALIZATION')[0]==0
        consumed=load(state_file)['rework']['total']
        real_correction(cli,state_file,tmp_path,repo)
        assert load(state_file)['rework']['total']==consumed==2*(batch+1)
        assert cli('rework','--source','finalization',*fast(cli))[0]==10
    st=load(state_file)
    assert st['correction_iteration']==3 and st['rework']['total']==6
    assert st['rework_recovery']['automatic_authorizations']==2
    assert st['status']=='STOPPED_WITH_BLOCKERS'


def test_an_unchanged_approach_requires_new_supporting_evidence(cli,state_file,tmp_path):
    started(cli,tmp_path);exhaust(cli);path,first=decision(cli,tmp_path,request_id='first')
    assert cli('rework-recovery','decide','--file',str(path))[0]==0
    assert cli('phase','FINALIZATION')[0]==0
    assert cli('rework','--source','finalization',*fast(cli))[0]==0
    assert cli('phase','FINALIZATION')[0]==0
    assert cli('rework','--source','finalization',*fast(cli))[0]==10
    path,second=decision(cli,tmp_path,request_id='second')
    second['next_action']=first['next_action'];path.write_text(json.dumps(second));before=state_file().read_bytes()
    assert 'without new evidence' in cli('rework-recovery','decide','--file',str(path))[0]
    assert state_file().read_bytes()==before



def test_no_approved_model_stops_with_precise_checkpoint_and_no_budget_authority(cli,state_file,tmp_path):
    import al_models as Models
    started(cli,tmp_path);exhaust(cli);decision(cli,tmp_path)
    primary=Models.POLICY['al-architect']['primary']['model']
    fallback=Models.POLICY['al-architect']['fallback']['model']
    assert cli('model-unavailable',primary,'--reason','provider does not support this model')[0]==0
    assert cli('rework-recovery','packet','--replace-unavailable-attempt')[0]==0
    assert cli('model-unavailable',fallback,'--reason','provider does not support this model')[0]==4
    st=load(state_file);r=st['rework_recovery']
    assert st['status']=='STOPPED_WITH_BLOCKERS' and r['pending'] is None
    assert r['stop']['kind']=='model' and r['stop']['checkpoint_id']==st['stopping_checkpoint']
    assert r['automatic_authorizations']==0 and st['rework']['total']==2
    assert J.read(st['checkpoints'][-1])['reason']==st['stop_reason']
    from al_recovery_subjects import issues
    guidance=issues(st)
    assert guidance[0]['code']=='REWORK_MODEL_BLOCKED'
    assert guidance[0]['recovery']['required_input']['kind']=='approved-model-availability'



def test_pending_request_replay_is_exact_and_candidate_drift_refuses(cli,state_file,tmp_path,repo):
    started(cli,tmp_path);exhaust(cli);before=state_file().read_bytes()
    assert cli('rework','--source','finalization',*fast(cli))[0]==10
    assert state_file().read_bytes()==before
    (repo/'svc/app.py').write_text('changed source')
    assert 'stale recovery' in cli('rework','--source','finalization',*fast(cli))[0]
    assert state_file().read_bytes()==before


def test_boolean_attempt_schema_cannot_authorize(cli,state_file,tmp_path):
    import al_rework_recovery_attempts as Attempts
    started(cli,tmp_path);exhaust(cli);decision(cli,tmp_path)
    st=load(state_file);o=st['rework_recovery']['occurrences'][0]
    value=J.read(o['packets'][0]['attempt']);value['schema_version']=True
    ref=J.snapshot(st['session_id'],value,'malformed-recovery-attempt')
    with pytest.raises(SystemExit):Attempts.validate(st,ref,o['binding'])



def test_save_cannot_forge_a_repro_resolution_without_recorded_red(cli,state_file,tmp_path):
    from test_rework import flags
    started(cli,tmp_path)
    assert cli('rework','--source','finalization',*flags(cli,cause_demonstrated='no',repro=''))[0]==12
    before=load(state_file);after=copy.deepcopy(before)
    ref=J.snapshot(before['session_id'],{'explanation':'supporting resolution bytes'},'rework-resolution')
    after['rework']['history'][0].update(repro='E99999',resolution={'reason':'diagnosed','artifact':ref})
    with pytest.raises(SystemExit,match='recorded red evidence'):C.prepare_save(after,before)



@pytest.mark.parametrize('command',[('show',),('rework','--source','finalization')])
def test_current_managed_load_refuses_deleted_capability_without_mutation(cli,state_file,tmp_path,command):
    from test_rework import without_repro
    started(cli,tmp_path);st=load(state_file);st.pop('rework_recovery')
    raw=json.dumps(st).encode();state_file().write_bytes(raw)
    arguments=list(command)
    if command[0]=='rework':arguments+=without_repro()
    result=cli(*arguments)[0]
    assert result!=0 and 'recovery' in str(result)
    assert state_file().read_bytes()==raw


@pytest.mark.parametrize('field',['session_id','run_id','runtime_origin','identity'])
def test_current_recovery_boundary_refuses_foreign_protected_identity(cli,state_file,tmp_path,monkeypatch,field):
    from types import SimpleNamespace
    import al_new_origin as Origin
    started(cli,tmp_path);st=load(state_file)
    protected={key:st['runtime_origin'][key] for key in ('kind','schema_version','run_id','publication_transaction','pin','initialization')}
    if protected['schema_version']==2:
        protected.update({key:st['runtime_origin'][key] for key in ('runtime_layout','runtime_directory')})
    protected.update(session_id=st['session_id'],repository_identity={key:st['identity'][key] for key in ('root','git_dir','common_dir')})
    foreign=copy.deepcopy(st)
    if field in {'session_id','run_id'}:foreign[field]='foreign'
    elif field=='runtime_origin':foreign[field]['publication_transaction']='f'*64
    else:foreign[field]['root']='/foreign'
    monkeypatch.setattr(Origin,'context',lambda:SimpleNamespace(transaction=protected))
    before=state_file().read_bytes()
    with pytest.raises(SystemExit,match='protected issuer'):C.require_current(foreign)
    assert state_file().read_bytes()==before


def test_schema_only_historical_inspection_does_not_require_current_capability(cli,state_file,tmp_path):
    from al_schema import validate_state
    started(cli,tmp_path);st=load(state_file);st.pop('rework_recovery')
    # This is the generic historical validation seam, not a claim that a deleted current run is old.
    validate_state(st)
    assert 'rework_recovery' not in st


def test_scoped_readonly_legacy_inspection_never_grants_recovery(monkeypatch,tmp_path):
    import sys
    from types import SimpleNamespace
    st={'identity':{'root':str(tmp_path)},'session_id':'old-session','run_id':'old-run'}
    descriptor={'repository':str(tmp_path),'session_id':'old-session','run_id':'old-run'}
    validator=SimpleNamespace(legacy_readonly=descriptor,readonly=SimpleNamespace(enabled=True))
    monkeypatch.delitem(sys.modules,'_al_run_pin_context',raising=False)
    monkeypatch.setitem(sys.modules,'_al_validator_context',validator)
    assert C.require_current(st) is None
    with pytest.raises(SystemExit,match='lacks rework recovery capability'):C.require(st)
    for altered in (dict(st,run_id='foreign'),dict(st,proof_admission={})):
        with pytest.raises(SystemExit,match='protected managed'):C.require_current(altered)
    validator.readonly.enabled=False
    with pytest.raises(SystemExit,match='protected managed'):C.require_current(st)
