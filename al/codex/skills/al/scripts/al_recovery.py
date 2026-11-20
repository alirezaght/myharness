"""Durable stopping checkpoints and conservative resume."""
import json
import time
from pathlib import Path
import al_artifacts as A
import al_scope as S
import al_store
import al_task
from al_schema import refuse
from al_writers import operation_gaps

def require_running(st):
    if st['status']!='RUNNING' or st['phase']=='DONE':refuse('command requires a nonterminal RUNNING run')

def checkpoint(st,sid,reason,instructions,recipient=None,artifact=None):
    data={'schema_version':1,'run_id':st['run_id'],'identity':st['identity'],'task_hash':al_task.task_hash(st),
          'candidate_hash':S.candidate_fingerprint(st),'prior_phase':st['phase'],
          'phase_occurrence':len(st['phase_occurrences'])-1,
          'outstanding':operation_gaps(st),'reason':reason,'resume_instructions':instructions,'recipient':recipient,'transfer_artifact':artifact,'at':time.time()}
    ref=A.snapshot_bytes(sid,json.dumps(data,sort_keys=True).encode(),'checkpoint','runtime')
    st.setdefault('checkpoints',[]).append(ref)
    return ref

def close_occurrence(st):
    now=time.time()
    if st['phase_timings'][st['phase']].get('ended') is None:st['phase_timings'][st['phase']]['ended']=now
    if st['phase_occurrences'] and st['phase_occurrences'][-1]['ended'] is None:st['phase_occurrences'][-1]['ended']=now


def apply_checkpoint(st,data,checkpoint_id,*,restoration,rework_continuation=None,retry_agent=None):
    if restoration not in {'checkpoint-resume','agent-retry','rework-continuation'}:
        refuse('checkpoint restoration needs a supported operation')
    if ((restoration=='agent-retry') != (retry_agent is not None)
            or (restoration=='rework-continuation') != (rework_continuation is not None)):
        refuse('checkpoint restoration authority does not match operation')
    if data['run_id']!=st['run_id'] or data['identity']!=st['identity'] or data['task_hash']!=al_task.task_hash(st):refuse('checkpoint identity/task mismatch')
    from al_gate_refresh import refresh
    currency=refresh(st)
    retained_marker=st.get('gate_recovery')
    changed=data['candidate_hash']!=S.candidate_fingerprint(st)
    phase=data['prior_phase']
    if (changed or not currency['current']) and phase in {'REVIEW','FINAL_VERIFICATION','VERIFICATION'}:
        from al_transitions import structural_gaps
        gaps=structural_gaps(st)
        if gaps:refuse('cannot recover completed workflow: '+str(gaps))
        if currency['current']:phase='VERIFICATION'
        else:
            phase='PLANNING';st['spec_status']=None
            st.setdefault('recovery_replans',[]).append(dict(currency,prior_phase=data['prior_phase'],at=time.time()))
    from al_gate_recovery import RETAINED
    if not currency['current'] and data['prior_phase'] in RETAINED:
        st['gate_recovery']={'checkpoint':checkpoint_id,'phase':phase,'superseded_manifest':currency['superseded']['sha256'] if currency['superseded'] else None,'reasons':currency['reasons'],'replacement_manifest':None}
    if changed or not currency['current']:
        st['review']=None;st['review_status']=None;st['evidence']['invalidated_at']=time.time()
    st['status']='RUNNING';st['stop_reason']=None
    if retained_marker is not None and st.get('gate_recovery') is retained_marker:
        from al_gate_recovery import continue_phase
        proof={'retry_agent':retry_agent} if retry_agent is not None else (rework_continuation or {})
        continue_phase(st,phase,restoration,checkpoint=checkpoint_id,**proof)
    al_store.set_phase(st,phase)
    return changed


