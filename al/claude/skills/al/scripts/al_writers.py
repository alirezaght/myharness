"""Explicit writer/check lifetimes share the run's admission lock."""
import json
import shlex
import time
from pathlib import Path
import al_store
import al_parallel
import al_ledger as L
from al_schema import refuse, valid_id
from al_artifacts import snapshot_artifact
from al_transitions import WRITING, BARRIERS

def unresolved_writers(st):
    return [w for w in st['writers'].values() if w['state'] in {'ACTIVE','UNRESOLVED'}]

def operation_gaps(st):
    out = ['writer ' + w['id'] + ' is ' + w['state'] for w in unresolved_writers(st)]
    out += ['check ' + e['id'] + ' unresolved' for e in st['evidence']['entries']
            if (e['status'] == 'RUNNING' or e.get('interrupted')) and not e.get('reconciliation')]
    return out

def canonical_paths(paths):
    out=[]
    for raw in paths:
        p=Path(raw)
        target=(al_store.REPO_ROOT/p).resolve()
        if p.is_absolute() or '..' in p.parts or target == al_store.REPO_ROOT or al_store.REPO_ROOT not in target.parents:
            refuse('writer path must be a specific repository-relative path')
        if any((al_store.REPO_ROOT/Path(*p.parts[:i])).is_symlink() for i in range(1,len(p.parts)+1)):
            refuse('writer ownership cannot traverse symlinks')
        out.append(p.as_posix())
    if not out: refuse('writer needs owned files')
    return out

def admit_check(st, spec):
    if st['status'] != 'RUNNING' or st['phase'] in {'REVIEW','DONE'}: refuse('checks cannot start in this state')
    from al_scope import safe_path
    cwd=spec.get('cwd') or '.'
    if cwd!='.': safe_path(cwd)
    target=(al_store.REPO_ROOT/cwd).resolve()
    if target!=al_store.REPO_ROOT and al_store.REPO_ROOT not in target.parents: refuse('check cwd escapes pinned repository')
    if not target.is_dir(): refuse('check cwd does not exist')
    missing=L.unmet(st,spec)
    if missing: refuse('; '.join(missing))
    shared=al_parallel.shared_state_hits([shlex.join(spec['argv'])])
    for e in st['evidence']['entries']:
        if e.get('reconciliation') or (e['status']!='RUNNING' and not e.get('interrupted')): continue
        if e.get('interrupted') or not L._alive(e.get('owner_pid')): refuse('reconcile interrupted check '+e['id'])
        if shared or al_parallel.shared_state_hits([shlex.join(e['argv'])]): refuse('shared-state check overlap '+e['id'])
    for w in unresolved_writers(st):
        if w['state'] != 'ACTIVE' or st['phase'] in BARRIERS or shared or al_parallel.shared_state_hits(w['commands']) or '.' in spec['scope'] or al_parallel.overlaps(w['files'],spec['scope']):
            refuse('check overlaps unresolved writer ' + w['id'])

def cmd_writer(a,sid):
    st=al_store.load(sid); writers=st['writers']
    if a.action == 'start':
        if st['status'] != 'RUNNING' or st['phase'] not in WRITING: refuse('writer admission requires a writing phase')
        if not valid_id(a.id) or a.id in writers: refuse('duplicate/invalid writer id')
        if a.role not in {'al-engineer','al-builder'}: refuse('invalid writer role')
        files=canonical_paths(al_parallel.parse_paths(a.files))
        from al_gate_coverage import writer_ready
        writer_ready(st,files)
        commands=json.loads(Path(a.commands_file).read_text()) if a.commands_file else []
        if not isinstance(commands,list) or any(not isinstance(c,str) for c in commands): refuse('commands file must be a JSON string array')
        shared=al_parallel.shared_state_hits(commands)
        active=unresolved_writers(st)
        if st.get('classification') == 'SMALL' and active: refuse('SMALL permits one writer')
        for w in active:
            if w['state'] != 'ACTIVE' or shared or al_parallel.shared_state_hits(w['commands']) or al_parallel.overlaps(files,w['files']): refuse('writer overlap: ' + w['id'])
        for e in st['evidence']['entries']:
            if (e['status']=='RUNNING' or e.get('interrupted')) and not e.get('reconciliation'):
                if shared or '.' in e['scope'] or al_parallel.overlaps(files,e['scope']) or al_parallel.shared_state_hits([shlex.join(e['argv'])]): refuse('writer overlaps check '+e['id'])
        from al_gate_recovery import complete
        complete(st)
        writers[a.id]={'id':a.id,'role':a.role,'files':files,'commands':commands,'phase':st['phase'],'started_at':time.time(),'state':'ACTIVE'}
    else:
        w=writers.get(a.id)
        if not w: refuse('unknown writer')
        if a.action=='finish':
            if w['state']!='ACTIVE': refuse('writer is not active')
            w.update(state='FINISHED' if a.outcome=='success' else 'UNRESOLVED',outcome=a.outcome,ended_at=time.time())
        else:
            if w['state'] not in {'ACTIVE','UNRESOLVED'} or not a.reason: refuse('reconciliation requires unresolved writer and reason')
            w.update(state='RECONCILED',reconciliation={'reason':a.reason,'artifact':snapshot_artifact(sid,Path(a.artifact),'writer-reconciliation'),'at':time.time()})
    al_store.save(sid,st)

def register(sub):
    children=sub.add_parser('writer').add_subparsers(dest='action',required=True)
    for action in ('start','finish','reconcile'):
        p=children.add_parser(action); p.add_argument('--id',required=True); p.set_defaults(fn=cmd_writer)
        if action=='start':
            p.add_argument('--role',required=True); p.add_argument('--files',required=True); p.add_argument('--commands-file')
        elif action=='finish': p.add_argument('--outcome',required=True,choices=['success','failed','abandoned'])
        else: p.add_argument('--reason',required=True); p.add_argument('--artifact',required=True)
