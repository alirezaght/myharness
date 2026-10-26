"""Fresh and comparison Architect reports bound to immutable packets."""
import json
import time
from pathlib import Path
import al_artifacts as A
import al_assessments
import al_ledger as L
import al_scope as S
import al_store
import al_task
import al_traceability as T
from al_schema import refuse
from al_transitions import require_transition
from al_writers import operation_gaps
from al_gates import candidate_gaps
from al_transitions import workflow_gaps
import al_comparison
import al_review_history
import al_review_attempts as B
from al_render import render_line
from al_behavior_observations import behavior_gaps
import al_review_behavior as Behavior

NO_FINDINGS={'blocking':0,'nonblocking':0,'core':0,'tests':0,'both':0}

def review_gaps(st):
    r=st.get('review')
    if not r or r.get('stage')!='comparison-saved': return ['latest Architect fresh/comparison review incomplete']
    if r['candidate']['fingerprint']!=S.capture_candidate(st)['fingerprint']: return ['review candidate changed']
    try:
        Behavior.validate_review_lineage(st,r)
        fresh=A.read_json(r['fresh']); comparison=A.read_json(r['comparison'])
        if Behavior.enabled(st) and any(row['result']=='blocked' for row in fresh['counterexample_search']):return ['independent fresh counterexample assessment remains blocked']
        validate_report(st,fresh,'fresh'); validate_report(st,comparison,'compare')
        blocking,_=T.validate(st,comparison,T.obligations(prior_findings(st),fresh['findings'],fresh=True))
        al_assessments.validate(comparison,r['candidate'])
        if blocking: return ['latest review has blocking findings']
    except (OSError,ValueError,KeyError,SystemExit) as exc: return ['review artifact invalid: '+str(exc)]
    return []

@S.decision
def approval_gaps(st):
    return S.child_content_gaps(S.capture_candidate(st))+operation_gaps(st)+workflow_gaps(st)+L.gaps(st,'broad')+candidate_gaps(st)+behavior_gaps(st)+review_gaps(st)

def prior_findings(st):
    return al_review_history.findings(st)


def cmd_review_start(a,sid):
    st=al_store.load(sid); require_transition(st,'review-start','REVIEW')
    missing=S.child_content_gaps(S.capture_candidate(st))+workflow_gaps(st)+L.gaps(st,'broad')+candidate_gaps(st)+behavior_gaps(st)
    if st.get('gate_manifests'):
        # The one full inventory between planning and approval: the candidate's obligations must
        # still match the planned manifest.
        import al_gate_inventory as V
        missing+=V.gaps(st,A.read_json(st['gate_manifests'][-1]))
    if missing: refuse('review evidence incomplete: '+str(missing))
    st['review_iteration']+=1
    candidate=S.capture_candidate(st)
    candidate['changed_paths']=S.changed_paths(candidate['base'])
    st['review']={'id':'R'+str(st['review_iteration']),'stage':'fresh','candidate':candidate,'task_hash':al_task.task_hash(st),'packets':{},'correction_consumed':False}
    Behavior.initialize(st,st['review'])
    st['findings']=dict(NO_FINDINGS); st['review_status']='checking'
    al_store.set_phase(st,'REVIEW'); al_store.save(sid,st); print(render_line(st))

def cmd_packet(a,sid):
    st=al_store.load(sid); r=st.get('review')
    if not r or st['phase']!='REVIEW': refuse('review not started')
    if r['candidate']['fingerprint']!=S.capture_candidate(st)['fingerprint']: refuse('review candidate changed')
    if a.stage=='compare' and r['stage'] not in {'comparison','comparison-saved'}: refuse('comparison unavailable until independent fresh report saved and compare started')
    binding,new=B.issue(st,sid,a.stage,getattr(a,'replace_unavailable_attempt',False))
    if not new:
        ref=r['packets'][a.stage];raw=A.read(ref);data=json.loads(raw)
        if a.output:Path(a.output).write_bytes(raw)
        print(json.dumps({'packet':ref,'content':data},indent=2));return
    data={'attempt':binding,'schema_version':1,'review_id':r['id'],'stage':a.stage,'candidate_hash':r['candidate']['fingerprint'],'task_hash':r['task_hash'],
          'task_package':A.read_json(st['task_package']),'amendments':st['amendments'],'candidate':r['candidate'],
          'evidence':st['evidence'],'gates':st.get('gate_manifests',[]),'escapes':st.get('escapes',[])}
    if a.stage=='compare':
        data.update(fresh=A.read_json(r['fresh']),prior_findings=prior_findings(st),comparison_artifacts=st.get('comparison_artifacts',[]),approved_inputs=al_comparison.packet(st))
        data['comparison_contents'] = [{'artifact':ref,'content':A.read(ref).decode(errors='replace')} for ref in data['comparison_artifacts']]
    data=Behavior.packet(st,data)
    import al_review_rules
    data['repository_review_rules']=al_review_rules.capture(st,sid)
    raw=json.dumps(data,sort_keys=True).encode(); ref=A.snapshot_bytes(sid,raw,'review-packet-'+a.stage+('-v2' if Behavior.enabled(st) else ''),'runtime')
    r['packets'][a.stage]=ref; al_store.save(sid,st)
    if a.output: Path(a.output).write_bytes(raw)
    print(json.dumps({'packet':ref,'content':data},indent=2))

