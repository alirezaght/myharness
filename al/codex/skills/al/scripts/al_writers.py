"""Explicit writer/check lifetimes share the run's admission lock."""
import contextlib
import contextvars
import json
import os
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

def _active(st):
    return {w['id']: w for w in st.get('writers', {}).values() if w.get('state') == 'ACTIVE'}


_COMMAND = contextvars.ContextVar('al_sibling_drift_command', default=None)


def refuse_sibling_drift(command, paths, still, earlier=None, rerun=None, nothing_saved=True):
    """Refuse with the ACTIVE writers behind a moving input, or return when no writer was active.

    A repository read made twice is unstable while a writer still edits its files. With no
    writer active the caller's original drift error stands: the drift is then real."""
    import al_task_attribution as T
    command = command or _COMMAND.get() or 'implementation readiness'
    earlier = earlier or {}
    if not still and not earlier:
        return
    moved = sorted(set(paths))
    def named(writers):
        return sorted(i for i, w in writers.items() if any(T._owned(p, w['files']) for p in moved)) or sorted(writers)
    detail = ('inputs changed while writers were editing' + (' (' + ', '.join(moved) + ')' if moved else '')
              + ('; nothing was saved. ' if nothing_saved else '. '))
    def plural(owners):
        return 'writer' + ('s' if len(owners) > 1 else '') + ' ' + ', '.join(owners)
    if still:
        refuse(command + ': ' + plural(named(still)) + ' still active; run after writer finish: ' + detail
               + 'Re-run after `writer finish`' + (': ' + rerun if rerun else '.'))
    refuse(command + ': ' + plural(named(earlier)) + ' finished during ' + command + ': ' + detail
           + 'Re-run' + (': ' + rerun if rerun else '.'))


@contextlib.contextmanager
def sibling_drift(command, st, *, current=None, rerun=None):
    """Translate a capture drift or unstable attribution read while writers are ACTIVE."""
    from al_repository_capture import CaptureDrift
    import al_task_attribution as T
    token = _COMMAND.set(command)
    try:
        yield
    except (CaptureDrift, SystemExit) as exc:
        if isinstance(exc, SystemExit) and exc.code != 'al_state: ' + T.UNSTABLE_OBSERVATION:
            raise
        now = current() if current else st
        refuse_sibling_drift(command, getattr(exc, 'paths', ()), _active(now), _active(st), rerun)
        raise
    finally:
        _COMMAND.reset(token)


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

def _check_effects(st, spec):
    if not spec.get('recipe') and not spec.get('recipe_assignment'):
        return al_parallel.shared_state_hits([shlex.join(spec['argv'])])
    import al_evidence_recipe
    view = dict(spec, run=spec.get('run', st['run_id']))
    try:
        al_evidence_recipe.validate_entry_recipe(view)
        if view.get('recipe'):
            al_evidence_recipe.load_recipe(view['recipe'])
        else:
            binding = view['recipe_assignment']['parent']
            parent = next((e for e in st['evidence']['entries'] if e['id'] == binding['id']), None)
            if (parent is None or parent.get('status') != 'RUNNING' or not parent.get('recipe')
                    or any(parent.get(k) != v for k, v in binding.items())
                    or parent.get('owner_pid') != view.get('owner_pid', os.getpid())
                    or not L._alive(parent.get('owner_pid'))):
                refuse('recipe child has no exact live coordinator reservation')
            al_evidence_recipe.validate_entry_recipe(parent)
            al_evidence_recipe.load_recipe(parent['recipe'])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        refuse('invalid typed recipe operation: ' + type(exc).__name__)
    # This closed operation is not shell argv. Actual children remain fixed sandbox executions.
    return []


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
    shared=_check_effects(st,spec)
    for e in st['evidence']['entries']:
        if e.get('reconciliation') or (e['status']!='RUNNING' and not e.get('interrupted')): continue
        if e.get('interrupted') or not L._alive(e.get('owner_pid')): refuse('reconcile interrupted check '+e['id'])
        if shared or _check_effects(st,e): refuse('shared-state check overlap '+e['id'])
    # A base gate runs in a disposable checkout of the base commit: writers in the working tree
    # cannot affect it, nor it them.
    if gate_context and gate_context.get('stage')=='base':return
    if own_writer is not None and not any(w['id']==own_writer and w['state']=='ACTIVE' for w in unresolved_writers(st)):
        refuse('--writer names no active writer: '+str(own_writer))
    provisional = []
    for w in unresolved_writers(st):
        # A writer's own check runs between its own edits.
        if w['id']==own_writer and w['state']=='ACTIVE':continue
        conflict = w['state'] != 'ACTIVE' or st['phase'] in BARRIERS or shared or al_parallel.shared_state_hits(w['commands'])
        reads = '.' in spec['scope'] or al_parallel.overlaps(w['files'],spec['scope'])
        if not conflict and reads and own_writer is not None:
            # The scope only reads a disjoint sibling's paths: run now, provisional on that sibling.
            provisional.append(w['id']);continue
        if conflict or reads:
            hint = '' if conflict or own_writer is not None else '; a writer\'s own check may run with --writer <id> (provisional until ' + w['id'] + ' finishes)'
            refuse('check overlaps unresolved writer ' + w['id'] + hint)
    return provisional


