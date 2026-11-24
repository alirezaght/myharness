"""Content-based task contributions; absence of provenance never narrows discovery."""
import json
import hashlib
import os
import stat
import re
import al_artifacts as A
import al_scope as S
import al_store
import al_task
import al_writer_progress as progress
from al_schema import refuse

UNSTABLE_OBSERVATION = 'unstable task attribution observation'


def _binding(st):
    return {k: st[k] for k in ('session_id', 'run_id', 'identity')}


def _rows(st):
    base = st['identity']['merge_base'] if st.get('review_mode') == 'whole-mr' else st['baseline_head']
    rows = S.records(base)
    for row in rows:
        path = al_store.REPO_ROOT / S.safe_path(row['path'])
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            row['physical_mode'] = None
        else:
            row['physical_mode'] = stat.S_IMODE(metadata.st_mode)
    return rows


def concurrent_paths(st, writer_id):
    """Ownership of the other ACTIVE writers: paths a sibling may be editing right now.

    Parallel writers are admitted only with disjoint ownership, so a sibling's paths are never
    this writer's. A writer's contribution capture leaves them out (the sibling's own capture
    attributes them); every other path stays in the capture and must be stable."""
    return sorted({p for w in st.get('writers', {}).values()
                   if w.get('state') == 'ACTIVE' and w.get('id') != writer_id for p in w['files']})


def observe(st, *, retain_content=False, expected=None, concurrent=()):
    """Double observation includes non-executable mode changes and new membership.

    A re-observation compared against an `expected` capture that was itself stable needs one
    read: equality with a stable observation is the stability proof, and any difference is
    refused by the caller's comparison. Paths under `concurrent` (a sibling writer's ownership)
    are excluded from the capture, so a sibling still editing cannot destabilize it. A plain
    (unretained) observation keeps ACTIVE writers' paths as read but does not require them to
    be stable: they are mid-edit by design, and only a retained capture binds content."""
    concurrent = sorted(set(concurrent))
    volatile = () if retain_content else concurrent_paths(st, None)
    def rows():
        found = _rows(st)
        return [r for r in found if not _owned(r['path'], concurrent)] if concurrent else found
    def stable(rows):
        return [r for r in rows if not _owned(r['path'], volatile)] if volatile else rows
    progress.report('attribution-first', count=0)
    first = rows()
    progress.report('attribution-first', count=len(first), force=True)
    if expected is None or retain_content or first != expected.get('files'):
        progress.report('attribution-second', count=0)
        second = rows()
        progress.report('attribution-second', count=len(second), force=True)
        if stable(first) != stable(second):
            refuse(UNSTABLE_OBSERVATION)
    else:
        second = first
    capture = {'schema_version': 1, 'kind': 'task-content-capture-v1',
               'binding': _binding(st), 'files': second}
    if concurrent:
        capture['concurrent_writer_paths'] = concurrent
    if retain_content:
        progress.report('attribution-retain', count=0)
        capture['contents'] = {}
        for count,row in enumerate(second,1):
            work = row['worktree']; path = al_store.REPO_ROOT / S.safe_path(row['path'])
            if work['type'] not in {'file', 'symlink'}:continue
            raw = os.fsencode(os.readlink(path)) if work['type'] == 'symlink' else path.read_bytes()
            expected = hashlib.sha256(os.fsencode(work['text'])).hexdigest() if work['type'] == 'symlink' else work['sha256']
            if hashlib.sha256(raw).hexdigest() != expected:refuse('source moved during attribution retention')
            capture['contents'][row['path']] = A.snapshot_bytes(st['session_id'], raw, 'task-source-content-v1', row['path'])
            progress.report('attribution-retain', count=count)
        progress.report('attribution-retain', count=len(second), force=True)
        progress.report('attribution-final', count=0)
        if rows() != second:refuse('source moved during attribution retention')
        progress.report('attribution-final', count=len(second), force=True)
    return capture


def _save(st, data, kind):
    return A.snapshot_bytes(st['session_id'], json.dumps(data, sort_keys=True, allow_nan=False).encode(), kind, 'AL task attribution')


def _read(st, ref, kind):
    try:value = A.read_json(ref)
    except (OSError, ValueError, KeyError, TypeError):refuse('unreadable task attribution artifact: '+kind)
    if not isinstance(value, dict):refuse('invalid task attribution artifact: '+kind)
    if value.get('schema_version') != 1 or value.get('kind') != kind or value.get('binding') != _binding(st):
        refuse('foreign or corrupt task attribution')
    return value