def cmd_checkpoint(a,sid):
    st=al_store.load(sid)
    if a.cmd=='resume':
        ref=next((x for x in st.get('checkpoints',[]) if x['id']==a.checkpoint),None)
        if not ref or st.get('stopping_checkpoint') != ref['id']: refuse('resume requires the latest stopping checkpoint')
        resumable = {
            'WAITING_FOR_USER', 'INTERRUPTED', 'TRANSFERRED', 'AGENT_FAILED', 'SPEC_BLOCKED'
        }
        if st['status'] not in resumable:
            refuse('run is not resumably stopped')
        if operation_gaps(st): refuse('reconcile outstanding operations before resume')
        if st.get('failure'): refuse('pending agent failure requires guarded retry, not resume')
        changed=apply_checkpoint(st,A.read_json(ref),ref['id'],restoration='checkpoint-resume')
        st.setdefault('resumes',[]).append({'checkpoint':ref['id'],'changed':changed,
                                          'phase_occurrence':len(st['phase_occurrences'])-1,'at':time.time()})
    else:
        require_running(st)
        data=json.loads(Path(a.file).read_bytes()) if getattr(a,'file',None) else {'reason':a.reason,'resume_instructions':a.resume_instructions}
        if not data.get('reason') or not data.get('resume_instructions'): refuse('checkpoint needs reason and resume instructions')
        artifact=None
        if a.cmd=='transfer':
            if not data.get('recipient') or not data.get('artifact'): refuse('transfer needs recipient and artifact')
            artifact=A.snapshot_artifact(sid,Path(data['artifact']),'transfer')
        ref=checkpoint(st,sid,data['reason'],data['resume_instructions'],data.get('recipient'),artifact)
        if a.cmd!='checkpoint':
            st['status']={'waiting':'WAITING_FOR_USER','interrupt':'INTERRUPTED','transfer':'TRANSFERRED'}[a.cmd]
            st['stop_reason']=data['reason']; st['stopping_checkpoint']=ref['id']
            close_occurrence(st)
        print(ref['id'])
    al_store.save(sid,st)

def cmd_reconcile(a,sid):
    st=al_store.load(sid)
    e=next((x for x in st['evidence']['entries'] if x['id']==a.id),None)
    if not e or (e['status']!='RUNNING' and not e.get('interrupted')) or e.get('reconciliation'): refuse('check is not unresolved')
    import al_ledger as L
    if L._alive(e.get('owner_pid')) or L._alive(e.get('pid')): refuse('cannot reconcile a living check')
    from al_agent_exchange import consume_verification, produce
    from al_artifact_cli import read_input
    from types import SimpleNamespace
    import copy
    consume_verification(st, e)
    if 'integration' in st:
        from al_execution_tasks import authority
        authority(st, 'revalidate')
    if not a.reason.strip(): refuse('check reconciliation requires a reason')
    # Typed terminal observations must still have their original authenticated log.
    if ('verification-result:' + e['id']) in st.get('agent_contracts', {}):
        if L.assess(e)[0] in {'LOG_MISSING', 'LOG_ALTERED'}:
            refuse('original check log provenance changed')
    raw, source = read_input(SimpleNamespace(file=a.artifact, stdin=False))
    original = {'schema_version': 1, 'entry': copy.deepcopy(e),
                'contract': copy.deepcopy(st.get('agent_contracts', {}).get('verification-result:' + e['id']))}
    observation = A.snapshot_bytes(sid, json.dumps(original, sort_keys=True).encode(),
                                   'check-observation-v1', 'authorized check reconciliation')
    artifact = A.snapshot_bytes(sid, raw, 'check-reconciliation', source)
    e.update(status='INFRA', note='interrupted check explicitly reconciled', ended_at=time.time(),
             reconciliation={'schema_version': 1, 'reason': a.reason, 'artifact': artifact,
                             'observation': observation})
    # The authorized transition precedes outbox verdict calculation in save().
    produce(st, 'verification-result', e['id'], e, status='observed', refs=[
        {'reference': {'kind': ref['kind'], 'id': ref['id'], 'sha256': ref['sha256']},
         'purpose': purpose, 'required': True}
        for ref, purpose in ((observation, 'immutable original check observation'),
                             (artifact, 'actual reconciliation artifact'))])
    al_store.save(sid,st)

def register(sub):
    for name in ('checkpoint','waiting','interrupt','transfer','resume'):
        p=sub.add_parser(name); p.set_defaults(fn=cmd_checkpoint)
        if name=='resume': p.add_argument('--checkpoint',required=True)
        elif name=='checkpoint': p.add_argument('--reason',required=True); p.add_argument('--resume-instructions',required=True)
        else: p.add_argument('--file',required=True)
    p=sub.add_parser('check-reconcile'); p.add_argument('--id',required=True); p.add_argument('--reason',required=True); p.add_argument('--artifact',required=True); p.set_defaults(fn=cmd_reconcile)
