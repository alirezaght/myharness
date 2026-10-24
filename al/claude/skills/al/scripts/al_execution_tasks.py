"""Task-scoped admission layered on existing AL writer/evidence guards."""
import copy
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path

import al_events as E
import al_store as S
from al_execution_contract import BOOTSTRAP, canonical, digest, identity, loads, progress, text
from al_schema import refuse


def integration(st):
    if 'integration' not in st:
        refuse('this operation requires a harness-bound run')
    return st['integration']


def scope_revision(st, task_id):
    i = integration(st)
    task = i['tasks'].get(task_id)
    if not task:
        refuse('unknown authority task scope')
    return digest({'task':task_id,'revision':task['revision'],'graph_revision':i['graph_revision'],'amendments':[a['artifact']['sha256'] for a in st['amendments'] if task_id in a.get('affected_tasks',[])]})


def authority(st, operation, task=None, delivery=None):
    i = integration(st)
    nonce = secrets.token_hex(16)
    # Executable/module are fixed here; envelope data cannot select a program.
    argv = [sys.executable, '-I', '-c', BOOTSTRAP, 'harness.execution_authority', '--handoff', i['handoff'], '--operation', operation, '--nonce', nonce, '--binding', canonical(i['binding'])]
    revision = scope_revision(st, task) if task else None
    if task:
        argv += ['--task', task, '--scope-revision', revision]
    if delivery:
        argv += ['--delivery', delivery]
    try:
        with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
            try:
                import selectors
                import os
                chunks, size, deadline = [], 0, time.monotonic() + 10
                with selectors.DefaultSelector() as selector:
                    selector.register(proc.stdout, selectors.EVENT_READ)
                    while selector.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(argv, 10)
                        for key, _ in selector.select(min(remaining, 0.2)):
                            chunk = os.read(key.fd, 4096)
                            if not chunk:
                                selector.unregister(key.fileobj)
                            else:
                                size += len(chunk)
                                if size > 65536:
                                    raise ValueError('authority response exceeds limit')
                                chunks.append(chunk)
                output = b''.join(chunks)
                proc.wait(timeout=max(0.01, deadline - time.monotonic()))
            except BaseException:
                proc.kill()
                proc.wait()
                raise
        reply = loads(output)
        if len(output) > 65536 or proc.returncode != 0 or reply != {'allowed': True, 'nonce': nonce, 'operation': operation, 'binding': i['binding'], 'task': task, 'delivery': delivery, 'scope_revision': revision}:
            raise ValueError('authority response mismatch')
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        refuse('execution authority unavailable: ' + str(exc))
    return reply


def affected_tasks(st, task_ids):
    tasks = integration(st)['tasks']
    affected = set(task_ids)
    if not affected <= tasks.keys():
        refuse('unknown affected task')
    while True:
        expanded = affected | {t for t, row in tasks.items() if set(row['dependencies']) & affected}
        if expanded == affected:
            return affected
        affected = expanded


def _overlap(a, b):
    return a == '.' or b == '.' or a == b or a.startswith(b + '/') or b.startswith(a + '/')


def scope_tasks(st, scope):
    tasks = integration(st)['tasks']
    found = {tid for tid, task in tasks.items() if any(_overlap(a, b) for a in scope for b in task['files'])}
    if not found or any(not any(_overlap(a, b) for task in tasks.values() for b in task['files']) for a in scope):
        refuse('ambiguous/unmapped execution check scope')
    return found


def require_task_admission(st, task_id, *, files=(), check_scope=(), verify_authority=True):
    i = integration(st)
    task = i['tasks'].get(task_id)
    if not task:
        refuse('unknown execution task')
    if st['status'] != 'RUNNING' or task['status'] in {'Completed', 'Failed'}:
        refuse('task is terminal or run is not running')
    if any(i['tasks'][dep]['status'] != 'Completed' for dep in task['dependencies']):
        refuse('task dependencies are not completed')
    for blocker in i['blockers'].values():
        if blocker['state'] != 'Resolved' and task_id in affected_tasks(st, blocker['tasks']):
            refuse('task is blocked by ' + blocker['id'])
    for obligation in i['revalidations'].values():
        if task_id in obligation['tasks']:
            if obligation['state'] != 'Validated' or obligation['graph_revision'] != i['graph_revision']:
                refuse('task requires current scoped Architect revalidation')
            import al_artifacts as A
            report = A.read_json(obligation['artifact'])
            if report.get('packet_hash') != obligation['packet_hash'] or report.get('id') != obligation['id']:
                refuse('scoped revalidation artifact provenance changed')
    if files and any(not any(a == b or a.startswith(b + '/') for b in task['files']) for a in files):
        refuse('writer exceeds task ownership')
    if check_scope and task_id not in scope_tasks(st, check_scope):
        refuse('check does not intersect execution task')
    if verify_authority:
        authority(st, 'admit', task_id)
    return task