def _validate_conflict_records(rows):
    """Validate the additive conflict variant without changing historical stage-zero rows."""
    for row in rows:
        entry = row.get('index')
        if not isinstance(entry, dict) or 'stages' not in entry:
            continue
        stages = entry['stages']
        if (set(entry) != {'stages'} or not isinstance(stages, list) or not stages
                or any(not isinstance(e, dict) or set(e) != {'stage', 'mode', 'oid', 'sha256'}
                       or type(e['stage']) is not int or e['stage'] not in {1, 2, 3}
                       or not isinstance(e['mode'], str) or e['mode'] not in {'100644', '100755'}
                       or not isinstance(e['oid'], str) or not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', e['oid'])
                       or not isinstance(e['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', e['sha256'])
                       for e in stages)
                or [e['stage'] for e in stages] != sorted({e['stage'] for e in stages})
                or not isinstance(row.get('worktree'), dict)
                or row['worktree'].get('type') not in {'file', 'deleted'}):
            refuse('invalid retained unmerged index records')
        # Git object identity includes its content: recheck immutable stage evidence rather
        # than accepting an arbitrary digest supplied in a retained artifact.
        hashes = S._blob_hashes(al_store.REPO_ROOT, [e['oid'] for e in stages])
        if any(hashes[e['oid']] != e['sha256'] for e in stages):
            refuse('retained unmerged stage content differs')


def _capture(st, ref):
    value = _read(st, ref, 'task-content-capture-v1')
    rows = value.get('files')
    if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get('path'), str) for r in rows):
        refuse('invalid task attribution capture')
    if len({r['path'] for r in rows}) != len(rows):
        refuse('duplicate task attribution member')
    concurrent = value.get('concurrent_writer_paths', [])
    if (not isinstance(concurrent, list) or any(not isinstance(p, str) for p in concurrent)
            or any(_owned(r['path'], concurrent) for r in rows)):
        refuse('invalid task attribution concurrent writer paths')
    _validate_conflict_records(rows)
    contents = value.get('contents')
    retained = {r['path']: r['worktree'] for r in rows if r['worktree']['type'] in {'file', 'symlink'}}
    if not isinstance(contents, dict) or set(contents) != set(retained):refuse('incomplete retained attribution content')
    progress.report('attribution-history', count=0)
    for count,(path, work) in enumerate(retained.items(),1):
        try:raw = A.read(contents[path])
        except (OSError, ValueError, KeyError, TypeError):refuse('unreadable retained attribution content')
        expected = hashlib.sha256(os.fsencode(work['text'])).hexdigest() if work['type'] == 'symlink' else work['sha256']
        if A.digest(raw) != expected:refuse('retained attribution content differs')
        progress.report('attribution-history', count=count)
    progress.report('attribution-history', count=len(retained), force=True)
    return value


def _map(capture):
    return {r['path']: r for r in capture['files']}


def _changes(before, after):
    return sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))


def _contribution_changes(before, after):
    """Changed records of one contribution, without paths a sibling writer owned meanwhile.

    Either capture may leave out an ACTIVE sibling's ownership; those paths are the sibling's
    to attribute, so their presence in only one capture is not a change of this writer."""
    concurrent = sorted(set(before.get('concurrent_writer_paths', [])) | set(after.get('concurrent_writer_paths', [])))
    return [p for p in _changes(_map(before), _map(after)) if not _owned(p, concurrent)]


def _owned(path, files):
    return any(path == p or path.startswith(p.rstrip('/') + '/') for p in files)


def capture_attribution(st):
    """Called only during a genuinely new start, never on load or resume."""
    if 'task_attribution' in st:
        refuse('task attribution cannot be replaced')
    capture = observe(st, retain_content=True)
    data = {'schema_version': 1, 'kind': 'task-attribution-v1', 'binding': _binding(st),
            'task_hash': al_task.revision_hashes(st)[0], 'captured_requirement_hash': al_task.task_hash(st),
            'baseline_capture_ref': _save(st, capture, capture['kind']),
            'baseline_changed_paths': S.changed_paths(st['identity']['merge_base']),
            'admitted_paths': [], 'contributions': []}
    st['task_attribution'] = {'snapshot': _save(st, data, data['kind']), 'contributions': [], 'reconciliations': []}
    return st['task_attribution']['snapshot']


def _concurrent(st, writer):
    concurrent = concurrent_paths(st, writer['id'])
    from al_parallel import overlaps
    if concurrent and overlaps(writer['files'], concurrent):
        refuse('writer ' + writer['id'] + ' ownership overlaps an active sibling writer')
    return concurrent