PROVISIONAL_PENDING = {'ACTIVE', 'UNRESOLVED'}


def provisional_note(writers):
    return 'needs revalidation after writer' + ('s ' if len(writers) > 1 else ' ') + ', '.join(writers) + ' finish' + ('' if len(writers) > 1 else 'es')


def provisional_gap(st, entry):
    """Why a provisional result is not yet evidence, or None once every sibling it read finished.

    A finished sibling's in-scope edits are caught by the scope fingerprint: the result stays
    VALID only if nothing the check read changed since it ran, otherwise it is STALE."""
    value = entry.get('provisional')
    if not value:return None
    pending = [w for w in value.get('writers', []) if st.get('writers', {}).get(w, {}).get('state', 'ACTIVE') in PROVISIONAL_PENDING]
    return provisional_note(pending) if pending else None


def _report_provisional(st, writer_id):
    import al_ledger as L
    for e in st['evidence']['entries']:
        if writer_id not in (e.get('provisional') or {}).get('writers', []):continue
        verdict, why = L.entry_verdict(st, e)
        tail = ' — rerun `evidence run --area ' + e['area'] + '`' if verdict not in {'VALID', 'PROVISIONAL'} else ''
        print('AL · evidence ' + e['id'] + ' ' + e['area'] + ' provisional on ' + writer_id + ': now ' + verdict + (' (' + why + ')' if why else '') + tail)

def _implicit_entry(st):
    # A writer after SPEC_READY/classification begins implementation; the Designer stage
    # (designer_required) is still entered explicitly before any writer.
    if (st['status'] != 'RUNNING' or st.get('spec_status') != 'SPEC_READY'
            or not st.get('classification')):
        return False
    return st['phase'] == 'PSEUDOCODE' or (
        st['phase'] == 'PLANNING' and not st.get('designer_required'))


def enter_implementation(st, role):
    """Record the documented PLANNING/PSEUDOCODE -> IMPLEMENTATION edge for an ordinary writer.

    The same guarded transition as `phase IMPLEMENTATION` runs, so its refusals (operations,
    recovery, identity) still apply; nothing is saved unless the writer is admitted."""
    if not _implicit_entry(st):
        return False
    from al_transitions import require_transition
    require_transition(st, 'phase', 'IMPLEMENTATION')
    st['failure'] = None
    st['fanout'] = None
    al_store.set_phase(st, 'IMPLEMENTATION', [role])
    return True


def next_writer_step(st):
    """Name the next supported command for a writer refused by phase."""
    phase = st.get('phase')
    if st.get('status') != 'RUNNING':
        return 'next: the run is ' + str(st.get('status')) + '; use `recovery next`'
    if st.get('spec_status') != 'SPEC_READY' or not st.get('classification'):
        return ('next: record `spec-gate SPEC_READY` and `classify <SMALL|STANDARD|COMPLEX> ...`, '
                'then `writer start` enters IMPLEMENTATION')
    if phase == 'PLANNING' and st.get('designer_required'):
        return ('next: `phase PSEUDOCODE` and dispatch AL-Designer; '
                '`writer start` then enters IMPLEMENTATION')
    if phase == 'PLAN_CONFLICT':
        return 'next: resolve the plan conflict, then `phase PSEUDOCODE`'
    if phase == 'VERIFICATION':
        return ('next: `rework --source verification --files <paths> ...` '
                'returns to writing before review')
    if phase == 'REVIEW':
        return 'next: `correction` returns to writing after review findings'
    return 'next: `recovery next` names the supported continuation from ' + str(phase)


def cmd_writer(a,sid):
    if a.action == 'finish' and getattr(a, 'evidence_mode', None) == 'auto':
        # Intent command: the same `writer finish`, then `evidence run` for the areas its changes reach.
        import al_intent
        return al_intent.writer_finish_auto(a, sid)
    if a.action == 'start':
        from al_writer_progress import report, writer_progress
        with writer_progress():
            report('preflight', force=True)
            return _cmd_writer(a,sid)
    return _cmd_writer(a,sid)