def _pre_task_base_check(st, spec, gate_context):
    i = integration(st)
    if (not gate_context or st['status'] != 'RUNNING' or st['phase'] != 'PLANNING'
            or i['tasks'] or i['graph_revision'] != 0 or i['blockers'] or i['revalidations']):
        return False
    # Only the gate dispatcher supplies this context; public evidence labels do not.
    refs = st.get('gate_manifests', [])
    if not refs or gate_context != {'stage': 'base', 'manifest': refs[-1]['sha256'], 'gate': gate_context.get('gate')}:
        return False
    from al_gates import manifest
    current = manifest(st)
    gate = next((g for g in current['gates'] if g['id'] == gate_context['gate']), None)
    if not gate or not gate['required'] or gate['applicability'] != 'APPLICABLE':
        return False
    expected = {'area': 'gate-base-' + gate['id'], 'kind': 'check', 'tier': 'focused',
                'argv': gate['argv'], 'cwd': gate['cwd'], 'scope': gate['scope'],
                'env': gate['env_names'], 'sources': gate.get('sources', [])}
    if any(spec.get(key) != value for key, value in expected.items()):
        return False
    # Base verification may precede worker ingestion; start is the read-only
    # authority query that also validates an unbound original launch reservation.
    authority(st, 'start')
    return True


def admit_check(st, spec, *, gate_context=None):
    if 'integration' not in st:
        return
    if _pre_task_base_check(st, spec, gate_context):
        return
    if spec.get('kind') == 'preflight' and not spec.get('scope'):
        # Tool availability touches no repository path, so no execution task owns it.
        authority(st, 'start')
        return
    for tid in sorted(scope_tasks(st, spec['scope'])):
        task = integration(st)['tasks'][tid]
        # Completed tasks remain eligible for verification, subject to blockers.
        saved = task['status']
        if saved == 'Completed':
            task['status'] = 'Waiting'
        try:
            require_task_admission(st, tid, check_scope=spec['scope'])
        finally:
            task['status'] = saved


def completion_gaps(st):
    if 'integration' not in st:
        return []
    i = integration(st)
    return (['execution has no registered tasks'] if not i['tasks'] else []) + ['task ' + t + ' incomplete' for t, v in i['tasks'].items() if v['status'] != 'Completed'] + ['blocker ' + b + ' unresolved' for b, v in i['blockers'].items() if v['state'] != 'Resolved'] + ['amendment ' + a + ' awaiting revalidation' for a, v in i['revalidations'].items() if v['state'] != 'Validated']


def _strings(value, label):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value) or len(set(value)) != len(value):
        refuse('invalid ' + label)
    return value


def _task(st, data):
    from al_writers import canonical_paths
    import al_artifacts as A
    i = integration(st)
    tid = identity(data['id'])
    if tid in i['tasks']:
        if i['tasks'][tid].get('registration') == digest(data):
            return
        refuse('task identity already registered')
    dependencies = _strings(data.get('dependencies', []), 'dependencies')
    if tid in dependencies or not set(dependencies) <= i['tasks'].keys():
        refuse('dangling/cyclic task dependency')
    files = canonical_paths(data['files'])
    if any(_overlap(a, b) for row in i['tasks'].values() for a in files for b in row['files']):
        refuse('ambiguous task ownership')
    criteria = _strings(data['criteria'], 'criteria')
    if not criteria or not set(criteria) <= set(A.read_json(st['task_package'])['criteria']):
        refuse('unknown task criteria')
    row = {'id': tid, 'title': text(data['title'], 'title'), 'status': 'Idle', 'dependencies': dependencies, 'files': files, 'criteria': criteria, 'revision': 1, 'progress': None, 'registration': digest(data)}
    i['tasks'][tid] = row
    i['graph_revision'] += 1
    E.queue(st, 'task.registered', row)


def _agent(st, data):
    i = integration(st)
    aid = identity(data['id'])
    old = i['agents'].get(aid)
    if old:
        if old.get('registration') == digest(data):
            return
        refuse('agent invocation identity already registered')
    role = data['role']
    import al_models
    if role not in al_models.ALL_POLICY:
        refuse('unknown actual agent role')
    task = data.get('task')
    if task is not None and task not in i['tasks']:
        refuse('unknown agent task')
    row = {'id': aid, 'name': text(data['name'], 'agent name'), 'role': role, 'task': task, 'status': 'Idle', 'activity': '', 'progress': None, 'started_at': time.time(), 'registration': digest(data)}
    i['agents'][aid] = row
    E.queue(st, 'agent.registered', row)


