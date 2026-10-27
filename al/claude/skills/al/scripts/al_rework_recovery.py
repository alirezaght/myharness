"""Bounded Architect reassessment and explicit user continuation."""
from pathlib import Path
import json
import al_proof_json as J
import al_rework_recovery_contract as C
import al_rework_recovery_attempts as B
import al_recovery as R
import al_store as Store
from al_schema import refuse
from al_writers import operation_gaps

DECISION={'schema_version','kind','request_id','occurrence_id','binding','packet_hash','attempt_id','invocation_id','model','effort','policy_digest','decision','basis','rationale','next_action','remaining_criteria','inspected_artifacts','evidence'}
CONTINUATION={'schema_version','kind','request_id','checkpoint_id','binding','user_authorization','next_action','grant_size'}

def settled(st):
    gaps=operation_gaps(st)
    if gaps:refuse('recovery requires reconciled operations: '+str(gaps))

def occurrence(st):
    r=C.require(st)
    if not r['pending'] or st['status']!='RUNNING' or st['phase']!='REWORK_REASSESSMENT':refuse('no pending Architect reassessment')
    return next(o for o in r['occurrences'] if o['id']==r['pending'])

def stop(st,sid,o,reason,kind):
    r=st['rework_recovery']
    ref=R.checkpoint(st,sid,reason,'Use a bound rework-recovery continue request with explicit user authorization.')
    r['stop']={'occurrence_id':o['id'],'checkpoint_id':ref['id'],'kind':kind,'reason':reason}
    r['pending']=None;st['status']='STOPPED_WITH_BLOCKERS';st['stop_reason']=reason;st['stopping_checkpoint']=ref['id']
    o['dispositions'].append(dict(r['stop'],checkpoint=ref))
    R.close_occurrence(st)

def enter(st,sid,request):
    r=C.require(st);settled(st)
    ref=R.checkpoint(st,sid,'rework batch exhausted','Architect reassessment required before more rework.')
    o={'id':'RR'+str(len(r['occurrences'])+1),'request':request,'checkpoint':ref,
       'phase_occurrence':len(st['phase_occurrences'])-1,'binding':None,'packets':[],'dispositions':[]}
    o['binding']=C.binding(st,o);r['occurrences'].append(o)
    if r['automatic_authorizations']>=2:stop(st,sid,o,'automatic rework recovery cap reached','cap')
    else:
        r['pending']=o['id'];Store.set_phase(st,'REWORK_REASSESSMENT',['al-architect'])
    Store.save(sid,st)
    return o

def current(st,o):
    settled(st)
    if C.binding(st,o)!=o['binding']:refuse('stale recovery candidate/task/history binding')

def cmd_packet(a,sid):
    st=Store.load(sid);o=occurrence(st);current(st,o)
    replace=getattr(a,'replace_unavailable_attempt',False)
    if o['packets'] and not replace:
        ref=o['packets'][-1]['packet'];B.validate(st,o['packets'][-1]['attempt'],o['binding'])
    else:
        try:attempt=B.issue(st,sid,o['binding'],o['packets'][-1]['attempt'] if o['packets'] else None,replace=replace)
        except SystemExit as exc:
            # Model availability is a blocker, never an implicit fallback.
            if str(exc)!='al_state: Architect model unavailable':raise
            stop(st,sid,o,str(exc),'model');Store.save(sid,st);raise
        data={'schema_version':1,'kind':'rework-recovery-packet-v1','occurrence_id':o['id'],'binding':o['binding'],
              'request':o['request'],'attempt':J.read(attempt),'task_package':J.read(st['task_package']),
              'amendments':st['amendments'],'recovery_lineage':C.lineage(st),'rework':st['rework'],
              'evidence':st['evidence'],'gates':st.get('gate_manifests',[]),'readiness':st.get('gate_recovery')}
        ref=J.snapshot(sid,data,'rework-recovery-packet');o['packets'].append({'attempt':attempt,'packet':ref});Store.save(sid,st)
    data=J.read(ref)
    if a.output:Path(a.output).write_bytes(J.raw(data))
    print(json.dumps({'packet':ref,'content':data},indent=2))

