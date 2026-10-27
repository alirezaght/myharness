"""Separate code assessment, provider verification and business acceptance.

Assessments are diagnostic and never substitute for formal independent review.
Provider results are reconstructed from actual gate attempts, never submitted.
"""
import json
from pathlib import Path
import time

import al_artifacts as A
import al_ledger as L
import al_scope as S
import al_store
import al_task
import al_traceability as T
from al_schema import refuse


def binding(st):
    from al_verification_authority import authority_binding
    authority = authority_binding(st) if 'proof_admission' in st else None
    return {'run_id':st['run_id'],'task_hash':al_task.task_hash(st),
            'candidate_hash':S.capture_candidate(st)['fingerprint'],
            'verification_authority_hash':authority['hash'] if authority else None}


def _aggregate(rows):
    if not rows:return 'NOT_APPLICABLE'
    # A real failure is never hidden behind another provider's missing prerequisite.
    for status in ['FAIL','BLOCKED','STALE','RUNNING','NOT_RUN']:
        if any(r['status']==status for r in rows):return status
    return 'PASS'


def providers(st):
    import al_gates as G
    import al_gate_continuation as C
    m=G.manifest(st)
    rows=[]
    if not m:return {'status':'BLOCKED','required':True,'reason':'repository gate discovery not complete','checks':[]}
    states,_=C._states(st,m,'candidate')
    for gate in m['gates']:
        if not gate['required'] or gate['applicability']=='NOT_APPLICABLE':continue
        if gate.get('execution_kind')!='remote' and gate['applicability']!='UNRESOLVED':continue
        entry,_=C._attempt(st,'candidate',gate)
        state=states[gate['id']]
        status={'valid':'PASS','failed':'FAIL','stale':'STALE','missing':'NOT_RUN',
                'unresolved':'RUNNING' if entry and entry['status']=='RUNNING' else 'BLOCKED',
                'unverified':'BLOCKED','accepted-base-decision':'BLOCKED'}.get(state,'BLOCKED')
        if entry and entry.get('remote_provenance',{}).get('status')=='failed':status='FAIL'
        rows.append({'gate':gate['id'],'status':status,'evidence':entry['id'] if entry else None,
                     'provider_result':entry.get('remote_provenance') if entry else None,
                     'reason':entry.get('note','') if entry else gate.get('reason','provider check not run')})
    return {'status':_aggregate(rows),'required':bool(rows),'checks':rows,
            'reason':'Actual required provider checks' if rows else 'No required provider checks in the current sourced manifest'}


def business_requirement(st):
    package=A.read_json(st['task_package'])
    requirement=package.get('business_acceptance')
    if requirement is None:return None
    if (not isinstance(requirement,dict) or set(requirement)!={'id','source','required','description'}
            or type(requirement['required']) is not bool
            or any(not isinstance(requirement[k],str) or not requirement[k].strip() for k in ['id','source','description'])):
        refuse('invalid task-owned business acceptance requirement')
    return requirement if requirement['required'] else None


def business(st):
    requirement=business_requirement(st)
    if requirement is None:
        return {'status':'NOT_APPLICABLE','required':False,'reason':'Accepted task has no required business acceptance decision'}
    refs=st.get('business_acceptances',[])
    if not refs:return {'status':'NOT_RUN','required':True,'requirement':requirement,'reason':'Required business decision has not been recorded'}
    value=A.read_json(refs[-1])
    current=value['binding']==binding(st) and value['requirement']==requirement
    return {'status':value['decision'] if current else 'STALE','required':True,'requirement':requirement,
            'decision':refs[-1],'reason':value['rationale'] if current else 'Task or source changed since the decision'}


def code(st):
    refs=st.get('code_assessments',[])
    if refs:
        value=A.read_json(refs[-1])
        if value['binding']!=binding(st):return {'status':'STALE','assessment':refs[-1],'authorizes_approval':False}
        try:
            for criterion in value['criteria']:
                if criterion['status']=='SATISFIED':T.evidence(st,criterion['evidence'])
        except (OSError,ValueError,SystemExit):return {'status':'STALE','assessment':refs[-1],'authorizes_approval':False}
        return {'status':value['status'],'assessment':refs[-1],'authorizes_approval':False}
    review=st.get('review')
    if review:
        if review['candidate']['fingerprint']!=binding(st)['candidate_hash']:return {'status':'STALE','authorizes_approval':False}
        if review.get('comparison') or review.get('fresh'):
            ref=review.get('comparison') or review['fresh'];report=A.read_json(ref)
            return {'status':'CODE_ACCEPTABLE' if report['status']=='APPROVED' else 'CHANGES_REQUIRED',
                    'formal_report':ref,'authorizes_approval':False}
        return {'status':'IN_PROGRESS','authorizes_approval':False}
    return {'status':'NOT_RUN','authorizes_approval':False}


