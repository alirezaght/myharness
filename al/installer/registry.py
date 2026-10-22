"""External installer metadata, separate from distribution ownership."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import threading
from .engine import D, R, T


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_json(path):
    path = R.safe_absolute(path)
    if not path.is_file():
        raise ValueError('missing regular JSON file: ' + str(path))
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('JSON size limit exceeded: ' + str(path))
    return json.loads(path.read_bytes(), object_pairs_hook=pairs)


def write_json(path, data):
    R.safe_absolute(path)
    D.atomic(Path(path), canonical(data) + b'\n', 0o600)


def namespace(state_root):
    return R.safe_absolute(state_root) / 'installer'


def record_path(state_root, target_id):
    if not isinstance(target_id, str) or len(target_id) != 64 or any(c not in '0123456789abcdef' for c in target_id):
        raise ValueError('invalid installation identity')
    return namespace(state_root) / 'records' / (target_id + '.json')


def load_record(state_root, target_id):
    path = record_path(state_root, target_id)
    if not path.exists():
        return None
    row = read_json(path)
    if row.get('schema_version') != 1 or row.get('target_id') != target_id:
        raise ValueError('installer record identity/schema mismatch: ' + str(path))
    if row.get('policy') not in {'follow', 'pinned'} or not isinstance(row.get('target'), dict):
        raise ValueError('malformed installer record: ' + str(path))
    target = row['target']
    if target.get('scope') not in {'global', 'repo'} or not isinstance(target.get('root'), str) or not Path(target['root']).is_absolute():
        raise ValueError('invalid registered target')
    if target.get('target_id') != target_id or not isinstance(target.get('platforms'), list) or not target['platforms'] or set(target['platforms']) - {'codex', 'claude'} or len(set(target['platforms'])) != len(target['platforms']):
        raise ValueError('invalid registered target platforms/identity')
    expected = digest(canonical({'scope': target['scope'], 'root': target['root']}))
    if expected != target_id:
        raise ValueError('registered target identity digest mismatch')
    if row.get('current') and (row['current'].get('policy') != row['policy'] or row.get('source') != {'identity': row['current']['release']['source_identity'], 'subdir': row['current']['release']['subdir']}):
        raise ValueError('registered policy/source differs from completed version')
    return row


def records(state_root):
    root = namespace(state_root) / 'records'
    R.safe_absolute(root)
    if not root.exists():
        return []
    rows = [load_record(state_root, path.stem) for path in sorted(root.glob('*.json'))]
    occupied = set()
    for row in rows:
        target = row['target']
        for platform in target['platforms']:
            path = str(Path(target['root']) / ('.' + platform) / 'skills/al')
            if path in occupied:
                raise ValueError('overlapping installation records: ' + path)
            occupied.add(path)
    return rows


def legacy_records(state_root):
    root = R.safe_absolute(state_root)
    result = []
    if not root.exists():
        return result
    enrolled = {str(Path(row['target']['root']) / ('.' + p) / 'skills/al')
                for row in records(state_root) for p in row['target']['platforms']}
    for path in sorted(root.glob('*.json')):
        row = read_json(path)
        if row.get('schema_version') == 1 and row.get('target') and row.get('platform') in {'codex', 'claude'} and row['target'] not in enrolled:
            result.append({'target': row['target'], 'platform': row['platform'],
                           'version': 'unknown', 'policy': 'legacy', 'eligible': False})
    return result


class Held:
    """Proof that this thread currently holds one state root's coordination lock."""
    __slots__ = ('root', 'pid', 'thread', 'active')

    def __init__(self, root):
        self.root, self.pid, self.thread, self.active = root, os.getpid(), threading.get_ident(), True


@contextlib.contextmanager
def coordination(state_root, *, held=None):
    root = namespace(state_root)
    R.safe_absolute(root)
    # flock locks belong to the open file description, so re-acquiring through a
    # second handle in the same process deadlocks; nested callers pass the token.
    if held is not None:
        if not (isinstance(held, Held) and held.active and held.root == root
                and held.pid == os.getpid() and held.thread == threading.get_ident()):
            raise ValueError('coordination token does not hold this state root')
        yield held
        return
    root.mkdir(parents=True, exist_ok=True)
    lock = R.safe_absolute(root / 'coordination.lock')
    with lock.open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        token = Held(root)
        try:
            yield token
        finally:
            token.active = False


def completed(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError('missing completed installation snapshot')
    path = R.safe_absolute(snapshot['journal'])
    if D.sha(path) != snapshot.get('journal_sha256'):
        raise ValueError('completed installer journal integrity mismatch: ' + str(path))
    journal = read_json(path)
    candidate = {k: v for k, v in snapshot.items() if k != 'journal_sha256'}
    if journal.get('status') != 'COMPLETE' or journal.get('candidate') != candidate:
        raise ValueError('installation is not linked to a completed journal')
    return journal


def reject_pending(row, state_root):
    if row and row.get('pending'):
        raise ValueError('unresolved installer operation: ' + row['pending'] + '; inspect journal/backups; do not retry')
    if row and row.get('current'):
        completed(row['current'])
    target_id = row['target_id'] if row else None
    if target_id:
        for path in (namespace(state_root) / 'operations').glob('*/journal.json'):
            journal = read_json(path)
            if journal.get('target_id') == target_id and journal.get('status') != 'COMPLETE':
                raise ValueError('incomplete/orphan installer journal: ' + str(path))
