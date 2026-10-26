"""Authorized parent worktree isolation through the shared typed overlay."""
import json
import subprocess
import time
from pathlib import Path
import al_checkouts as C
import al_evidence as E
import al_ledger as L
import al_scope as S
import al_sources as Sources
import al_store
from al_schema import refuse


def cmd_isolate(a,sid):
    st=al_store.load(sid)
    if st['phase']!='PLANNING' or not a.authorized:refuse('worktree creation requires planning and explicit --authorized')
    destination=Path(a.path).expanduser().resolve()
    if destination.exists() or destination==al_store.REPO_ROOT or al_store.REPO_ROOT in destination.parents:refuse('isolation needs a new path outside the candidate tree')
    capture=S.capture_candidate(st)
    gaps=S.child_content_gaps(capture)
    if gaps:refuse('cannot isolate uncaptured child work: '+'; '.join(gaps))
    sources=[{'path':path,'capability':'parent-entry'} for path in Sources.boundaries(capture['files'])]
    entry=E._create(sid,st['run_id'],area='isolation',kind='preflight',tier='preflight',argv=['git','worktree','add','--detach',str(destination),st['baseline_head']],cwd='.',scope=['.'],env_names=[],sources=sources)
    log=L.log_dir(sid)/(entry['id']+'-isolation.log')
    with log.open('wb') as out:
        import al_harness_process
        proc=al_harness_process.popen(entry['argv'],cwd=al_store.REPO_ROOT,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
        entry['pid']=proc.pid;E._commit(sid,entry)
        try:code=proc.wait(timeout=a.timeout)
        except subprocess.TimeoutExpired:code=E._stop(proc)
    status='PASS' if code==0 else 'FAIL'
    if code==0:
        try:
            C.overlay_candidate(destination,capture,stage_files=False)
            if S.capture_candidate(st)['fingerprint']!=capture['fingerprint']:raise C.MaterializationError('candidate changed while isolating')
        except (C.MaterializationError, OSError, SystemExit) as exc:
            status='INFRA';code=126
            with log.open('a') as out:out.write('\nIsolation rejected: '+str(exc)+'\n')
    after=E._fingerprint(entry)
    if status=='PASS' and entry['fp_before']!=after:status='TORN'
    entry.update(status=status,exit=code,ended_at=time.time(),log=str(log),log_sha256=L.file_sha(log),fp_after=after)
    E._commit(sid,entry)
    with al_store.locked(sid):
        current=al_store.load(sid);current.setdefault('isolations',[]).append({'path':str(destination),'evidence':entry['id'],'input_fingerprint':capture['fingerprint'],'status':entry['status'],'at':time.time()});al_store.save(sid,current)
    print(json.dumps({'path':str(destination),'evidence':entry['id'],'next':'Run start with a new identity from the isolated worktree; the active run root was not switched.'}))
    if entry['status']!='PASS':raise SystemExit(1)


def register(sub):
    p=sub.add_parser('isolate');p.add_argument('--path',required=True);p.add_argument('--authorized',action='store_true');p.add_argument('--timeout',type=float,default=60);p.set_defaults(fn=cmd_isolate)
