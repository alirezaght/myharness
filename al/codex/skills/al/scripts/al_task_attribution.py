"""Content-based task contributions; absence of provenance never narrows discovery."""
import json
import hashlib
import os
import stat
import al_artifacts as A
import al_scope as S
import al_store
import al_task
from al_schema import refuse


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


def observe(st, *, retain_content=False, expected=None):
    """Double observation includes non-executable mode changes and new membership.

    A re-observation compared against an `expected` capture that was itself stable needs one
    read: equality with a stable observation is the stability proof, and any difference is
    refused by the caller's comparison."""
    first = _rows(st)
    if expected is None or retain_content or first != expected.get('files'):
        second = _rows(st)
        if first != second:
            refuse('unstable task attribution observation')
    else:
        second = first
    capture = {'schema_version': 1, 'kind': 'task-content-capture-v1',
               'binding': _binding(st), 'files': second}
    if retain_content:
        capture['contents'] = {}
        for row in second:
            work = row['worktree']; path = al_store.REPO_ROOT / S.safe_path(row['path'])
            if work['type'] not in {'file', 'symlink'}:continue
            raw = os.fsencode(os.readlink(path)) if work['type'] == 'symlink' else path.read_bytes()
            expected = hashlib.sha256(os.fsencode(work['text'])).hexdigest() if work['type'] == 'symlink' else work['sha256']
            if hashlib.sha256(raw).hexdigest() != expected:refuse('source moved during attribution retention')
            capture['contents'][row['path']] = A.snapshot_bytes(st['session_id'], raw, 'task-source-content-v1', row['path'])
        if _rows(st) != second:refuse('source moved during attribution retention')
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


def _capture(st, ref):
    value = _read(st, ref, 'task-content-capture-v1')
    rows = value.get('files')
    if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get('path'), str) for r in rows):
        refuse('invalid task attribution capture')
    if len({r['path'] for r in rows}) != len(rows):
        refuse('duplicate task attribution member')
    contents = value.get('contents')
    retained = {r['path']: r['worktree'] for r in rows if r['worktree']['type'] in {'file', 'symlink'}}
    if not isinstance(contents, dict) or set(contents) != set(retained):refuse('incomplete retained attribution content')
    for path, work in retained.items():
        try:raw = A.read(contents[path])
        except (OSError, ValueError, KeyError, TypeError):refuse('unreadable retained attribution content')
        expected = hashlib.sha256(os.fsencode(work['text'])).hexdigest() if work['type'] == 'symlink' else work['sha256']
        if A.digest(raw) != expected:refuse('retained attribution content differs')
    return value


def _map(capture):
    return {r['path']: r for r in capture['files']}


def _changes(before, after):
    return sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))


def _owned(path, files):
    return any(path == p or path.startswith(p.rstrip('/') + '/') for p in files)


def capture_attribution(st):
    """Called only during a genuinely new start, never on load or resume."""
    if 'task_attribution' in st:
        refuse('task attribution cannot be replaced')
    capture = observe(st, retain_content=True)
    data = {'schema_version': 1, 'kind': 'task-attribution-v1', 'binding': _binding(st),
            'task_hash': al_task.task_hash(st), 'baseline_capture_ref': _save(st, capture, capture['kind']),
            'baseline_changed_paths': S.changed_paths(st['identity']['merge_base']),
            'admitted_paths': [], 'contributions': []}
    st['task_attribution'] = {'snapshot': _save(st, data, data['kind']), 'contributions': [], 'reconciliations': []}
    return st['task_attribution']['snapshot']


def begin_contribution(st, writer):
    if 'task_attribution' not in st:
        return None
    _snapshot(st)
    data = {'schema_version': 1, 'kind': 'task-contribution-admission-v1', 'binding': _binding(st),
            'writer_id': writer['id'], 'task_hash': al_task.task_hash(st), 'ownership_paths': writer['files'],
            'capture_ref': _save(st, observe(st, retain_content=True), 'task-content-capture-v1')}
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
    after = observe(st, retain_content=True)
    data = {'schema_version': 1, 'kind': 'task-contribution-v1', 'binding': _binding(st),
            'task_hash': admission['task_hash'], 'ended_task_hash': al_task.task_hash(st), 'writer_id': writer['id'],
            'ownership_paths': writer['files'], 'outcome': writer['outcome'],
            'admission_ref': before_ref, 'before_capture_ref': admission['capture_ref'], 'after_capture_ref': _save(st, after, after['kind']),
            'changed_records': _changes(_map(before), _map(after))}
    ref = _save(st, data, data['kind'])
    st['task_attribution']['contributions'].append(ref)
    return ref


def _snapshot(st):
    value = st['task_attribution']
    if not isinstance(value, dict) or set(value) - {'snapshot', 'contributions', 'reconciliations'} or not {'snapshot', 'contributions'} <= set(value) or not isinstance(value['contributions'], list) or not isinstance(value.get('reconciliations', []), list):
        refuse('corrupt task attribution registry')
    snapshot = _read(st, value['snapshot'], 'task-attribution-v1')
    if snapshot['task_hash'] != al_task.revision_hashes(st)[0]:
        refuse('task attribution original revision mismatch')
    return snapshot


def classify_changes(st, current_capture=None):
    if 'task_attribution' not in st:
        return {'attributed': False, 'task_owned': [], 'unchanged_preexisting': [],
                'externally_changed': [], 'ambiguous': [], 'overlapping_or_ambiguous': [],
                'limitations': ['historical run has no start attribution snapshot'],
                'discovery_paths': S.changed_paths(st['identity']['merge_base'])}
    snapshot = _snapshot(st)
    baseline = _map(_capture(st, snapshot['baseline_capture_ref']))
    expected = dict(baseline)
    owned, ambiguous = set(), set()
    seen = set()
    from al_task_reconciliation import Replay
    replay = Replay(st, expected, owned, ambiguous)
    revisions = set(al_task.revision_hashes(st))
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
        before, after = _map(_capture(st, c['before_capture_ref'])), _map(_capture(st, c['after_capture_ref']))
        changes = _changes(before, after)
        if changes != c['changed_records']:
            refuse('invalid task contribution records')
        for path in changes:
            if not _owned(path, writer['files']):
                continue
            if before.get(path) != expected.get(path) or c['outcome'] != 'success' or c['task_hash'] != c['ended_task_hash']:
                ambiguous.add(path)
            else:
                owned.add(path)
            if path in after: expected[path] = after[path]
            else: expected.pop(path, None)
    replay.apply(len(st['task_attribution']['contributions']))
    replay.finish()
    current = _map(current_capture or observe(st))
    limitations = S.child_content_gaps({'files': list(current.values())})
    ambiguous.update(r['path'] for r in current.values() if r.get('child') and (r['child'].get('dirty') or r['child']['state'] == 'inspection-failed'))
    external = set(_changes(expected, current)) | (replay.external - owned)
    ambiguous.update(external & owned)
    owned -= ambiguous | external
    # Contributions that reverted to start content are no longer task changes.
    owned &= set(_changes(baseline, current))
    preexisting = {p for p in snapshot['baseline_changed_paths'] if baseline.get(p) == current.get(p)} - owned - ambiguous
    result = {'attributed': True, 'task_owned': sorted(owned), 'unchanged_preexisting': sorted(preexisting),
              'externally_changed': sorted(external - ambiguous), 'ambiguous': sorted(ambiguous),
              'overlapping_or_ambiguous': sorted(ambiguous), 'limitations': limitations}
    result['discovery_paths'] = sorted(owned | external | ambiguous)
    return result
