"""Base/candidate gate manifests and nonwaivable isolated executions."""
import hashlib
import contextlib
import uuid
import json
import subprocess
import time
from pathlib import Path
import al_artifacts as A
import al_checkouts
import al_gate_reuse as R
import al_gate_continuation as Continuation
from al_gate_process import Owner,stop
import al_evidence as E
import al_gate_discovery as D
import al_gate_inventory as V
import al_gitlinks as G
import al_ledger as L
import al_scope as S
import al_review_state as ReviewState
import al_baseline_exceptions as Exceptions
import al_store
import al_sources as Sources
from al_schema import refuse

class CandidateChanged(al_checkouts.MaterializationError):pass

_VALIDATED=set()


def stale_sources(m):
    """Sourced rules and gate sources whose current bytes differ from the plan; checked at every launch."""
    out=[]
    for source in m['rules']+[g['source'] for g in m['gates'] if g.get('source')]:
        p=al_store.REPO_ROOT/S.safe_path(source['path'])
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=source['sha256']:out.append(source['path'])
    return sorted(set(out))


@S.decision
def manifest(st):
    refs=st.get('gate_manifests',[])
    if not refs: return None
    # Full inventory validation runs at gates plan and review start, not on every read; the
    # structural validation of one exact manifest is done once per process.
    m=A.read_json(refs[-1]);key=(refs[-1]['sha256'],st['identity']['merge_base'])
    if key not in _VALIDATED:D.validate_manifest(st,m,check_sources=False);_VALIDATED.add(key)
    return m

@S.decision
def gate_gaps(st,stage):
    m=manifest(st)
    if not m: return ['repository gate manifest missing']
    out=[]
    fp=S.capture_candidate(st)['fingerprint'] if stage=='candidate' else m['base_oid']
    for g in m['gates']:
        if g['applicability']=='NOT_APPLICABLE': continue
        if g['applicability']=='UNRESOLVED': continue  # unverified: reported by unverified(), never blocking
        runs=[r for r in st.get('gate_runs',[]) if r['gate']==g['id'] and r['stage']==stage and r['manifest']==st['gate_manifests'][-1]['sha256']]
        r=runs[-1] if runs else None
        e=L.find(st,r['evidence']) if r else None
        valid=e and e['status']=='PASS' and e.get('log') and Path(e['log']).is_file() and L.file_sha(e['log'])==e.get('log_sha256') and r['fingerprint']==fp
        environment_current=bool(e and e.get('env',[])==sorted(g['env_names']) and r.get('env_fingerprint')==L.scope_fingerprint([],g['env_names']))
        valid=valid and environment_current and e.get('sources',[])==g.get('sources',[]) and r.get('sources',[])==g.get('sources',[])
        if valid and 'reuse' in e:valid=not R.reuse_gaps(st,e)
        if valid and 'execution_association' in e:
            import al_execution_receipts as Receipts
            valid=not Receipts.validate_association(st,e)
        if valid:valid=not ReviewState.gaps(st,g,e,fp)
        if valid and stage=='candidate':
            valid=(e.get('kind')=='check' and e.get('gate_stage')=='candidate' and e.get('gate_manifest')==r['manifest']
                   and e['argv']==g['argv'] and e['cwd']==g['cwd'] and e['scope']==g['scope']
                   and r.get('execution_kind')==g['execution_kind'] and e['area']=='gate-candidate-'+g['id']
                   and L.entry_verdict(st,e)[0]=='VALID')
        if not valid:
            decision=st.get('gate_decisions',{}).get(g['id'])
            if stage=='base' and environment_current and e and e['status']=='FAIL' and e.get('log') and Path(e['log']).is_file() and L.file_sha(e['log'])==e.get('log_sha256') and decision and decision['action']=='fix' and decision.get('manifest')==st['gate_manifests'][-1]['sha256'] and decision.get('base_oid')==m['base_oid'] and decision.get('evidence')==e['id']: continue
            # An authorized exception accepts only this exact unchanged-base failure; the evidence stays FAIL.
            if stage=='base' and e and e['status']=='FAIL' and Exceptions.current(st,m,g):continue
            out.append(stage+' gate '+g['id']+' requires passing evidence')
    return out

