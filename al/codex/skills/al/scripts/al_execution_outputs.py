"""Byte-bound implementation milestones, distinct from verified task completion."""
import copy
import os
import stat

import al_artifacts as A
import al_events as E
import al_store as S
import al_task
from al_execution_contract import digest, validate_authored
from al_schema import refuse


def _overlap(a, b):
    return a == '.' or b == '.' or a == b or a.startswith(b + '/') or b.startswith(a + '/')


def _settled(st, affected):
    tasks = st['integration']['tasks']
    if any(w.get('task') in affected and w['state'] in {'ACTIVE', 'UNRESOLVED'} for w in st['writers'].values()):
        refuse('settle affected writer before authored output change')
    for entry in st['evidence']['entries']:
        if ((entry['status'] == 'RUNNING' or entry.get('interrupted')) and not entry.get('reconciliation')
                and any(_overlap(p, q) for p in entry['scope'] for t in affected for q in tasks[t]['files'])):
            refuse('settle affected or uncertain check before authored output change')


def invalidate(st, task_id, reason):
    from al_execution_tasks import affected_tasks
    affected = affected_tasks(st, [task_id])
    _settled(st, affected)
    for tid in sorted(affected):
        row = st['integration']['tasks'][tid]
        row.pop('admission', None)
        old = row.pop('authored_output', None)
        if old:
            row.setdefault('authored_history', []).append(old)
            E.queue(st, 'output.invalidated', {'task_id': tid, 'reason': reason})


def invalidate_for_write(st, task_id, paths):
    for tid, row in st['integration']['tasks'].items():
        receipt = row.get('authored_output')
        if receipt and any(r['kind'] == 'source' and any(_overlap(r['path'], p) for p in paths)
                           for r in receipt['output_refs_and_hashes']):
            invalidate(st, tid, 'producer output is being rewritten')


