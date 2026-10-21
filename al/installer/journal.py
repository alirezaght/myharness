"""Crash-visible outer journals and guarded ancillary actions."""
import uuid
from pathlib import Path
from .engine import D, R, T
from .registry import digest, namespace, read_json, record_path, write_json


def allocate(state_root, target_id, kind, before):
    operation = uuid.uuid4().hex
    directory = namespace(state_root) / 'operations' / operation
    R.safe_absolute(directory)
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / 'journal.json'
    journal = {'schema_version': 1, 'id': operation, 'target_id': target_id, 'kind': kind,
               'status': 'PREPARED', 'before': before, 'candidate': None,
               'engine_steps': [], 'ancillary_actions': [], 'directories': []}
    return directory, path, journal


def checkpoint(journal, path, status):
    journal['status'] = status
    write_json(path, journal)


def action(directory, path, raw, mode=0o644, *, kind='ancillary'):
    path = R.safe_absolute(path)
    before = T.state(path)
    after = None
    source = None
    if raw is not None:
        after = {'sha256': digest(raw), 'mode': mode}
        source = directory / 'postimages' / digest(str(path).encode())
        D.atomic(source, raw, mode)
    if before == after:
        return None
    return {'action': 'delete' if after is None else ('replace' if before else 'create'),
            'target_path': str(path), 'source_path': str(source) if source else None,
            'source': after, 'target': before,
            'backup': str(directory / 'backups' / digest(str(path).encode())) if before else None,
            'kind': kind, 'status': 'PENDING'}


def execute_ancillary(item, journal, path):
    T.execute_action(item, journal, path)


def ensure_directory(directory, journal, path):
    directory = R.safe_absolute(directory)
    missing = []
    current = directory
    while not current.exists():
        missing.append(current)
        current = current.parent
    if not current.is_dir():
        raise ValueError('destination ancestor is not a directory')
    for entry in reversed(missing):
        row = {'path': str(entry), 'status': 'PENDING'}
        journal['directories'].append(row)
        write_json(path, journal)
        R.safe_absolute(entry)
        entry.mkdir(exist_ok=False)
        row['status'] = 'CREATED'
        write_json(path, journal)


def publish_completed(row, candidate, journal, path, state_root):
    checkpoint(journal, path, 'VERIFIED')
    checkpoint(journal, path, 'COMPLETE')
    snapshot = dict(candidate, journal_sha256=D.sha(path))
    result = dict(row, current=snapshot, previous=row.get('current'), pending=None,
                  policy=candidate['policy'])
    write_json(record_path(state_root, row['target_id']), result)
    return result


def publication_succeeded(state_root, target_id, operation):
    try:
        row = read_json(record_path(state_root, target_id))
        return (not row.get('pending') and row.get('current', {}).get('operation') == operation)
    except (OSError, ValueError, AttributeError):
        return False


def validate_backups(journal):
    for step in journal.get('engine_steps', []):
        engine_path = R.safe_absolute(Path(step['backup']) / 'distribution-journal.json')
        if step.get('status') != 'COMPLETE' or D.sha(engine_path) != step.get('journal_sha256'):
            raise ValueError('retained engine journal integrity mismatch')
        engine = read_json(engine_path)
        if engine.get('status') != 'COMPLETE' or engine.get('manifest') != step['manifest']:
            raise ValueError('retained engine journal is incomplete or changed')
        expected = T.actions(step['manifest'], Path(step['backup']))
        if [dict(item, status='PENDING') for item in engine.get('actions', [])] != expected:
            raise ValueError('retained engine action accounting mismatch')
        for item in engine.get('actions', []):
            if item.get('status') != 'APPLIED':
                raise ValueError('retained engine action is incomplete')
            if item['target'] and T.state(R.safe_absolute(item['backup'])) != item['target']:
                raise ValueError('retained engine backup is corrupt')
    for item in journal.get('ancillary_actions', []):
        if item.get('status') != 'APPLIED':
            raise ValueError('retained ancillary action is incomplete')
        if item['target'] and T.state(R.safe_absolute(item['backup'])) != item['target']:
            raise ValueError('retained ancillary backup is corrupt')
