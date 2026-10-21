"""Refuse unresolved or unrecognized receipts before another verification run."""
import json
from pathlib import Path


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:raise ValueError('duplicate lifecycle field')
        result[key] = value
    return result


def _finished(path, *, collection):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('unsafe lifecycle receipt')
    raw = path.read_bytes()
    value = json.loads(raw, object_pairs_hook=_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite lifecycle field')))
    common = {'schema_version', 'status', 'owner_pid', 'cleanup', 'cause'}
    allowed = common | ({'assignment', 'ownership'} if collection else
                        {'run_id', 'source_digest', 'children', 'reconciliation', 'ended_at'})
    if not isinstance(value, dict) or set(value) - allowed or value.get('schema_version') != 1:
        raise ValueError('unknown lifecycle receipt')
    if value.get('status') != 'FINISHED':raise ValueError('unresolved lifecycle status')
    if type(value.get('owner_pid')) is not int or value['owner_pid'] <= 0:
        raise ValueError('missing lifecycle owner')
    cleanup = value.get('cleanup')
    if (not isinstance(cleanup, dict) or set(cleanup) - {'complete', 'errors', 'unresolved', 'secondary_signals', 'cause'}
            or cleanup.get('complete') is not True or cleanup.get('errors') != [] or cleanup.get('unresolved') != []):
        raise ValueError('unresolved lifecycle cleanup')
    if collection:
        if not isinstance(value.get('assignment'), str) or not value['assignment']:
            raise ValueError('missing collection assignment')
    elif (not isinstance(value.get('run_id'), str) or not value['run_id']
          or not isinstance(value.get('source_digest'), str) or not isinstance(value.get('children'), list)):
        raise ValueError('missing verification run binding')
    if path.read_bytes() != raw:raise ValueError('lifecycle receipt moved')


def require_finished_runs(artifact_root):
    root = Path(artifact_root)
    try:
        if root.is_symlink():raise ValueError('unsafe artifact root')
        runs = sorted(root.iterdir())
        if len(runs) > 10000:raise ValueError('verification run membership limit')
        for directory in runs:
            if directory.is_symlink() or not directory.is_dir() or not directory.name.startswith('run-'):
                raise ValueError('unknown verification run layout')
            members = sorted(directory.iterdir())
            if len(members) > 1000:raise ValueError('verification receipt membership limit')
            if not members:raise ValueError('missing verification lifecycle receipts')
            for member in members:
                if member.is_symlink():raise ValueError('unsafe verification boundary')
                if member.is_file():
                    if member.name != 'plan.json':raise ValueError('unknown lifecycle receipt layout')
                    continue
                collection = member.name in {'collect-installer', 'collect-codex', 'collect-claude'}
                if not collection and member.name != 'execution':
                    raise ValueError('unknown lifecycle directory layout')
                expected = 'process-journal.json' if collection else 'journal.json'
                unexpected = member / ('journal.json' if collection else 'process-journal.json')
                if unexpected.exists() or unexpected.is_symlink():raise ValueError('unknown lifecycle receipt layout')
                _finished(member / expected, collection=collection)
            if sorted(directory.iterdir()) != members:raise ValueError('verification run membership moved')
        if sorted(root.iterdir()) != runs:raise ValueError('verification run membership moved')
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError('unresolved owned verification run: ' + str(exc)) from exc
