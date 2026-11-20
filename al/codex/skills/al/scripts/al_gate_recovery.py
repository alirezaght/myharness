"""Checkpoint-bound repair admission for unfinished recovered phases."""
import time
import copy
import al_artifacts as A
import al_task
from al_schema import refuse
from al_writers import operation_gaps

RETAINED={'PLANNING','PSEUDOCODE','PLAN_CONFLICT','IMPLEMENTATION','FINALIZATION','REWORK','CORRECTION','REWORK_REASSESSMENT'}
EXCEPTION=RETAINED-{'PLANNING'}


def resumed_origin(st,index,expected):
    """Trace only same-phase restores backed by immutable stopping checkpoints."""
    while index>expected:
        step=next((s for s in st.get('gate_recovery',{}).get('continuations',[])
                   if s['occurrence']==index and s.get('command') in {'checkpoint-resume','agent-retry'}),None)
        if step is None:refuse('recovery continuation lacks authenticated resume ancestry')
        checkpoint_edge(st,step,index)
        index-=1
    return index==expected


def checkpoint_edge(st,step,index):
    ref=next((r for r in st.get('checkpoints',[]) if r['id']==step.get('checkpoint')),None)
    if ref is None:refuse('recovery restoration checkpoint missing')
    data=A.read_json(ref)
    source=data.get('phase_occurrence')
    if (type(source) is not int or not 0<=source<index or type(data.get('schema_version')) is not int
            or data['schema_version']!=1
            or data['run_id']!=st['run_id'] or data['identity']!=st['identity']
            or data['task_hash']!=al_task.task_hash(st)
            or st['phase_occurrences'][source]['phase']!=data['prior_phase']):
        refuse('recovery restoration checkpoint identity/occurrence mismatch')
    if step['command']=='checkpoint-resume':
        if (source!=index-1 or step['from']!=data['prior_phase']
                or not any(r['checkpoint']==ref['id'] and type(r.get('phase_occurrence')) is int
                           and r['phase_occurrence']==index
                           for r in st.get('resumes',[]))):
            refuse('recovery restoration lacks public resume record')
    elif step['command']=='agent-retry':
        row=next((r for r in st.get('retry_history',[]) if r['checkpoint']==ref
                  and type(r.get('phase_occurrence')) is int and r['phase_occurrence']==index
                  and r['agent']==step.get('retry_agent')),None)
        failure=(row or {}).get('failure',{})
        used=st.get('retries',{}).get(step.get('retry_agent'))
        if (row is None or source!=index-1 or step['from']!=data['prior_phase']
                or failure.get('checkpoint')!=ref or failure.get('agent')!=row['agent']
                or failure.get('phase')!=data['prior_phase'] or failure.get('retry_safe')!='yes'
                or failure.get('kind')!='implementation' or type(used) is not int or used!=1):
            refuse('recovery restoration lacks guarded retry authority')
    else:
        import al_proof_json as J
        recovery=st.get('rework_recovery',{})
        occurrence=next((o for o in recovery.get('occurrences',[]) if o['id']==step.get('rework_occurrence')),None)
        row=next((r for r in recovery.get('continuations',[]) if r['request_id']==step.get('continuation_request')),None)
        if occurrence is None or row is None or occurrence['checkpoint']!=ref:
            refuse('recovery restoration lacks accepted user continuation')
        report=J.read(row['report'])
        if not any(d.get('kind') in {'cap','declined'} and d.get('checkpoint_id')==report['checkpoint_id']
                   for d in occurrence['dispositions']):
            refuse('recovery restoration lacks authorized stopping checkpoint')
        expected=source+1 if step['from']=='REWORK_REASSESSMENT' else source
        if not resumed_origin(st,index-1,expected):
            refuse('invalid user continuation origin')
    return {data['prior_phase']}


