"""Checkpoint-bound repair admission for unfinished recovered phases."""
import time
import copy
import al_artifacts as A
import al_task
from al_schema import refuse
from al_writers import operation_gaps

RETAINED={'PLANNING','PSEUDOCODE','PLAN_CONFLICT','IMPLEMENTATION','FINALIZATION','REWORK','CORRECTION'}
EXCEPTION={'PSEUDOCODE','PLAN_CONFLICT','IMPLEMENTATION','FINALIZATION','REWORK','CORRECTION'}


def marker(st):
    obligation=st.get('gate_recovery')
    if not isinstance(obligation,dict) or not obligation or st['status']!='RUNNING' or obligation.get('phase')!=st['phase']:refuse('gate recovery does not match retained phase')
    ref=next((r for r in st.get('checkpoints',[]) if r['id']==obligation.get('checkpoint')),None)
    if not ref:refuse('gate recovery checkpoint missing')
    data=A.read_json(ref)
    if data['run_id']!=st['run_id'] or data['identity']!=st['identity'] or data['task_hash']!=al_task.task_hash(st) or data['prior_phase']!=obligation['phase']:refuse('gate recovery checkpoint identity/phase mismatch')
    hashes={r['sha256'] for r in st.get('gate_manifests',[])}
    if obligation.get('superseded_manifest') is not None and obligation['superseded_manifest'] not in hashes:refuse('superseded recovery manifest missing')
    if not isinstance(obligation.get('reasons'),list) or not obligation['reasons']:refuse('gate recovery reason missing')
    return obligation


def planning(st,sid,anticipated=()):
    if operation_gaps(st):refuse('reconcile operations before gate planning')
    if st['phase'] not in {'PLANNING','VERIFICATION'}:
        if st['phase'] not in EXCEPTION:refuse('gate planning requires planning/finalization/verification')
        if 'gate_recovery' not in st:
            if st.get('spec_status')!='SPEC_READY':refuse('replacement planning requires ready specification')
            from al_gate_currency import check
            currency=check(st)
            if currency['current']:
                from al_gate_coverage import prospective_gaps
                currency['reasons']=prospective_gaps(st,anticipated)
                if not currency['reasons']:
                    # Planning is a checkpoint: an edit that changed obligations (a new import, a new
                    # consumer) is found by the full inventory even when rule hashes are unchanged.
                    import al_artifacts as A
                    import al_gate_inventory as V
                    currency['reasons']=V.gaps(st,A.read_json(st['gate_manifests'][-1]),anticipated)
                if not currency['reasons']:refuse('current obligations do not permit replacement planning in unfinished phase')
            from al_recovery import checkpoint
            ref=checkpoint(st,sid,'Current gate obligations require replacement','Replace stale obligations and satisfy base readiness before continuing')
            st['gate_recovery']={'checkpoint':ref['id'],'phase':st['phase'],'superseded_manifest':currency['superseded']['sha256'] if currency['superseded'] else None,'reasons':currency['reasons'],'replacement_manifest':None}
        marker(st)
    if st.get('gate_recovery'):
        marker(st)


def ready(st):
    from al_gate_refresh import refresh
    currency=refresh(st)
    if not currency['current']:
        from al_recovery_guidance import refuse_guided
        refuse_guided(st,'current gate obligations require replanning: '+str(currency['reasons']))
    if st.get('gate_recovery'):
        obligation=marker(st)
        if obligation.get('replacement_manifest')!=st['gate_manifests'][-1]['sha256']:refuse('active manifest is not the recovery replacement')
    # Base gates run alongside implementation; review and approval still require them (workflow_gaps).


def complete(st):
    obligation=st.pop('gate_recovery',None)
    if obligation:
        evidence=[r['evidence'] for r in st.get('gate_runs',[]) if r['stage']=='base' and r['manifest']==obligation['replacement_manifest']]
        st.setdefault('gate_recovery_history',[]).append(dict(obligation,evidence=evidence,completed_at=time.time()))


def amendment_binding(st):
    previous=copy.deepcopy(marker(st))
    if operation_gaps(st):refuse('reconcile operations before recovery amendment')
    ref=next(r for r in st['checkpoints'] if r['id']==previous['checkpoint'])
    return previous,copy.deepcopy(ref),A.read_json(ref)


def amend(st,sid,amendment,binding):
    from al_recovery import checkpoint
    previous,old_ref,data=binding
    new_ref=checkpoint(st,sid,'User amendment '+amendment['id']+' supersedes recovery checkpoint',data['resume_instructions'])
    st.setdefault('gate_recovery_supersessions',[]).append({
        'status':'SUPERSEDED_BY_AMENDMENT','amendment':{'id':amendment['id'],'artifact':amendment['artifact']},
        'old_checkpoint':old_ref,'new_checkpoint':copy.deepcopy(new_ref),
        'marker':previous,'at':time.time()})
    st['gate_recovery']=dict(previous,checkpoint=new_ref['id'],phase='PLANNING',replacement_manifest=None)