def replay(st,data):
    r=C.require(st)
    for row in r['decisions']+r['continuations']:
        if row['request_id']==data['request_id']:
            if row['request_hash']!=J.digest(data):refuse('request id reused with altered content')
            print(json.dumps(J.read(row['receipt']),indent=2,sort_keys=True));return True
    return False

def read_request(a,fields,kind):
    with Path(a.file).open('rb') as f:data=J.loads(f.read(16*1024*1024+1))
    J.closed(data,fields,kind)
    if type(data['schema_version']) is not int or data['schema_version']!=1 or data['kind']!=kind:refuse('unsupported recovery request schema')
    J.text(data['request_id'],'request id');J.text(data['next_action'],'bounded next action')
    return data

def evidence(st,claims):
    import al_ledger as L
    if not isinstance(claims,list) or not claims:refuse('recovery needs concrete supporting evidence')
    seen=set()
    for c in claims:
        J.closed(c,{'id','entry_hash','claim'},'recovery evidence claim')
        e=L.find(st,c['id'])
        if not e or J.digest(e)!=c['entry_hash'] or e.get('run')!=st['run_id']:refuse('foreign/stale recovery evidence')
        verdict,_=L.assess(e)
        if c['claim']=='historical-red':
            if e['status']!='FAIL' or verdict in {'LOG_MISSING','LOG_ALTERED','RUNNING','INTERRUPTED'}:refuse('invalid historical red evidence')
        elif c['claim']=='current-success':
            if e['status']!='PASS' or L.entry_verdict(st,e)[0]!='VALID':refuse('current success requires VALID PASS')
        else:refuse('unknown recovery evidence claim')
        seen.add(c['entry_hash'])
    return seen

def accepted(st,sid,data,kind,grant_id,consumption=None):
    receipt={'schema_version':1,'kind':'rework-recovery-receipt-v1','request_id':data['request_id'],
             'request_hash':J.digest(data),'grant_id':grant_id,'consumption':consumption,'binding':data['binding']}
    row={'request_id':data['request_id'],'request_hash':J.digest(data),
         'report':J.snapshot(sid,data,'rework-recovery-'+kind),'receipt':J.snapshot(sid,receipt,'rework-recovery-receipt')}
    return row,receipt

def add_grant(st,kind,request_id):
    r=st['rework_recovery'];g=C.grant(kind,request_id,len(r['grants'])+1);r['grants'].append(g)
    st['rework'].update(batch=len(r['grants'])-1,used=0)
    return g

