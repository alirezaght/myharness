"""Failure checkpoints and the sole budgeted failed-agent recovery edge."""
import sys
import time
from pathlib import Path
import al_artifacts as A
import al_models
import al_recovery as R
import al_store
from al_schema import refuse
from al_writers import operation_gaps


def record(st,sid,a):
    if st['phase']=='DONE':refuse('agent failure requires a nonterminal phase')
    if a.agent not in al_models.ROLES or not a.reason.strip():refuse('agent failure needs a valid role and reason')
    if st.get('failure'):refuse('pending agent failure must be retried or stopped')
    phase=st['phase'];parallel=st.get('exec_mode',{}).get(phase)=='parallel'
    st['failure']={'agent':a.agent,'phase':phase,'reason':a.reason,'at':time.time(),
                   'parallel':parallel,'peer':a.peer or None,'peer_status':a.peer_status,'retry_safe':a.retry_safe}
    ref=R.checkpoint(st,sid,a.reason,'Reconcile operations and peer state, then use guarded retry')
    st['failure']['checkpoint']=ref
    st['stopping_checkpoint']=ref['id'];st['status']='AGENT_FAILED';st['stop_reason']=a.reason
    R.close_occurrence(st)


def cmd_peer(a,sid):
    st=al_store.load(sid);failure=st.get('failure')
    if not failure or not (failure.get('parallel') or failure.get('peer')):refuse('no failed-agent peer obligation')
    if a.agent not in al_models.ROLES or a.agent==failure['agent'] or not a.reason.strip():refuse('peer reconciliation needs matching role and reason')
    if failure.get('peer') and failure['peer']!=a.agent:refuse('peer reconciliation role mismatch')
    if operation_gaps(st):refuse('reconcile outstanding operations before peer proof')
    proof=A.snapshot_artifact(sid,Path(a.artifact),'failed-agent-peer-reconciliation')
    failure['peer_reconciliation']={'agent':a.agent,'reason':a.reason,'artifact':proof,'at':time.time()}
    al_store.save(sid,st)


def cmd_retry(a,sid):
    st=al_store.load(sid)
    if a.agent not in al_models.ROLES:refuse('invalid retry role')
    used=st.get('retries',{}).get(a.agent,0)
    if used>=1:
        st['status']='STOPPED_WITH_BLOCKERS'
        st['stop_reason']=al_models.display(a.agent)+' already retried once; no retry budget left'
        al_store.save(sid,st);print('AL: '+st['stop_reason'],file=sys.stderr);sys.exit(7)
    failure=st.get('failure')
    if st['status'] not in {'AGENT_FAILED','WAITING_FOR_USER','INTERRUPTED','TRANSFERRED'} or not failure or failure.get('agent')!=a.agent:
        refuse('retry requires the matching pending agent failure')
    if not a.reason.strip() or failure.get('retry_safe')!='yes':refuse('retry requires nonempty reason and retry_safe=yes')
    if operation_gaps(st):refuse('reconcile outstanding writers/checks before retry')
    if failure.get('parallel') or failure.get('peer'):
        peer=failure.get('peer_reconciliation')
        if not peer or peer['agent']==a.agent or (failure.get('peer') and peer['agent']!=failure['peer']):refuse('peer state needs explicit reconciliation proof')
        A.read(peer['artifact'])
    ref=failure.get('checkpoint')
    if not ref or ref not in st.get('checkpoints',[]):refuse('failure checkpoint missing')
    changed=R.apply_checkpoint(st,A.read_json(ref),ref['id'])
    st.setdefault('retry_history',[]).append({'agent':a.agent,'reason':a.reason,'failure':failure,'checkpoint':ref,'changed':changed,'at':time.time()})
    st.setdefault('retries',{})[a.agent]=used+1;st['failure']=None
    al_store.save(sid,st);print('AL · retrying '+al_models.display(a.agent)+' (1/1) · '+a.reason)


def register(sub):
    p=sub.add_parser('agent-peer-reconcile');p.add_argument('agent');p.add_argument('--reason',required=True);p.add_argument('--artifact',required=True);p.set_defaults(fn=cmd_peer)