def validate_report(st,report,stage):
    r=st['review']; packet=r['packets'].get(stage)
    if not packet: refuse('review packet must be captured before report')
    A.read(packet)
    packet_value=A.read_json(packet)
    Behavior.validate_report(st,report,stage,packet_value)
    if report.get('schema_version')!=(2 if Behavior.enabled(st) else 1) or report.get('stage')!=stage or report.get('review_id')!=r['id'] or report.get('reviewer_role')!='al-architect': refuse('review report schema/identity/role mismatch')
    if report.get('candidate_hash')!=r['candidate']['fingerprint'] or report.get('task_hash')!=(packet_value['task_hash'] if Behavior.enabled(st) else r['task_hash']) or report.get('packet_hash')!=packet['sha256']: refuse('report hashes do not match packet')
    B.validate(st,report,stage,packet)
    if not report.get('invocation_id') or not report.get('model') or packet['sha256'] not in report.get('inspected_artifacts',[]): refuse('report requires invocation/model and inspected packet provenance')
    if stage=='compare':
        fresh=A.read_json(r['fresh'])
        if report['invocation_id']==fresh['invocation_id']: refuse('fresh/comparison require separate invocations')
        required=set(al_comparison.required(st))
        if not required.issubset(set(report.get('inspected_artifacts',[]))):refuse('comparison did not inspect required approved input hashes')
        if not isinstance(report.get('plan_defects'),list): refuse('comparison must explicitly assess plan defects')
    return T.validate(st,report,() if stage=='fresh' else T.obligations(prior_findings(st),A.read_json(r['fresh'])['findings'],fresh=True))

def cmd_report(a,sid):
    st=al_store.load(sid); r=st.get('review')
    stage='fresh' if a.cmd=='review-fresh' else 'compare'
    if not a.file: refuse('count-only review results cannot approve; provide structured --file')
    if not r or st['phase']!='REVIEW' or r['stage']!=('fresh' if stage=='fresh' else 'comparison'): refuse('report is not allowed at this review stage')
    if r['candidate']['fingerprint']!=S.capture_candidate(st)['fingerprint']: refuse('candidate changed during review')
    raw=Path(a.file).read_bytes()
    if Behavior.enabled(st):
        import al_proof_json as J
        data=J.loads(raw)
    else:data=json.loads(raw)
    blocking,notes=validate_report(st,data,stage)
    binding=B.validate(st,data,stage,r['packets'][stage])
    if binding['model'] in st.get('unavailable_models',[]):refuse('issued attempt unavailable; replace the unsaved stage explicitly')
    if stage=='compare': al_assessments.validate(data,r['candidate'])
    ref=A.snapshot_bytes(sid,raw,'architect-'+stage+('-v2' if Behavior.enabled(st) else ''),a.file)
    if Behavior.enabled(st):Behavior.retain_lineage(st,sid,ref,stage)
    r['fresh' if stage=='fresh' else 'comparison']=ref; r['stage']='fresh-saved' if stage=='fresh' else 'comparison-saved'
    if stage=='compare':
        st['review_status']=data['status']; st['findings']=dict(NO_FINDINGS,blocking=blocking,nonblocking=notes)
        st['review_history'].append({'review':st['review_iteration'],'status':data['status'],'blocking':blocking,'nonblocking':notes,'fresh':r['fresh'],'comparison':ref,'resolution_validation':al_review_history.receipt(st,sid,r['fresh'],ref),'at':time.time()})
    al_store.save(sid,st)