def _blocker(st, data):
    i = integration(st)
    bid = identity(data['id'])
    if bid in i['blockers']:
        if i['blockers'][bid].get('registration') == digest(data):
            return
        refuse('blocker identity already registered')
    tasks = _strings(data['tasks'], 'blocker tasks')
    if not tasks:
        refuse('blocker requires affected tasks')
    affected = affected_tasks(st, tasks)
    row = {key: copy.deepcopy(data[key]) for key in ('id', 'tasks', 'question', 'context', 'alternatives', 'recommendation')}
    for key in ('question', 'context', 'recommendation'):
        text(row[key], key)
    _strings(row['alternatives'], 'alternatives')
    row.update(revision=1, state='Open', affected=sorted(affected), registration=digest(data))
    i['blockers'][bid] = row
    for tid in affected:
        task = i['tasks'][tid]
        task.pop('admission', None)
        if task['status'] != 'Completed':
            task['status'] = 'Blocked'
            E.queue(st, 'task.updated', task)
    for agent in i['agents'].values():
        if agent['task'] in affected and agent['status'] == 'Running':
            agent.update(status='Blocked', yield_required=True)
            E.queue(st, 'agent.updated', agent)
    E.queue(st, 'blocker.raised', row)


def _graph(st, data):
    from al_writers import canonical_paths
    i = integration(st)
    tid = data['id']
    task = i['tasks'].get(tid)
    if not task:
        refuse('unknown graph task')
    dependencies = _strings(data.get('dependencies', task['dependencies']), 'dependencies')
    files = canonical_paths(data.get('files', task['files']))
    if not set(task['dependencies']) <= set(dependencies) or not set(task['files']) <= set(files):
        refuse('graph updates cannot remove approved dependencies or ownership')
    if not set(dependencies) <= i['tasks'].keys() or tid in dependencies:
        refuse('dangling/cyclic dependency')
    graph = {key: list(row['dependencies']) for key, row in i['tasks'].items()}
    graph[tid] = dependencies
    def visit(node, stack):
        if node in stack:
            refuse('cyclic task graph')
        for child in graph[node]:
            visit(child, stack | {node})
    for node in graph:
        visit(node, set())
    if any(_overlap(a, b) for other, row in i['tasks'].items() if other != tid for a in files for b in row['files']):
        refuse('ambiguous task ownership')
    affected = affected_tasks(st, [tid])
    if any(w.get('task') in affected and w['state'] in {'ACTIVE','UNRESOLVED'} for w in st['writers'].values()):
        refuse('settle affected writer before graph mutation')
    if any((e['status']=='RUNNING' or (e.get('interrupted') and not e.get('reconciliation'))) and any(_overlap(a,b) for a in e['scope'] for t in affected for b in i['tasks'][t]['files']) for e in st['evidence']['entries']):
        refuse('settle affected check before graph mutation')
    authority(st, 'admit', tid)
    if task['dependencies'] == dependencies and task['files'] == files:
        return
    for key, kind, value in (('dependencies','dependency.changed',dependencies),('files','ownership.changed',files)):
        if task[key] != value:
            task[key] = value
            E.queue(st, kind, {'task':tid,key:value})
    i['graph_revision'] += 1
    for row in i['tasks'].values():
        row.pop('admission',None)
    for item in affected:
        row = i['tasks'][item]
        if row['status'] == 'Completed':
            row['revision'] += 1
            row.update(status='Waiting',progress=None)
            E.queue(st,'task.updated',row)
    for obligation in i['revalidations'].values():
        if obligation['state'] != 'Validated':
            refuse('settle scoped revalidation before changing the graph')
        from al_ledger import scope_fingerprint
        previous = obligation.pop('artifact')
        obligation.setdefault('history', []).append(previous)
        obligation.update(state='Required', graph_revision=i['graph_revision'])
        scope = sorted({p for t in obligation['tasks'] for p in i['tasks'][t]['files']})
        obligation['candidate'] = scope_fingerprint(scope, [])
        obligation.pop('packet_hash', None)
        obligation['packet_hash'] = digest(obligation)