def unverified(st):
    """Checks AL could not model or run locally: reported to the reviewer and the user, never blocking."""
    m=manifest(st)
    if not m:return []
    return [{'gate':g['id'],'category':g['category'],'reason':g.get('reason',''),
             'verified_by':'pipeline' if g['category']=='ci' else 'not verified'}
            for g in m['gates'] if g['applicability']=='UNRESOLVED']

def base_gaps(st): return gate_gaps(st,'base')


@S.decision
def status(st):
    """Every status section within one worktree observation instead of one per section."""
    from al_recovery_guidance import descriptors
    return {'base':base_gaps(st),'candidate':candidate_gaps(st),'recovery':descriptors(st),
            'baseline_exceptions':Exceptions.view(st,manifest(st)),'unverified':unverified(st)}
@S.decision
def candidate_gaps(st):
    m=manifest(st)
    if m:
        from al_task_attribution import classify_changes
        changed=classify_changes(st)['discovery_paths'] if st.get('review_mode') != 'whole-mr' else S.changed_paths(st['identity']['merge_base'])
        opaque={row['path'] for row in S.source_rows() if G.opaque(row)}
        for raw in changed:
            p=raw; units=m.get('units',[])
            unit=V.classify_unit(p,opaque,m.get('service_coverage',{}).get('semantic_requirements'))
            if unit not in units: return ['new touched unit requires extended base/candidate gates: '+unit]
    return gate_gaps(st,'candidate')

def cmd_gates(a,sid):
    st=al_store.load(sid)
    if a.action in {'discover','plan'}:
        from al_gate_recovery import planning
        if a.action=='discover':
            paths=json.loads(Path(a.paths_file).read_text())
            if a.base and S.git_bytes('rev-parse',a.base).decode().strip()!=st['identity']['merge_base']: refuse('base differs from pinned merge base')
            m=D.discover(st,paths)
        else: m=json.loads(Path(a.file).read_bytes())
        D.validate_manifest(st,m)
        planning(st,sid,m.get('paths',[]))
        m.setdefault('id','G'+str(len(st.get('gate_manifests',[]))+1))
        m['revision']=len(st.get('gate_manifests',[]))+1
        st.setdefault('gate_manifests',[]).append(A.snapshot_bytes(sid,json.dumps(m,sort_keys=True).encode(),'gate-manifest',a.file if a.action=='plan' else a.paths_file))
        if st.get('gate_recovery'):st['gate_recovery']['replacement_manifest']=st['gate_manifests'][-1]['sha256']
        al_store.save(sid,st); print(json.dumps(m,indent=2)); return
    if a.action=='decision':
        m=manifest(st)
        if not m or a.id not in [g['id'] for g in m['gates']]: refuse('unknown gate ID')
        if not a.reason or not a.source: refuse('decision requires explicit user source and reason')
        if a.decision=='exception':
            row=Exceptions.record(st,sid,m,next(g for g in m['gates'] if g['id']==a.id),a.source,a.reason)
            al_store.save(sid,st);print(json.dumps({'baseline_exception':row,'evidence_status':'FAIL'},indent=2));return
        runs=[r for r in st.get('gate_runs',[]) if r['gate']==a.id and r['stage']=='base' and r['manifest']==st['gate_manifests'][-1]['sha256']]
        failed=L.find(st,runs[-1]['evidence']) if runs else None
        gate=next(g for g in m['gates'] if g['id']==a.id)
        if runs and runs[-1].get('env_fingerprint')!=L.scope_fingerprint([],gate['env_names']):refuse('baseline decision environment changed')
        if not failed or failed['status']!='FAIL' or not failed.get('log') or not Path(failed['log']).is_file() or L.file_sha(failed['log'])!=failed.get('log_sha256'):refuse('baseline decision requires current manifest failed base evidence')
        st.setdefault('gate_decisions',{})[a.id]={'action':a.decision,'source':a.source,'reason':a.reason,'at':time.time(),
                                               'manifest':st['gate_manifests'][-1]['sha256'],'base_oid':m['base_oid'],'evidence':failed['id']}
        if a.decision in {'stop','defer'}: st.update(status='STOPPED_WITH_BLOCKERS',stop_reason='user '+a.decision+' for gate '+a.id)
        al_store.save(sid,st); return
    if a.action=='status':
        print(json.dumps(status(st),indent=2)); return
    refuse('gate run must use unlocked dispatcher')