def begin_contribution(st, writer):
    if 'task_attribution' not in st:
        return None
    _snapshot(st)
    capture = observe(st, retain_content=True, concurrent=_concurrent(st, writer))
    data = {'schema_version': 1, 'kind': 'task-contribution-admission-v1', 'binding': _binding(st),
            'writer_id': writer['id'], 'task_hash': al_task.task_hash(st), 'ownership_paths': writer['files'],
            'capture_ref': _save(st, capture, 'task-content-capture-v1')}
    return _save(st, data, data['kind'])


def finish_contribution(st, writer, before_ref):
    if 'task_attribution' not in st:
        return
    if before_ref is None:
        refuse('missing admitted task contribution capture')
    admission = _read(st, before_ref, 'task-contribution-admission-v1')
    if admission['writer_id'] != writer['id'] or admission['ownership_paths'] != writer['files']:
        refuse('task contribution admission differs')
    before = _capture(st, admission['capture_ref'])
    after = observe(st, retain_content=True, concurrent=_concurrent(st, writer))
    data = {'schema_version': 1, 'kind': 'task-contribution-v1', 'binding': _binding(st),
            'task_hash': admission['task_hash'], 'ended_task_hash': al_task.task_hash(st), 'writer_id': writer['id'],
            'ownership_paths': writer['files'], 'outcome': writer['outcome'],
            'admission_ref': before_ref, 'before_capture_ref': admission['capture_ref'], 'after_capture_ref': _save(st, after, after['kind']),
            'changed_records': _contribution_changes(before, after)}
    ref = _save(st, data, data['kind'])
    st['task_attribution']['contributions'].append(ref)
    return ref


def _snapshot(st):
    value = st['task_attribution']
    if not isinstance(value, dict) or set(value) - {'snapshot', 'contributions', 'reconciliations'} or not {'snapshot', 'contributions'} <= set(value) or not isinstance(value['contributions'], list) or not isinstance(value.get('reconciliations', []), list):
        refuse('corrupt task attribution registry')
    snapshot = _read(st, value['snapshot'], 'task-attribution-v1')
    if (snapshot['task_hash'] != al_task.revision_hashes(st)[0]
            or snapshot.get('captured_requirement_hash',snapshot['task_hash']) not in al_task.revision_hashes(st)):
        refuse('task attribution original revision mismatch')
    return snapshot


def _content(row):
    return None if row is None else {k: row.get(k) for k in ('worktree', 'physical_mode', 'child')}


def _content_changes(before, after):
    return sorted(p for p in before.keys() | after.keys() if _content(before.get(p)) != _content(after.get(p)))


def _cause(causes, path, cause, *writers):
    if causes is not None:
        causes.setdefault(path, []).append({'cause': cause, 'writers': [w for w in writers if w]})


def _replay(st, expected, owned, ambiguous, *, content_only=False, causes=None):
    """Replay admitted contributions.

    Ownership compares content (worktree bytes/type, physical mode, gitlink child), never
    index or HEAD identity: staging or committing a writer's result between sequential
    writers is no concurrent edit. `causes` (optional) collects why each path is ambiguous."""
    row = _content if content_only else lambda value: value
    comparison = _content_changes if content_only else _changes
    seen = set()
    from al_task_reconciliation import Replay
    replay = Replay(st, expected, owned, ambiguous)
    revisions = set(al_task.revision_hashes(st))
    last = {}
    progress.report('attribution-replay', count=0)
    for index, ref in enumerate(st['task_attribution']['contributions']):
        replay.apply(index)
        c = _read(st, ref, 'task-contribution-v1')
        writer = st['writers'].get(c['writer_id'])
        if c['writer_id'] in seen or not writer or c['ownership_paths'] != writer['files'] or c['task_hash'] not in revisions or writer['state'] not in {'FINISHED', 'UNRESOLVED', 'RECONCILED'} or c['outcome'] != writer.get('outcome'):
            refuse('invalid task contribution writer provenance')
        seen.add(c['writer_id'])
        admission = _read(st, c['admission_ref'], 'task-contribution-admission-v1')
        if any(admission[k] != c[k] for k in ('writer_id', 'ownership_paths', 'task_hash')) or admission['capture_ref'] != c['before_capture_ref'] or c['ended_task_hash'] not in revisions:
            refuse('task contribution admission provenance differs')
        before, after = _capture(st, c['before_capture_ref']), _capture(st, c['after_capture_ref'])
        changes = _contribution_changes(before, after)
        before, after = _map(before), _map(after)
        if changes != c['changed_records']:
            refuse('invalid task contribution records')
        for path in comparison(before, after):
            if not _owned(path, writer['files']):
                continue
            if row(before.get(path)) != row(expected.get(path)):
                ambiguous.add(path)
                _cause(causes, path, 'changed-before-writer: the content differed from the last attributed '
                       'content when this writer started (an unregistered edit, or a change by '
                       + ('writer ' + last[path] if path in last else 'no registered writer') + ')',
                       last.get(path), c['writer_id'])
            elif c['outcome'] != 'success':
                ambiguous.add(path)
                _cause(causes, path, 'writer-outcome-' + c['outcome'] + ': the writer did not finish successfully', c['writer_id'])
            elif c['task_hash'] != c['ended_task_hash']:
                ambiguous.add(path)
                _cause(causes, path, 'task-revised-during-writer: the task was amended while the writer ran', c['writer_id'])
            else:
                owned.add(path)
            last[path] = c['writer_id']
            if path in after: expected[path] = after[path]
            else: expected.pop(path, None)
        progress.report('attribution-replay', count=index+1)
    replay.apply(len(st['task_attribution']['contributions']))
    replay.finish()
    progress.report('attribution-replay', count=len(st['task_attribution']['contributions']), force=True)
    if causes is not None:
        causes['__last_writer__'] = last
    return replay.external