def cmd_compare(a,sid):
    st=al_store.load(sid); r=st.get('review')
    if not r or r['stage']!='fresh-saved': refuse('save independent fresh review first')
    if r['candidate']['fingerprint']!=S.capture_candidate(st)['fingerprint']: refuse('candidate changed')
    A.read(r['fresh'])
    Behavior.validate_review_lineage(st,r,A.read_json(r['packets']['fresh']))
    inputs=al_comparison.capture(st,sid,a)
    supplemental=[A.snapshot_artifact(sid,Path(f),'comparison-input') for f in (a.artifact or [])]
    r['comparison_inputs']=inputs; r['stage']='comparison'; st['comparison_artifacts']=supplemental
    al_store.save(sid,st)

def cmd_correction(a,sid):
    st=al_store.load(sid); require_transition(st,'correction','CORRECTION'); r=st.get('review')
    if not r or r['stage']!='comparison-saved' or r['correction_consumed'] or st['findings']['blocking']<=0: refuse('one correction requires a completed blocking review')
    if st['correction_iteration']>=st['max_correction_iterations']: refuse('correction budget exhausted; record stopping state')
    r['correction_consumed']=True; st['correction_iteration']+=1
    route=(a.route or 'al-engineer').split(',')
    if any(x not in {'al-engineer','al-builder'} for x in route): refuse('invalid correction route')
    al_store.set_phase(st,'CORRECTION',route,a.mode); al_store.save(sid,st)

def cmd_notes(a,sid):
    st=al_store.load(sid)
    if st.get('notes_triage'): refuse('notes triage already used')
    if review_gaps(st): refuse('notes require completed zero-blocker review')
    data=json.loads(Path(a.file).read_bytes()); notes=A.read_json(st['review']['comparison'])['findings']
    ids={f['id'] for f in notes if f['severity']=='NON_BLOCKING'}
    if not isinstance(data,list) or {x.get('id') for x in data}!=ids: refuse('triage must cover every note')
    for x in data:
        if x.get('action') not in {'fix','defer','dispute'} or not x.get('rationale'): refuse('triage requires action/rationale')
        if x['action']=='fix' and (not x.get('concrete_defect') or not x.get('criterion') or x['criterion'] not in A.read_json(st['task_package'])['criteria']): refuse('notes fix requires concrete in-scope defect')
    st['notes_triage']=A.snapshot_artifact(sid,Path(a.file),'notes-triage')
    if any(x['action']=='fix' for x in data): al_store.set_phase(st,'CORRECTION',['al-engineer']); st['review_status']=None
    al_store.save(sid,st)

def cmd_oscillation(a,sid):
    st=al_store.load(sid); st['oscillations'].append({'detail':a.detail,'at':time.time()}); al_store.save(sid,st)

def register(sub):
    sub.add_parser('review-start').set_defaults(fn=cmd_review_start)
    p=sub.add_parser('review-packet'); p.add_argument('--stage',choices=['fresh','compare'],required=True); p.add_argument('--output'); p.add_argument('--replace-unavailable-attempt',action='store_true'); p.add_argument('--recovery-file',dest='operation_recovery_file'); p.set_defaults(fn=cmd_packet)
    p=sub.add_parser('review-compare-start'); p.add_argument('--plan'); p.add_argument('--pseudocode'); p.add_argument('--approval-source'); p.add_argument('--artifact',action='append'); p.set_defaults(fn=cmd_compare)
    for name in ('review-fresh','review-result'):
        p=sub.add_parser(name); p.add_argument('--file'); p.add_argument('result',nargs='?',choices=['APPROVED','CHANGES_REQUIRED'])
        for key in ('blocking','nonblocking','core','tests','both'): p.add_argument('--'+key,type=int,default=0)
        p.set_defaults(fn=cmd_report)
    p=sub.add_parser('correction'); p.add_argument('--route'); p.add_argument('--mode',choices=['parallel','sequential']); p.add_argument('--labels'); p.add_argument('--reason'); p.set_defaults(fn=cmd_correction)
    p=sub.add_parser('notes-triage'); p.add_argument('--file',required=True); p.set_defaults(fn=cmd_notes)
    p=sub.add_parser('oscillation'); p.add_argument('--detail',required=True); p.set_defaults(fn=cmd_oscillation)

cmd_review_result=cmd_report