def cmd_decide(a,sid):
    data=read_request(a,DECISION,'rework-recovery-decision-v1');st=Store.load(sid)
    if replay(st,data):return
    o=occurrence(st);current(st,o);r=st['rework_recovery']
    if data['occurrence_id']!=o['id'] or data['binding']!=o['binding'] or not o['packets']:refuse('decision occurrence/packet mismatch')
    packet=o['packets'][-1];attempt=B.validate(st,packet['attempt'],o['binding'])
    if data['packet_hash']!=packet['packet']['sha256'] or any(data[k]!=attempt[k] for k in ('attempt_id','effort','policy_digest')) or data['model'] not in {attempt['model'],attempt['slug']}:refuse('decision attempt provenance mismatch')
    J.text(data['invocation_id'],'Architect invocation');J.text(data['rationale'],'recovery rationale')
    J.strings(data['inspected_artifacts'],'inspected artifacts',nonempty=True)
    if not {packet['packet']['sha256'],packet['attempt']['sha256']}<=set(data['inspected_artifacts']):refuse('decision did not inspect packet/attempt')
    criteria=J.read(st['task_package'])['criteria'];J.strings(data['remaining_criteria'],'remaining criteria',nonempty=True)
    if not set(data['remaining_criteria'])<=set(criteria):refuse('unknown remaining criterion')
    if data['decision'] not in {'allow','decline'}:refuse('invalid recovery decision')
    if not isinstance(data['evidence'],list):refuse('invalid recovery evidence claims')
    if data['decision']=='decline' and data['evidence']:evidence(st,data['evidence'])
    if data['decision']=='allow':
        if data['basis'] not in {'verified-progress','new-independent-defect','revised-diagnosis'}:refuse('no-progress recovery refused')
        claims=evidence(st,data['evidence'])
        for row in r['decisions']:
            prior=J.read(row['report'])
            if prior['decision']=='allow' and prior['next_action']==data['next_action'] and not claims-{c['entry_hash'] for c in prior['evidence']}:refuse('repeated approach without new evidence')
        from al_rework import validated_request,consume_return
        from types import SimpleNamespace
        request=validated_request(st,SimpleNamespace(**o['request']['arguments']))
        if request!=o['request']:refuse('pending rework request changed')
        from al_new_origin import require_admitted
        from al_gate_recovery import ready
        require_admitted(st)
        if st.get('gate_recovery'):ready(st)
        if r['automatic_authorizations']>=2:refuse('automatic recovery cap reached')
        # All authority checks precede the single atomic grant + consumption commit.
        g=add_grant(st,'automatic',data['request_id']);r['automatic_authorizations']+=1;r['pending']=None
        consume_return(st,request)
        row,receipt=accepted(st,sid,data,'decision',g['id'],st['rework']['total'])
        r['decisions'].append(row)
        o['dispositions'].append({'kind':'allowed','request_id':data['request_id'],'receipt':row['receipt']})
    else:
        if data['basis']!='no-useful-next-step':refuse('decline requires no-useful-next-step basis')
        row,receipt=accepted(st,sid,data,'decision',None);r['decisions'].append(row)
        stop(st,sid,o,data['rationale'],'declined')
    Store.save(sid,st);print(json.dumps(receipt,indent=2,sort_keys=True))

def cmd_continue(a,sid):
    data=read_request(a,CONTINUATION,'rework-recovery-continuation-v1');st=Store.load(sid)
    if replay(st,data):return
    r=C.require(st);stop_record=r['stop'];settled(st)
    if st['status']!='STOPPED_WITH_BLOCKERS' or not stop_record or stop_record['kind'] not in {'cap','declined'}:refuse('stop is not eligible for budget continuation')
    if data['checkpoint_id']!=st.get('stopping_checkpoint') or data['checkpoint_id']!=stop_record['checkpoint_id'] or data['binding']!=C.binding(st):refuse('stale/foreign continuation checkpoint or candidate')
    if type(data['grant_size']) is not int or data['grant_size']!=2:refuse('continuation grants exactly two returns')
    if st.get('failure') or any(b.get('state')!='Resolved' for b in st.get('integration',{}).get('blockers',{}).values()):refuse('unresolved independent blocker cannot be cleared by budget continuation')
    auth=J.read(data['user_authorization']);J.closed(auth,{'schema_version','kind','request_id','binding','message','provenance'},'user continuation authorization')
    if type(auth['schema_version']) is not int or auth['schema_version']!=1 or auth['kind']!='explicit-user-continuation-v1' or auth['request_id']!=data['request_id'] or auth['binding']!=data['binding']:refuse('explicit user authorization binding mismatch')
    J.text(auth['message'],'explicit user message');J.text(auth['provenance'],'user message provenance')
    o=next(o for o in r['occurrences'] if o['id']==stop_record['occurrence_id'])
    R.apply_checkpoint(st,J.read(o['checkpoint']),o['checkpoint']['id'])
    g=add_grant(st,'user',data['request_id']);row,receipt=accepted(st,sid,data,'continuation',g['id'])
    r['continuations'].append(row);r['pending']=None;r['stop']=None
    Store.save(sid,st);print(json.dumps(receipt,indent=2,sort_keys=True))

def register(sub):
    p=sub.add_parser('rework-recovery');actions=p.add_subparsers(dest='action',required=True)
    q=actions.add_parser('packet');q.add_argument('--output');q.add_argument('--replace-unavailable-attempt',action='store_true');q.set_defaults(fn=cmd_packet)
    for name,fn in (('decide',cmd_decide),('continue',cmd_continue)):
        q=actions.add_parser(name);q.add_argument('--file',required=True);q.set_defaults(fn=fn)