def cmd_integration(a, sid):
    if a.action == 'capabilities':
        print(canonical({'schema_version': 1, 'protocol': 'al-execution-v1', 'semantic_outbox': True, 'scoped_decisions': True}))
        return
    st = S.load(sid)
    if a.action == 'events':
        print(canonical(E.events_after(st, a.after, a.limit)))
        return
    i = integration(st)
    if st['status'] != 'RUNNING':
        refuse('execution reporting requires a running run')
    data = loads(Path(a.file).read_bytes()) if getattr(a, 'file', None) else None
    if a.action == 'graph-update':
        _graph(st, data)
    elif a.action == 'task-register':
        _task(st, data)
    elif a.action == 'agent-register':
        _agent(st, data)
    elif a.action == 'blocker':
        _blocker(st, data)
    elif a.action == 'agent-update':
        row = i['agents'].get(data['id'])
        if not row:
            refuse('unknown agent invocation')
        status = data['status']
        allowed = {'Idle': {'Idle', 'Running', 'Waiting', 'Failed'}, 'Running': {'Running', 'Waiting', 'Blocked', 'Failed', 'Completed'}, 'Waiting': {'Waiting', 'Running', 'Blocked', 'Failed', 'Completed'}, 'Blocked': {'Blocked', 'Waiting', 'Running', 'Failed'}, 'Failed': {'Failed'}, 'Completed': {'Completed'}}
        if status not in allowed[row['status']]:
            refuse('invalid actual agent lifecycle transition')
        if status == 'Running' and row['task']:
            require_task_admission(st, row['task'])
        progress(data.get('progress'))
        for key in ('status', 'activity', 'progress', 'latest_output'):
            if key in data:
                row[key] = data[key]
        if status in {'Completed', 'Failed'}:
            row['ended_at'] = time.time()
        E.queue(st, 'agent.updated', row)
    elif a.action == 'task-progress':
        row = require_task_admission(st, data['id'])
        progress(data.get('progress'))
        row['progress'] = data.get('progress')
        row['activity'] = text(data['activity'], 'activity')
        E.queue(st, 'task.updated', row)
    elif a.action in {'task-admit', 'task-start'}:
        row = require_task_admission(st, a.task)
        if a.action == 'task-start' and not any(v['task'] == a.task and v['status'] == 'Running' for v in i['agents'].values()):
            refuse('task start requires actual running assigned agent')
        row['admission'] = {'revision': row['revision'], 'graph_revision': i['graph_revision']}
        row['status'] = 'Running' if a.action == 'task-start' else 'Waiting'
        E.queue(st, 'task.updated', row)
    elif a.action == 'task-finish':
        row = require_task_admission(st, a.task)
        writers = [w for w in st['writers'].values() if w.get('task') == a.task]
        if not writers or any(w['state'] != 'FINISHED' or w.get('outcome') != 'success' for w in writers):
            refuse('task completion needs successful resolved writers')
        from al_ledger import entry_verdict
        relevant = [e for e in st['evidence']['entries'] if any(_overlap(p, q) for p in e['scope'] for q in row['files'])]
        plans = [p for p in st['evidence']['plan'].values() if any(_overlap(pth, q) for pth in p['scope'] for q in row['files'])]
        if not plans or any(not any(e['area'] == p['area'] and entry_verdict(st, e)[0] == 'VALID' and e['status'] == 'PASS' for e in relevant) for p in plans):
            refuse('task completion requires current planned evidence')
        if any(e['status'] == 'RUNNING' or (e.get('interrupted') and not e.get('reconciliation')) for e in relevant):
            refuse('task has unresolved check')
        row.update(status='Completed', progress={'completed': 1, 'total': 1, 'unit': 'task'}, completed_at=time.time())
        E.queue(st, 'task.updated', row)
    elif a.action == 'handoff':
        if data['from'] not in i['tasks'] or data['to'] not in i['tasks']:
            refuse('handoff references unknown task')
        text(data['text'], 'handoff')
        E.queue(st, 'handoff.created', data)
    elif a.action in {'decision', 'revalidate'}:
        import al_execution_decisions as D
        (D.apply_delivery if a.action == 'decision' else D.revalidate)(st, sid, data)
    S.save(sid, st)
    print(canonical({'ok': True, 'sequence': i['sequence']}))


def register(sub):
    p = sub.add_parser('integration').add_subparsers(dest='action', required=True)
    p.add_parser('capabilities').set_defaults(fn=cmd_integration)
    s = p.add_parser('events'); s.add_argument('--after', type=int, default=0); s.add_argument('--limit', type=int, default=100); s.set_defaults(fn=cmd_integration)
    for action in ('task-register', 'agent-register', 'agent-update', 'blocker', 'handoff', 'decision', 'revalidate', 'task-progress', 'graph-update'):
        s = p.add_parser(action); s.add_argument('--file', required=True); s.set_defaults(fn=cmd_integration)
    for action in ('task-admit', 'task-start', 'task-finish'):
        s = p.add_parser(action); s.add_argument('--task', required=True); s.set_defaults(fn=cmd_integration)