def cmd_run(a,sid):
    with al_store.locked(sid):
        st=al_store.load(sid); m=manifest(st)
        if not m: refuse('gate manifest missing')
        resume = getattr(a,'resume',False)
        retry_failed = getattr(a,'retry_failed',False)
        if retry_failed and not resume: refuse('--retry-failed requires --resume')
        selected = Continuation.select_continuation(st,m,a.stage,a.id,retry_failed) if resume else [g for g in m['gates'] if not a.id or g['id']==a.id]
        if not selected and not resume: refuse('unknown gate ID')
        if a.id and selected and all(g['applicability']=='UNRESOLVED' for g in selected): refuse('gate '+a.id+' is unverified: AL cannot execute it locally')
        selected=[g for g in selected if g['applicability']!='UNRESOLVED']
        ref=st['gate_manifests'][-1]['sha256']
    invocation=uuid.uuid4().hex;checkout=None;pinned=None
    selected_ids=[g['id'] for g in selected];results=[]
    def report():
        current=al_store.load(sid)
        value=Continuation.stage_report(current,a.stage,results,selected_ids)
        print(json.dumps(value,sort_keys=True))
        return value['stage_ready']
    with contextlib.ExitStack() as stack:
        for g in selected:
            if g['applicability']=='NOT_APPLICABLE': continue
            env_fingerprint=L.scope_fingerprint([],g['env_names'])
            context={'stage':a.stage,'manifest':ref,'gate':g['id']}
            if resume:context.update(resume=True,explicit=bool(a.id),retry_failed=retry_failed)
            try:
                entry=E._create(sid,st['run_id'],area='gate-'+a.stage+'-'+g['id'],kind='check',tier='focused',argv=g['argv'],cwd=g['cwd'],scope=g['scope'],env_names=g['env_names'],sources=g.get('sources',[]),gate_context=context)
            except Continuation.DestinationSettled:
                continue
            log=L.log_dir(sid)/(entry['id']+'-gate.log')
            status='INFRA'; code=127; fp=None
            note='isolated '+a.stage+' gate';dirty=None
            preparation=projection=receipt=None
            entry['invocation']=invocation
            with Owner(log) as owner:
                try:
                    current=al_store.load(sid)
                    if current['run_id']!=st['run_id'] or current['gate_manifests'][-1]['sha256']!=ref:
                        raise al_checkouts.MaterializationError('run or manifest changed before execution')
                    if pinned is not None and S.capture_candidate(st)['fingerprint']!=pinned:
                        raise CandidateChanged('source candidate changed between gates')
                    if checkout is None:
                        checkout=stack.enter_context(al_checkouts.materialize(st,a.stage,base=m['base_oid']))
                        pinned=checkout['capture']['fingerprint'] if pinned is None else pinned
                        if checkout['capture']['fingerprint']!=pinned:
                            raise CandidateChanged('source candidate changed during rebuild')
                        initial=R.scratch_state(checkout['root'])
                    current=al_store.load(sid)
                    if current['run_id']!=st['run_id'] or current['gate_manifests'][-1]['sha256']!=ref or manifest(current)!=m:
                        raise al_checkouts.MaterializationError('run or manifest changed before launch')
                    stale=stale_sources(m)
                    if stale:raise al_checkouts.MaterializationError('source rule changed before launch: '+', '.join(stale))
                    root=checkout['root']
                    before_state=R.scratch_state(root,initial)
                    if R.drift(initial,before_state,root):raise al_checkouts.MaterializationError('scratch changed between gates')
                    gaps=L.check_scope(g['scope'],g.get('sources',[]),g['cwd'],require_matches=False)
                    gaps+=Sources.capability_gaps(g['scope'],g.get('sources',[]),S.source_rows(root),g['cwd'],root=root)
                    if gaps:raise al_checkouts.MaterializationError('; '.join(gaps))
                    if env_fingerprint!=L.scope_fingerprint([],g['env_names']):raise CandidateChanged('declared environment changed before execution')
                    cwd=al_checkouts.gate_cwd(root,g['cwd'])
                    fp=checkout['capture']['fingerprint'] if a.stage=='candidate' else m['base_oid']
                    key=R.execution_key(st,m,ref,g,a.stage,checkout['capture'],env_fingerprint,a.timeout)
                    entry['execution_key']=key
                    # Declared review-hook state is produced fresh by each execution, never by a receipt.
                    if 'task_attribution' in current and g['execution_kind']=='local' and not g.get('review_state'):
                        import al_execution_receipts as Receipts
                        import al_verification_prepare as Preparation
                        try:
                            preparation=Preparation.prepare(current,gate=g['id'],stage=a.stage)
                            projection=Receipts.project_execution(current,preparation['spec'],checkout,preparation,a.timeout)
                        except ValueError:
                            preparation=projection=None
                        if projection is not None:
                            entry['execution_projection_digest']=Receipts.execution_identity(projection)
                            E._commit(sid,entry)
                            receipt=Receipts.reuse_candidate(al_store.load(sid),projection,exclude_reservation=entry['reservation'])
                    # Every new run captures task attribution, so reuse is only through
                    # verified execution receipts; legacy key-cache reuse no longer exists.
                    if receipt:
                        log.write_text('Associated immutable execution receipt '+json.dumps(receipt,sort_keys=True)+'\n')
                        status='PASS';code=0
                    elif g['execution_kind']=='remote':
                        from al_remote_gates import execute_remote
                        status,code,provenance=execute_remote(st,g,a.stage,log,a.timeout,sid,entry,owner)
                        entry['remote_provenance']=provenance
                    else:
                        state_top=ReviewState.prepare(root,g['review_state']) if g.get('review_state') else None
                        try:
                            with log.open('wb') as out:
                                out.write(('argv: '+json.dumps(g['argv'])+'\ncwd: '+str(cwd)+'\nexecution_sha256: '+R.key_digest(key)+'\ninvocation: '+invocation+'\n').encode()); out.flush()
                                proc=owner.spawn(g['argv'],cwd=cwd,stdout=out,stderr=subprocess.STDOUT)
                                entry['pid']=proc.pid; E._commit(sid,entry)
                                try: code=proc.wait(timeout=a.timeout)
                                except subprocess.TimeoutExpired: code=stop(proc); status='INFRA'
                                else:
                                    status='PASS' if code==0 else ('INFRA' if code in {126,127} else 'FAIL')
                                    stop(proc)  # Reap surviving group members before inspecting reusable state.
                        except OSError as exc: log.write_text(str(exc)); status='INFRA'
                        if state_top is not None:
                            # The declared location is removed even when its capture is refused.
                            try:captured=ReviewState.capture(root,g['review_state'])
                            finally:ReviewState.remove(state_top)
                            entry['review_state']=ReviewState.seal(st,sid,g,a.stage,fp,invocation,captured,entry,ref)
                    dirty=R.drift(before_state,R.scratch_state(root,before_state,post_execution=True),root)
                    if dirty=='FAIL' and status=='PASS':
                        status='FAIL';log.write_bytes(log.read_bytes()+b'\nGenerated/checkout metadata drift detected\n')
                    if S.capture_candidate(st)['fingerprint']!=checkout['capture']['fingerprint']:
                        status='TORN';note='source candidate changed during gate execution'
                except CandidateChanged as exc:
                    status='TORN';code=126;note=str(exc);log.write_text(str(exc)+'\n')
                except al_checkouts.MaterializationError as exc:
                    status='INFRA';code=126;fp=None;note='rejected '+a.stage+' materialization'
                    log.write_text('Disposable materialization rejected: '+str(exc)+'\n')
                except (L.FingerprintError, Sources.SourceError, SystemExit) as exc:
                    status='INFRA';code=126;fp=None;note='source capture/capability unavailable'
                    log.write_text(str(exc)+'\n')
                except KeyboardInterrupt:
                    owner.cancel()
                    if owner.proc is not None:entry['pid']=owner.proc.pid
                entry['fp_after']=E._fingerprint(entry)
                if entry.get('fingerprint_error') and status=='PASS':status='INFRA'
                if entry['fp_after']!=entry['fp_before'] and status=='PASS':status='TORN'
                if env_fingerprint!=L.scope_fingerprint([],g['env_names']):status='TORN'
                def persist(reason):
                    if reason:
                        entry['interrupted']=True
                        result_status,result_code,result_note='INFRA',130,'gate cancelled: '+reason
                    else:result_status,result_code,result_note=status,code,note
                    entry.update(status=result_status,exit=result_code,ended_at=time.time(),log=str(log),log_sha256=L.file_sha(log),note=result_note,gate_stage=a.stage,gate_manifest=ref)
                    current=al_store.load(sid)
                    previous=next((e for e in current['evidence']['entries'] if e['id']==entry['id']),None)
                    if entry['status']=='PASS' and current['gate_manifests'][-1]['sha256']!=ref:entry.update(status='INFRA',exit=126,note='gate manifest changed before finalization')
                    if current['run_id']!=entry['run'] or not previous or previous.get('reservation')!=entry.get('reservation'):refuse('gate reservation changed before finalization')
                    if entry['status']=='PASS':
                        try:A.read(current['gate_manifests'][-1])
                        except (OSError,ValueError,SystemExit):entry.update(status='INFRA',exit=126,note='gate manifest artifact unavailable at finalization')
                    if entry['status']=='PASS' and projection is not None:
                        try:
                            fresh=Preparation.prepare(current,gate=g['id'],stage=a.stage)
                            if not Preparation.preparation_equivalent(current,preparation,fresh):raise ValueError('execution preparation changed before finalization')
                            if receipt:
                                associated_projection=Receipts.Versions.enable(current,projection) if projection['schema_version']==2 else projection
                                entry['execution_association']=Receipts.associate(current,fresh['destination'],receipt,entry['reservation'],
                                                                                {'projection':associated_projection,'preparation':fresh})
                            elif dirty is None:
                                completion=owner.completion()
                                if completion['complete']:
                                    entry['execution_completion']=completion
                                    entry['execution_receipt']=Receipts.seal_execution(current,entry,projection,completion)
                        except ValueError as exc:
                            entry.update(status='INFRA',exit=126,note=str(exc))
                    current['evidence']['entries']=[entry if e['id']==entry['id'] else e for e in current['evidence']['entries']]
                    runs=current.setdefault('gate_runs',[])
                    runs[:]=[r for r in runs if r['evidence']!=entry['id']]
                    runs.append({'gate':g['id'],'stage':a.stage,'manifest':ref,'fingerprint':fp,'evidence':entry['id'],'execution_kind':g['execution_kind'],'env_fingerprint':env_fingerprint,'sources':g.get('sources',[])})
                    if entry.get('reuse') and entry['status']=='PASS' and R.reuse_gaps(current,entry):
                        entry.update(status='INFRA',exit=126,note='reuse provenance invalid at finalization')
                    if entry['status']=='PASS' and S.capture_candidate(st)['fingerprint']!=pinned:
                        entry.update(status='TORN',note='source candidate changed before finalization')
                    if entry['status']=='PASS' and env_fingerprint!=L.scope_fingerprint([],g['env_names']):
                        entry.update(status='TORN',note='environment changed before finalization')
                    al_store.save(sid,current)
                with al_store.locked(sid):
                    owner.finish(persist)
            status=entry['status']
            if dirty=='REBUILD':
                stack.close();checkout=None
            print(L.row(entry,status))
            results.append({'gate':g['id'],'evidence':entry['id'],'status':status})
            if status!='PASS':
                if resume:report()
                raise SystemExit(1)
    if resume and not report():raise SystemExit(1)

def register(sub):
    children=sub.add_parser('gates').add_subparsers(dest='action',required=True)
    p=children.add_parser('discover'); p.add_argument('--paths-file',required=True); p.add_argument('--base'); p.set_defaults(fn=cmd_gates)
    p=children.add_parser('plan'); p.add_argument('--file',required=True); p.set_defaults(fn=cmd_gates)
    p=children.add_parser('run'); p.add_argument('--stage',choices=['base','candidate'],required=True); p.add_argument('--id'); p.add_argument('--resume',action='store_true'); p.add_argument('--retry-failed',action='store_true'); p.add_argument('--timeout',type=float,default=300); p.add_argument('--recovery-file',dest='operation_recovery_file'); p.set_defaults(fn=cmd_run)
    p=children.add_parser('decision'); p.add_argument('--id',required=True); p.add_argument('--action',dest='decision',choices=['fix','stop','defer','exception'],required=True); p.add_argument('--source',required=True); p.add_argument('--reason',required=True); p.set_defaults(fn=cmd_gates)
    children.add_parser('status').set_defaults(fn=cmd_gates)
