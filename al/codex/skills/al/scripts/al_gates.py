"""Base/candidate gate manifests and nonwaivable isolated executions."""
import hashlib
import json
import subprocess
import time
from pathlib import Path
import al_artifacts as A
import al_checkouts
from al_gate_process import Owner,stop
import al_evidence as E
import al_gate_discovery as D
import al_gate_inventory as V
import al_gitlinks as G
import al_ledger as L
import al_scope as S
import al_store
import al_sources as Sources
from al_schema import refuse

def manifest(st):
    refs=st.get('gate_manifests',[])
    if not refs: return None
    m=A.read_json(refs[-1]); D.validate_manifest(st,m); return m

def gate_gaps(st,stage):
    m=manifest(st)
    if not m: return ['repository gate manifest missing']
    out=[]
    fp=S.capture_candidate(st)['fingerprint'] if stage=='candidate' else m['base_oid']
    for g in m['gates']:
        if g['applicability']=='NOT_APPLICABLE': continue
        if g['applicability']=='UNRESOLVED': out.append('unresolved gate '+g['id']); continue
        runs=[r for r in st.get('gate_runs',[]) if r['gate']==g['id'] and r['stage']==stage and r['manifest']==st['gate_manifests'][-1]['sha256']]
        r=runs[-1] if runs else None
        e=L.find(st,r['evidence']) if r else None
        valid=e and e['status']=='PASS' and e.get('log') and Path(e['log']).is_file() and L.file_sha(e['log'])==e.get('log_sha256') and r['fingerprint']==fp
        environment_current=bool(e and e.get('env',[])==sorted(g['env_names']) and r.get('env_fingerprint')==L.scope_fingerprint([],g['env_names']))
        valid=valid and environment_current and e.get('sources',[])==g.get('sources',[]) and r.get('sources',[])==g.get('sources',[])
        if valid and stage=='candidate':
            valid=(e.get('kind')=='check' and e.get('gate_stage')=='candidate' and e.get('gate_manifest')==r['manifest']
                   and e['argv']==g['argv'] and e['cwd']==g['cwd'] and e['scope']==g['scope']
                   and r.get('execution_kind')==g['execution_kind'] and e['area']=='gate-candidate-'+g['id']
                   and L.entry_verdict(st,e)[0]=='VALID')
        if not valid:
            decision=st.get('gate_decisions',{}).get(g['id'])
            if stage=='base' and environment_current and e and e['status']=='FAIL' and e.get('log') and Path(e['log']).is_file() and L.file_sha(e['log'])==e.get('log_sha256') and decision and decision['action']=='fix' and decision.get('manifest')==st['gate_manifests'][-1]['sha256'] and decision.get('base_oid')==m['base_oid'] and decision.get('evidence')==e['id']: continue
            out.append(stage+' gate '+g['id']+' requires passing evidence')
    return out

def base_gaps(st): return gate_gaps(st,'base')
def candidate_gaps(st):
    m=manifest(st)
    if m:
        changed=S.changed_paths(st['identity']['merge_base'])
        opaque={row['path'] for row in S.source_rows() if G.opaque(row)}
        for raw in changed:
            p=raw; units=m.get('units',[])
            unit=V.classify_unit(p,opaque)
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
        runs=[r for r in st.get('gate_runs',[]) if r['gate']==a.id and r['stage']=='base' and r['manifest']==st['gate_manifests'][-1]['sha256']]
        failed=L.find(st,runs[-1]['evidence']) if runs else None
        gate=next(g for g in m['gates'] if g['id']==a.id)
        if runs and runs[-1].get('env_fingerprint')!=L.scope_fingerprint([],gate['env_names']):refuse('baseline decision environment changed')
        if not failed or failed['status']!='FAIL' or not failed.get('log') or not Path(failed['log']).is_file() or L.file_sha(failed['log'])!=failed.get('log_sha256'):refuse('baseline decision requires current manifest failed base evidence')
        st.setdefault('gate_decisions',{})[a.id]={'action':a.decision,'source':a.source,'reason':a.reason,'at':time.time(),
                                               'manifest':st['gate_manifests'][-1]['sha256'],'base_oid':m['base_oid'],'evidence':failed['id']}
        if a.decision in {'stop','defer'}: st.update(status='STOPPED_WITH_BLOCKERS',stop_reason='user '+a.decision+' for gate '+a.id)
        al_store.save(sid,st); return
    if a.action=='status': print(json.dumps({'base':base_gaps(st),'candidate':candidate_gaps(st)},indent=2)); return
    refuse('gate run must use unlocked dispatcher')