def rework_edge(st,step,index):
    """Closed lifecycle edges bind to retained recovery authority, never a free phase jump."""
    recovery=st.get('rework_recovery',{})
    occurrence=next((o for o in recovery.get('occurrences',[]) if o['id']==step.get('rework_occurrence')),None)
    if occurrence is None:refuse('recovery continuation lacks rework occurrence')
    source=step['from'];command=step['command'];prior=occurrence['phase_occurrence']
    if command=='rework-reassessment':
        if source not in {'FINALIZATION','VERIFICATION'} or prior!=index-1:
            refuse('invalid reassessment continuation origin')
        return {'REWORK_REASSESSMENT'}
    if command=='rework-recovery':
        allowed=next((d for d in occurrence['dispositions'] if d.get('kind')=='allowed'),None)
        if (source!='REWORK_REASSESSMENT' or not resumed_origin(st,index-1,prior+1) or allowed is None
                or not any(row['request_id']==allowed['request_id'] and row['receipt']==allowed['receipt']
                           for row in recovery.get('decisions',[]))):
            refuse('recovery continuation lacks accepted decision')
        return {'REWORK'}
    import al_proof_json as J
    record=next((r for r in st.get('rework_implementation_continuity',[])
                 if J.digest(r)==step.get('continuity_hash')),None)
    if record is None:refuse('recovery continuation lacks implementation record')
    bound=record['pending'] or (record['stop'] or {}).get('occurrence_id')
    expected=prior+1 if source=='REWORK_REASSESSMENT' else prior
    if bound!=occurrence['id'] or not resumed_origin(st,index-1,expected) or source not in {'REWORK_REASSESSMENT','FINALIZATION','VERIFICATION'}:
        refuse('invalid implementation continuation origin')
    return {'IMPLEMENTATION'}


def marker(st):
    obligation=st.get('gate_recovery')
    if not isinstance(obligation,dict) or not obligation or st['status']!='RUNNING' or obligation.get('continuation_phase', obligation.get('phase'))!=st['phase']:refuse('gate recovery does not match retained phase')
    ref=next((r for r in st.get('checkpoints',[]) if r['id']==obligation.get('checkpoint')),None)
    if not ref:refuse('gate recovery checkpoint missing')
    data=A.read_json(ref)
    if data['run_id']!=st['run_id'] or data['identity']!=st['identity'] or data['task_hash']!=al_task.task_hash(st) or data['prior_phase']!=obligation['phase']:refuse('gate recovery checkpoint identity/phase mismatch')
    hashes={r['sha256'] for r in st.get('gate_manifests',[])}
    if obligation.get('superseded_manifest') is not None and obligation['superseded_manifest'] not in hashes:refuse('superseded recovery manifest missing')
    if not isinstance(obligation.get('reasons'),list) or not obligation['reasons']:refuse('gate recovery reason missing')
    if 'continuation_phase' in obligation and not obligation.get('continuations'):
        refuse('recovery continuation provenance missing')
    previous = obligation['phase']
    for step in obligation.get('continuations', []):
        index = step.get('occurrence')
        if (type(index) is not int or not 0 < index < len(st['phase_occurrences'])
                or step.get('from') != previous
                or st['phase_occurrences'][index]['phase'] != step.get('to')
                or st['phase_occurrences'][index-1]['phase'] != step.get('from')
                or st['phase_occurrences'][index-1]['ended'] is None):
            refuse('invalid recovery continuation provenance')
        from al_transitions import COMPLETION_EDGES
        allowed = COMPLETION_EDGES.get(step['from'], set()) | ({'PLAN_CONFLICT'} if step['from'] == 'PSEUDOCODE' else set())
        if step.get('command', 'phase') == 'rework':
            allowed = {'REWORK'} if step['from'] in {'FINALIZATION', 'VERIFICATION'} else set()
        elif step.get('command') in {'checkpoint-resume','agent-retry','rework-continuation'}:
            allowed = checkpoint_edge(st,step,index)
        elif step.get('command') in {'rework-reassessment','rework-recovery','implementation-continuity'}:
            allowed = rework_edge(st,step,index)
        elif step.get('command', 'phase') not in {'phase', 'correction'}:
            refuse('invalid recovery continuation command')
        if step['to'] not in allowed:
            refuse('invalid recovery continuation edge')
        previous = step['to']
    if obligation.get('continuations') and obligation['continuations'][-1]['to'] != obligation.get('continuation_phase'):
        refuse('invalid recovery continuation phase')
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
    from al_service_coverage import require_ready
    require_ready(st,current=True)
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
    retained = {k: v for k, v in previous.items() if k not in {'continuation_phase', 'continuations'}}
    st['gate_recovery']=dict(retained,checkpoint=new_ref['id'],phase='PLANNING',replacement_manifest=None)


def continue_phase(st, destination, command, **provenance):
    value = marker(st)
    value.setdefault('continuations', []).append({'from': st['phase'], 'to': destination, 'command': command,
                                                'occurrence': len(st['phase_occurrences']),**provenance})
    value['continuation_phase'] = destination
