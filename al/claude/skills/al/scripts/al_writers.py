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

def admit_check(st, spec, *, gate_context=None, own_writer=None):
    from al_execution_tasks import admit_check as execution_check
    execution_check(st, spec, gate_context=gate_context)
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
    # A base gate runs in a disposable checkout of the base commit: writers in the working tree
    # cannot affect it, nor it them.
    if gate_context and gate_context.get('stage')=='base':return
    if own_writer is not None and not any(w['id']==own_writer and w['state']=='ACTIVE' for w in unresolved_writers(st)):
        refuse('--writer names no active writer: '+str(own_writer))
    for w in unresolved_writers(st):
        # A writer's own check runs between its own edits; other writers still conflict as before.
        if w['id']==own_writer and w['state']=='ACTIVE':continue
        if w['state'] != 'ACTIVE' or st['phase'] in BARRIERS or shared or al_parallel.shared_state_hits(w['commands']) or '.' in spec['scope'] or al_parallel.overlaps(w['files'],spec['scope']):
            refuse('check overlaps unresolved writer ' + w['id'])

def cmd_writer(a,sid):
    st=al_store.load(sid); writers=st['writers']
    if a.action == 'start':
        from al_rework_recovery_contract import guard
        guard(st)
        from al_new_origin import require_admitted, validate
        degraded = getattr(a, 'degraded', False)
        if degraded:
            validate(st)
            if st.get('proof_admission', {}).get('status') != 'pending' or not getattr(a, 'reason', '').strip():
                refuse('degraded tracking requires pending proof admission and an explicit reason')
            if st.get('spec_status') != 'SPEC_READY' or not st.get('classification'):
                refuse('degraded tracking still requires SPEC_READY and classification')
        else: require_admitted(st)
        phases = WRITING | {'PLANNING', 'PSEUDOCODE'} if degraded else WRITING
        if st['status'] != 'RUNNING' or st['phase'] not in phases: refuse('writer admission requires a writing phase or explicit degraded planning tracking')
        if not valid_id(a.id) or a.id in writers: refuse('duplicate/invalid writer id')
        if a.role not in {'al-engineer','al-builder'}: refuse('invalid writer role')
        files=canonical_paths(al_parallel.parse_paths(a.files))
        if 'integration' in st:
            from al_execution_tasks import require_task_admission
            task = getattr(a, 'task', None)
            agent = st['integration']['agents'].get(getattr(a, 'agent', None))
            execution_task = require_task_admission(st, task, files=files)
            receipt = execution_task.get('admission')
            if execution_task['status'] != 'Running' or receipt != {'revision':execution_task['revision'],'graph_revision':st['integration']['graph_revision']}: refuse('writer requires current admitted and started task')
            if not agent or agent['task'] != task or agent['role'] != a.role or agent['status'] != 'Running': refuse('writer requires compatible actual assigned running agent')
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
        from al_task_attribution import begin_contribution
        before = begin_contribution(st, {'id':a.id,'files':files})
        writers[a.id]={'id':a.id,'role':a.role,'files':files,'commands':commands,'phase':st['phase'],'started_at':time.time(),'state':'ACTIVE', **({'task':a.task,'agent':a.agent} if 'integration' in st else {})}
        if before is not None: writers[a.id]['contribution_before']=before
        if degraded:
            writers[a.id]['degraded'] = {'reason': a.reason, 'proof_status': 'pending', 'started_at': time.time()}
    else:
        w=writers.get(a.id)
        if not w: refuse('unknown writer')
        if a.action=='finish':
            if w['state']!='ACTIVE': refuse('writer is not active')
            w.update(state='FINISHED' if a.outcome=='success' else 'UNRESOLVED',outcome=a.outcome,ended_at=time.time())
            from al_task_attribution import finish_contribution
            finish_contribution(st,w,w.get('contribution_before'))
        else:
            if w['state'] not in {'ACTIVE','UNRESOLVED'} or not a.reason: refuse('reconciliation requires unresolved writer and reason')
            w.update(state='RECONCILED',reconciliation={'reason':a.reason,'artifact':snapshot_artifact(sid,Path(a.artifact),'writer-reconciliation'),'at':time.time()})
    al_store.save(sid,st)
    if a.action == 'start':
        if getattr(a, 'degraded', False):
            print('AL · DEGRADED TRACKING · scoped writer/evidence only; proof admission, review and approval remain required.')
        from al_writer_handoff import evidence_block
        print('Generate the complete writer handoff with writer handoff --id ' + shlex.quote(a.id) + ' --body-file PATH; dispatch its output verbatim.')
        if st['evidence']['plan']:
            print(evidence_block(st, sid, a.id, sorted(st['evidence']['plan'])))
        else:
            print('EVIDENCE: no planned checks; register evidence areas before generating the handoff for --writer ' + a.id)

def register(sub):
    children=sub.add_parser('writer').add_subparsers(dest='action',required=True)
    from al_writer_handoff import cmd_handoff
    p=children.add_parser('handoff'); p.add_argument('--id',required=True)
    p.add_argument('--body-file',required=True); p.add_argument('--area',action='append')
    p.set_defaults(fn=cmd_handoff)
    for action in ('start','finish','reconcile'):
        p=children.add_parser(action); p.add_argument('--id',required=True); p.set_defaults(fn=cmd_writer)
        if action=='start':
            p.add_argument('--role',required=True); p.add_argument('--files',required=True); p.add_argument('--commands-file'); p.add_argument('--task'); p.add_argument('--agent')
            p.add_argument('--degraded',action='store_true');p.add_argument('--reason',default='')
        elif action=='finish': p.add_argument('--outcome',required=True,choices=['success','failed','abandoned'])
        else: p.add_argument('--reason',required=True); p.add_argument('--artifact',required=True)