def status(st):
    return {'schema_version':1,'kind':'acceptance-status-v1','binding':binding(st),
            'code_assessment':code(st),'provider_verification':providers(st),'business_acceptance':business(st),
            'overall_status':st['status'],'final_approval':'independent fresh/comparison review and all required checks remain mandatory'}


def completion_gaps(st):
    decision=business(st)
    return ['business acceptance '+decision['status']] if decision['required'] and decision['status']!='ACCEPTED' else []


def packet(st):
    from al_verification_authority import authority_binding
    authority=authority_binding(st) if 'proof_admission' in st else None
    criteria=[r['id'] for r in authority['value']['criteria']] if authority else A.read_json(st['task_package'])['criteria']
    return {'schema_version':1,'kind':'code-assessment-packet-v1','binding':binding(st),'criteria':criteria,
            'purpose':'non-authorizing code assessment; not a formal Architect review',
            'provider_verification':providers(st),'business_acceptance':business(st)}


def assess(st, value):
    required={'schema_version','kind','binding','reviewer','criteria'}
    if not isinstance(value,dict) or set(value)!=required or type(value['schema_version']) is not int or value['schema_version']!=1 or value['kind']!='code-assessment-v1':
        refuse('invalid closed code assessment contract')
    if value['binding']!=binding(st):refuse('code assessment binding is stale or foreign')
    if not isinstance(value['reviewer'],str) or not value['reviewer'].strip():refuse('code assessment requires reviewer provenance')
    from al_service_coverage import admission_gaps
    from al_writers import operation_gaps
    if admission_gaps(st,current=True) or operation_gaps(st):refuse('code assessment requires complete discovery and settled operations')
    expected=packet(st)['criteria'];rows=value['criteria']
    if not isinstance(rows,list) or len(rows)!=len(expected) or {r.get('id') for r in rows}!=set(expected):refuse('code assessment must cover every accepted criterion')
    for row in rows:
        if set(row)!={'id','status','rationale','locations','evidence'} or row['status'] not in {'SATISFIED','UNSATISFIED','BLOCKED'} or not isinstance(row['rationale'],str) or not row['rationale'].strip():
            refuse('invalid code assessment criterion')
        T.locations(row['locations'])
        if row['status']=='SATISFIED':T.evidence(st,row['evidence'])
        elif not isinstance(row['evidence'],list):refuse('criterion evidence must be an array')
    result=dict(value,at=time.time(),authorizes_approval=False)
    result['status']='CHANGES_REQUIRED' if any(r['status']=='UNSATISFIED' for r in rows) else 'BLOCKED' if any(r['status']=='BLOCKED' for r in rows) else 'CODE_ACCEPTABLE'
    return result


def cmd_acceptance(a,sid):
    st=al_store.load(sid)
    if a.action=='status':print(json.dumps(status(st),sort_keys=True));return
    if a.action=='packet':print(json.dumps(packet(st),sort_keys=True));return
    value=json.loads(Path(a.file).read_bytes())
    if a.action=='assess':
        result=assess(st,value);key='code_assessments'
    else:
        requirement=business_requirement(st)
        if not requirement:refuse('accepted task has no required business acceptance')
        if (not isinstance(value,dict) or set(value)!={'schema_version','kind','binding','requirement_id','decision','source','rationale'}
                or type(value['schema_version']) is not int or value['schema_version']!=1 or value['kind']!='business-acceptance-v1'
                or value['binding']!=binding(st) or value['requirement_id']!=requirement['id']
                or value['source']!=requirement['source'] or value['decision'] not in {'ACCEPTED','REJECTED'}
                or not isinstance(value['rationale'],str) or not value['rationale'].strip()):refuse('invalid or unbound business acceptance decision')
        result=dict(value,requirement=requirement,at=time.time());key='business_acceptances'
    ref=A.snapshot_bytes(sid,json.dumps(result,sort_keys=True).encode(),result['kind'],a.file)
    st.setdefault(key,[]).append(ref);al_store.save(sid,st)
    print(json.dumps({'artifact':ref,'status':result.get('status',result.get('decision')),'authorizes_approval':False},sort_keys=True))


def register(sub):
    commands=sub.add_parser('acceptance').add_subparsers(dest='action',required=True)
    for name in ['status','packet','assess','business']:
        p=commands.add_parser(name)
        if name in {'assess','business'}:p.add_argument('--file',required=True)
        p.set_defaults(fn=cmd_acceptance)