@S.decision
def classify_changes(st, current_capture=None):
    return _classify(st, current_capture)


@S.decision
def explain_ambiguity(st):
    """Which paths overlap, which writers touched them, and why (for `recovery next`)."""
    causes = {}
    result = _classify(st, None, causes)
    last = causes.pop('__last_writer__', {})
    rows = []
    for path in result['ambiguous']:
        reasons = causes.get(path) or []
        writers = sorted({w for r in reasons for w in r['writers']} | ({last[path]} if path in last else set()))
        rows.append({'path': path, 'writers': writers, 'causes': [r['cause'] for r in reasons]})
    return {'classification': result, 'overlaps': rows}


def _classify(st, current_capture=None, causes=None):
    if 'inherited_lineage' in st:
        from al_inherited_lineage import classify
        return classify(st, current_capture)
    if 'task_attribution' not in st:
        return {'attributed': False, 'task_owned': [], 'unchanged_preexisting': [],
                'externally_changed': [], 'ambiguous': [], 'overlapping_or_ambiguous': [],
                'limitations': ['historical run has no start attribution snapshot'],
                'discovery_paths': S.changed_paths(st['identity']['merge_base'])}
    snapshot = _snapshot(st)
    baseline = _map(_capture(st, snapshot['baseline_capture_ref']))
    expected = dict(baseline)
    owned, ambiguous = set(), set()
    external_reconciled = _replay(st, expected, owned, ambiguous, content_only=True, causes=causes)
    current = _map(current_capture or observe(st))
    limitations = S.child_content_gaps({'files': list(current.values())}) + S.unresolved_index_gaps({'files': list(current.values())})
    children = {r['path'] for r in current.values() if r.get('child') and (r['child'].get('dirty') or r['child']['state'] == 'inspection-failed')}
    for path in children:
        _cause(causes, path, 'uncaptured-gitlink-child: the submodule is dirty or cannot be inspected; '
               'explicit reconciliation cannot clear it (commit or clean the child, or run AL rooted in it)')
    ambiguous.update(children)
    external = set(_content_changes(expected, current)) | (external_reconciled - owned)
    for path in external & owned:
        _cause(causes, path, 'changed-after-writer: the content changed after its last registered writer '
               'finished (an unregistered edit, formatter or generator)')
    ambiguous.update(external & owned)
    owned -= ambiguous | external
    # Contributions that reverted to start content are no longer task changes.
    owned &= set(_content_changes(baseline, current))
    preexisting = {p for p in snapshot['baseline_changed_paths'] if _content(baseline.get(p)) == _content(current.get(p))} - owned - ambiguous
    result = {'attributed': True, 'task_owned': sorted(owned), 'unchanged_preexisting': sorted(preexisting),
              'externally_changed': sorted(external - ambiguous), 'ambiguous': sorted(ambiguous),
              'overlapping_or_ambiguous': sorted(ambiguous), 'limitations': limitations}
    result['discovery_paths'] = sorted(owned | external | ambiguous)
    return result