def _cmd_writer(a,sid):
    st=al_store.load(sid); writers=st['writers']
    if a.action == 'start':
        from al_rework_recovery_contract import guard
        guard(st,implementation=True)
        degraded = getattr(a, 'degraded', False)
        phases = WRITING | {'PLANNING', 'PSEUDOCODE'} if degraded else WRITING
        entered = False
        if st['status'] != 'RUNNING' or st['phase'] not in phases:
            if not degraded and _implicit_entry(st):
                # Cheap argument checks precede the guarded phase edge's readiness analysis.
                if not valid_id(a.id) or a.id in writers: refuse('duplicate/invalid writer id')
                if a.role not in {'al-engineer','al-builder'}: refuse('invalid writer role')
                entered = enter_implementation(st, a.role)
            if not entered:
                refuse('writer admission requires a writing phase or explicit degraded '
                       'planning tracking; ' + next_writer_step(st))
        if not valid_id(a.id) or a.id in writers: refuse('duplicate/invalid writer id')
        if a.role not in {'al-engineer','al-builder'}: refuse('invalid writer role')
        files=canonical_paths(al_parallel.parse_paths(a.files))
        commands=json.loads(Path(a.commands_file).read_text()) if a.commands_file else []
        if not isinstance(commands,list) or any(not isinstance(c,str) for c in commands): refuse('commands file must be a JSON string array')
        shared=al_parallel.shared_state_hits(commands)
        active=unresolved_writers(st)
        if st.get('classification') == 'SMALL' and active: refuse('SMALL permits one writer')
        for w in active:
            if w['state'] != 'ACTIVE' or shared or al_parallel.shared_state_hits(w['commands']) or al_parallel.overlaps(files,w['files']): refuse('writer overlap: ' + w['id'])
        for e in st['evidence']['entries']:
            if (e['status']=='RUNNING' or e.get('interrupted')) and not e.get('reconciliation'):
                if shared or '.' in e['scope'] or al_parallel.overlaps(files,e['scope']) or _check_effects(st,e): refuse('writer overlaps check '+e['id'])
        if degraded:
            from al_new_origin import validate
            validate(st)
            if st.get('proof_admission', {}).get('status') != 'pending' or not getattr(a, 'reason', '').strip():
                refuse('degraded tracking requires pending proof admission and an explicit reason')
            if st.get('spec_status') != 'SPEC_READY' or not st.get('classification'):
                refuse('degraded tracking still requires SPEC_READY and classification')
        else:
            from al_implementation import require_preflight
            require_preflight(st)
        if 'integration' in st:
            from al_execution_tasks import require_task_admission
            task = getattr(a, 'task', None)
            agent = st['integration']['agents'].get(getattr(a, 'agent', None))
            execution_task = require_task_admission(st, task, files=files)
            receipt = execution_task.get('admission')
            if execution_task['status'] != 'Running' or receipt != {'revision':execution_task['revision'],'graph_revision':st['integration']['graph_revision']}: refuse('writer requires current admitted and started task')
            if not agent or agent['task'] != task or agent['role'] != a.role or agent['status'] != 'Running': refuse('writer requires compatible actual assigned running agent')
        from al_gate_coverage import writer_ready
        # A gate refresh rediscovers planned inputs; a sibling still editing them is named.
        with sibling_drift('writer start', st):
            writer_ready(st,files)
        if 'integration' in st:
            from al_execution_outputs import invalidate_for_write
            invalidate_for_write(st, a.task, files)
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
        # A rule file the writer edited (a nested AGENTS.md, manifest or CI file) refreshes the
        # gate manifest now, once no writer is still editing, instead of failing every gate later.
        from al_gate_refresh import settle
        settle(st,changed=w['files'])
    if a.action == 'start':
        from al_writer_progress import report
        report('writer-publication', force=True)
    al_store.save(sid,st)
    if a.action in {'finish', 'reconcile'}:
        _report_provisional(st, a.id)
    if a.action == 'start':
        if entered:
            print('AL · IMPLEMENTATION entered by writer start (the `phase IMPLEMENTATION` edge).')
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
    p.add_argument('--agent-id',help='record the dispatched agent instance (warm-agent resume, #30)'); p.add_argument('--fresh-reason')
    p.set_defaults(fn=cmd_handoff)
    for action in ('start','finish','reconcile'):
        p=children.add_parser(action); p.add_argument('--id',required=True); p.set_defaults(fn=cmd_writer)
        if action=='start':
            p.add_argument('--role',required=True); p.add_argument('--files',required=True); p.add_argument('--commands-file'); p.add_argument('--task'); p.add_argument('--agent')
            p.add_argument('--degraded',action='store_true');p.add_argument('--reason',default='')
        elif action=='finish':
            p.add_argument('--outcome',required=True,choices=['success','failed','abandoned'])
            p.add_argument('--evidence',dest='evidence_mode',choices=['auto','none'],default='none',
                           help='auto: then run the planned evidence areas the writer\'s changed paths reach, and print the next step')
        else: p.add_argument('--reason',required=True); p.add_argument('--artifact',required=True)