def _source(path):
    from al_writers import canonical_paths
    canonical_paths([path])
    from pathlib import PurePosixPath
    if any(part.startswith('.env') or part in {'.git', '.codex', '.claude', '.agents', '.aws', '.ssh', '.gnupg', '.netrc', '.git-credentials', '.mcp.json', '.npmrc', '.pypirc', 'credentials', 'credentials.json'} for part in PurePosixPath(path).parts):
        refuse('authored output cannot read protected credentials')
    from al_scope import safe_path
    file = S.REPO_ROOT / safe_path(path)
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            refuse('authored source must be a single-link regular file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read()
        after = os.fstat(fd)
        def stable(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if stable(before) != stable(after) or stable(file.lstat()) != stable(after):
            refuse('authored source moved during observation')
    finally:
        os.close(fd)
    return {'kind': 'source', 'path': path, 'sha256': digest(raw), 'mode': stat.S_IMODE(after.st_mode)}


def _producer(st, aid, task_id):
    agent = st['integration']['agents'].get(aid)
    if (not agent or agent['task'] != task_id or agent['status'] != 'Completed'
            or not agent.get('ended_at') or agent.get('yield_required')):
        refuse('authored output requires an actual successful completed producer invocation')
    return agent


def _source_writers(st, task, output):
    import al_task_attribution as T
    if 'task_attribution' not in st:
        refuse('authored source requires retained contribution provenance')
    classified = T.classify_changes(st)
    if output['path'] not in classified['task_owned']:
        refuse('authored source has no unambiguous owned contribution')
    matches = []
    for ref in st['task_attribution']['contributions']:
        contribution = T._read(st, ref, 'task-contribution-v1')
        writer = st['writers'][contribution['writer_id']]
        if writer.get('task') != task['id'] or not T._owned(output['path'], writer['files']):
            continue
        if writer['state'] != 'FINISHED' or writer.get('outcome') != 'success':
            refuse('authored producer writer was not released successfully')
        _producer(st, writer.get('agent'), task['id'])
        capture = T._capture(st, contribution['after_capture_ref'])
        row = next((r for r in capture['files'] if r['path'] == output['path']), None)
        if (row and output['path'] in contribution['changed_records']
                and row['worktree']['type'] == 'file'
                and 'sha256:' + row['worktree']['sha256'] == output['sha256']
                and row['physical_mode'] == output['mode']
                and contribution['ended_task_hash'] == al_task.task_hash(st)):
            matches.append(writer)
    if not matches:
        refuse('authored source differs from released producer bytes/revision')
    return matches


def current(st, task_id, _seen=()):
    if task_id in _seen:
        refuse('cyclic authored dependency graph')
    row = st['integration']['tasks'][task_id]
    if any(not current(st, dep, (*_seen, task_id)) for dep in row['dependencies']):
        return False
    if row['status'] == 'Completed':
        return verified_current(st, task_id)
    receipt = row.get('authored_output')
    if receipt is None:
        return False
    validate_authored(receipt)
    if (receipt['task_revision'] != row['revision']
            or receipt['graph_revision'] != st['integration']['graph_revision']
            or receipt['requirement_hash'] != al_task.task_hash(st)):
        return False
    _settled(st, {task_id})
    for aid in receipt['producer_invocation_ids']:
        _producer(st, aid, task_id)
    for wid in receipt['writer_ids']:
        w = st['writers'].get(wid)
        if not w or w['state'] != 'FINISHED' or w.get('outcome') != 'success':
            refuse('authored writer provenance changed')
    for output in receipt['output_refs_and_hashes']:
        if output['kind'] == 'source':
            if _source(output['path']) != output:
                return False
        else:
            from al_execution_tasks import authority
            authority(st, 'authored-output', task_id, delivery=output['resource'])
    return receipt['candidate_identity'] == candidate(st, task_id)


def authored(st, task_id, data):
    from al_execution_tasks import require_task_admission, authority
    if (not isinstance(data, dict) or set(data) != {'schema_version', 'task_id', 'output_refs'}
            or type(data['schema_version']) is not int or data['schema_version'] != 1
            or data['task_id'] != task_id or not isinstance(data['output_refs'], list)
            or not data['output_refs']):
        refuse('invalid authored output input')
    row = require_task_admission(st, task_id)
    _settled(st, {task_id})
    outputs, writers, producers = [], set(), set()
    for ref in data['output_refs']:
        if isinstance(ref, dict) and set(ref) == {'kind', 'path'} and ref['kind'] == 'source':
            if not any(ref['path'] == p or ref['path'].startswith(p + '/') for p in row['files']):
                refuse('authored output exceeds task ownership')
            output = _source(ref['path'])
            for writer in _source_writers(st, row, output):
                writers.add(writer['id'])
                producers.add(writer['agent'])
        elif isinstance(ref, dict) and set(ref) == {'kind', 'resource', 'producer'} and ref['kind'] == 'report':
            agent = _producer(st, ref['producer'], task_id)
            if agent['role'] not in {'al-architect', 'al-designer', 'al-architect-fallback', 'al-designer-fallback'}:
                refuse('read-only authored reports require Architect/Designer producer')
            authority(st, 'authored-output', task_id, delivery=ref['resource'])
            # Trusted authority validates resource, owner, role, bytes and committed lifecycle.
            if agent.get('latest_output') != ref['resource']:
                refuse('report is not the completed producer submitted output')
            output = dict(ref, sha256=ref['resource'].split(':', 2)[2])
            producers.add(agent['id'])
        else:
            refuse('unknown authored output reference')
        if output in outputs:
            refuse('duplicate authored output')
        outputs.append(output)
    value = {'task_id': task_id, 'task_revision': row['revision'],
             'graph_revision': st['integration']['graph_revision'], 'requirement_hash': al_task.task_hash(st),
             'candidate_identity': candidate(st, task_id), 'output_refs_and_hashes': outputs,
             'producer_invocation_ids': sorted(producers), 'writer_ids': sorted(writers)}
    value['receipt_hash'] = digest(value)
    for output in outputs:
        if output['kind'] == 'source':
            if _source(output['path']) != output:
                refuse('authored source changed before publication')
        else:
            authority(st, 'authored-output', task_id, delivery=output['resource'])
    validate_authored(value)
    if row.get('authored_output') == value:
        return value
    if row.get('authored_output'):
        invalidate(st, task_id, 'authored output receipt replaced')
    row['authored_output'] = copy.deepcopy(value)
    E.queue(st, 'output.authored', value)
    return value


def verified_current(st, task_id):
    _settled(st, {task_id})
    row = st['integration']['tasks'][task_id]
    value = row.get('completion_binding')
    return bool(value and value['task_revision'] == row['revision']
                and value['graph_revision'] == st['integration']['graph_revision']
                and value['requirement_hash'] == al_task.task_hash(st)
                and value['candidate_identity'] == candidate(st, task_id))


def completion_binding(st, task_id):
    from al_task_attribution import observe, _owned
    row = st['integration']['tasks'][task_id]
    outputs = [_source(r['path']) for r in observe(st)['files']
               if _owned(r['path'], row['files']) and r['worktree']['type'] == 'file']
    return {'task_revision': row['revision'], 'graph_revision': st['integration']['graph_revision'],
            'requirement_hash': al_task.task_hash(st),
            'candidate_identity': candidate(st, task_id), 'outputs': outputs}


def candidate(st, task_id):
    from al_ledger import scope_fingerprint
    row = st['integration']['tasks'][task_id]
    dependencies = {}
    for dep in row['dependencies']:
        task = st['integration']['tasks'][dep]
        output = task.get('completion_binding') if task['status'] == 'Completed' else task.get('authored_output')
        if not output:
            refuse('dependency output binding is missing')
        dependencies[dep] = digest(output)
    return {'scope_fingerprint': scope_fingerprint(row['files'], []),
            'dependency_receipts': dependencies}


def reopen_stale_completion(st, task_id):
    from al_execution_tasks import authority
    row = st['integration']['tasks'].get(task_id)
    if not row or row['status'] != 'Completed' or current(st, task_id):
        return False
    authority(st, 'admit', task_id)
    invalidate(st, task_id, 'verified output requires a current observation')
    old = row.pop('completion_binding', None)
    if old:
        row.setdefault('completion_history', []).append(old)
    row.update(status='Waiting', revision=row['revision'] + 1, progress=None)
    E.queue(st, 'task.updated', row)
    return True