def cmd_run(a,sid):
    with al_store.locked(sid):
        st=al_store.load(sid); m=manifest(st)
        if not m: refuse('gate manifest missing')
        selected=[g for g in m['gates'] if not a.id or g['id']==a.id]
        if not selected: refuse('unknown gate ID')
        if any(g['applicability']=='UNRESOLVED' for g in selected): refuse('resolve gate semantics before execution')
        ref=st['gate_manifests'][-1]['sha256']
    for g in selected:
        if g['applicability']=='NOT_APPLICABLE': continue
        env_fingerprint=L.scope_fingerprint([],g['env_names'])
        entry=E._create(sid,st['run_id'],area='gate-'+a.stage+'-'+g['id'],kind='check',tier='focused',argv=g['argv'],cwd=g['cwd'],scope=g['scope'],env_names=g['env_names'],sources=g.get('sources',[]))
        log=L.log_dir(sid)/(entry['id']+'-gate.log')
        status='INFRA'; code=127; fp=None
        note='isolated '+a.stage+' gate'
        with Owner(log) as owner:
            try:
                with al_checkouts.materialize(st,a.stage,base=m['base_oid']) as checkout:
                    root=checkout['root']
                    gaps=L.check_scope(g['scope'],g.get('sources',[]),g['cwd'],require_matches=False)
                    gaps+=Sources.capability_gaps(g['scope'],g.get('sources',[]),S.source_rows(root),g['cwd'],root=root)
                    if gaps:raise al_checkouts.MaterializationError('; '.join(gaps))
                    cwd=al_checkouts.gate_cwd(root,g['cwd'])
                    fp=checkout['capture']['fingerprint'] if a.stage=='candidate' else m['base_oid']
                    if g['execution_kind']=='remote':
                        from al_remote_gates import execute_remote
                        status,code,provenance=execute_remote(st,g,a.stage,log,a.timeout,sid,entry,owner)
                        entry['remote_provenance']=provenance
                    else:
                        before_index=subprocess.run(['git','ls-files','--stage','-z'],cwd=root,capture_output=True,check=True).stdout
                        before=subprocess.run(['git','diff','--ignore-submodules=none','--binary','HEAD'],cwd=root,capture_output=True,check=True).stdout
                        try:
                            with log.open('wb') as out:
                                out.write(('argv: '+json.dumps(g['argv'])+'\ncwd: '+str(cwd)+'\n').encode()); out.flush()
                                proc=owner.spawn(g['argv'],cwd=cwd,stdout=out,stderr=subprocess.STDOUT)
                                entry['pid']=proc.pid; E._commit(sid,entry)
                                try: code=proc.wait(timeout=a.timeout)
                                except subprocess.TimeoutExpired: code=stop(proc); status='INFRA'
                                else: status='PASS' if code==0 else ('INFRA' if code in {126,127} else 'FAIL')
                        except OSError as exc: log.write_text(str(exc)); status='INFRA'
                        after=subprocess.run(['git','diff','--ignore-submodules=none','--binary','HEAD'],cwd=root,capture_output=True,check=True).stdout
                        untracked=subprocess.run(['git','ls-files','--others','--exclude-standard'],cwd=root,capture_output=True,check=True).stdout
                        after_index=subprocess.run(['git','ls-files','--stage','-z'],cwd=root,capture_output=True,check=True).stdout
                        if status=='PASS' and (after!=before or after_index!=before_index or untracked): status='FAIL'; log.write_bytes(log.read_bytes()+b'\nGenerated/checkout drift detected\n')
                    if S.capture_candidate(st)['fingerprint']!=checkout['capture']['fingerprint']:
                        status='TORN';note='source candidate changed during gate execution'
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
                if current['run_id']!=entry['run'] or not previous or previous.get('reservation')!=entry.get('reservation'):refuse('gate reservation changed before finalization')
                current['evidence']['entries']=[entry if e['id']==entry['id'] else e for e in current['evidence']['entries']]
                runs=current.setdefault('gate_runs',[])
                runs[:]=[r for r in runs if r['evidence']!=entry['id']]
                runs.append({'gate':g['id'],'stage':a.stage,'manifest':ref,'fingerprint':fp,'evidence':entry['id'],'execution_kind':g['execution_kind'],'env_fingerprint':env_fingerprint,'sources':g.get('sources',[])})
                al_store.save(sid,current)
            with al_store.locked(sid):
                owner.finish(persist)
        print(L.row(entry,status))
        if status!='PASS': raise SystemExit(1)

def register(sub):
    children=sub.add_parser('gates').add_subparsers(dest='action',required=True)
    p=children.add_parser('discover'); p.add_argument('--paths-file',required=True); p.add_argument('--base'); p.set_defaults(fn=cmd_gates)
    p=children.add_parser('plan'); p.add_argument('--file',required=True); p.set_defaults(fn=cmd_gates)
    p=children.add_parser('run'); p.add_argument('--stage',choices=['base','candidate'],required=True); p.add_argument('--id'); p.add_argument('--timeout',type=float,default=300); p.set_defaults(fn=cmd_run)
    p=children.add_parser('decision'); p.add_argument('--id',required=True); p.add_argument('--action',dest='decision',choices=['fix','stop','defer'],required=True); p.add_argument('--source',required=True); p.add_argument('--reason',required=True); p.set_defaults(fn=cmd_gates)
    children.add_parser('status').set_defaults(fn=cmd_gates)
