"""Positive provenance for runtime inputs also retained as immutable AL artifacts."""
import hashlib
from pathlib import Path

from .activity_profiles import decode
from .registry import canonical


def references(state, *, repository, runtime_root, members):
    candidates = [state.get('task_package')]
    candidates.extend(row.get('artifact') for row in state.get('amendments', []))
    candidates.extend(state.get('gate_manifests', []))
    result = []
    for reference in candidates:
        if not isinstance(reference, dict) or reference.get('kind') not in {'task-package', 'task-amendment', 'user-amendment', 'gate-manifest'}:continue
        source = reference.get('source')
        if not isinstance(source, str) or not source:continue
        path = Path(source)
        if not path.is_absolute():path = Path(repository['root']) / path
        path = path.resolve()
        try:relative = path.relative_to(runtime_root).as_posix()
        except ValueError:continue
        if relative not in members or len(Path(relative).parts) != 1:continue
        stored = Path(reference['stored_path'])
        try:stored_relative = stored.relative_to(runtime_root).as_posix()
        except ValueError:raise ValueError('referenced nonstate artifact outside runtime')
        if members.get(stored_relative, {}).get('sha256') != reference['sha256']:
            raise ValueError('referenced nonstate snapshot is corrupt')
        result.append({'path': str(path), 'reference': reference,
                       'owner_session': state['session_id'], 'owner_run': state['run_id']})
    return result


def recognize(path, raw, references):
    value = decode(raw)
    if not isinstance(value, dict) or any(k in value for k in ('session_id', 'status', 'phase', 'writers', 'evidence')):
        return None
    for source in references:
        if source['path'] != str(path):continue
        reference = source['reference']
        stored_raw = Path(reference['stored_path']).read_bytes()
        if hashlib.sha256(stored_raw).hexdigest() != reference['sha256']:
            raise ValueError('immutable nonstate reference moved')
        if canonical(decode(stored_raw)) != canonical(value):continue
        kind = reference['kind']
        valid = False
        if kind == 'task-package':
            valid = isinstance(value.get('accepted_spec'), str) and bool(value['accepted_spec'].strip())
        elif kind in {'task-amendment', 'user-amendment'}:
            valid = (set(value) == {'id', 'text', 'source', 'affected_criteria'} and isinstance(value.get('source'), str) and bool(value['source'].strip())
                     and isinstance(value.get('id'), str) and bool(value['id'])
                     and isinstance(value.get('text'), str) and bool(value['text'].strip())
                     and isinstance(value.get('affected_criteria'), list))
        elif kind == 'gate-manifest':
            valid = (value.get('schema_version') == 1 and isinstance(value.get('id'), str)
                     and type(value.get('revision')) is int and isinstance(value.get('gates'), list)
                     and isinstance(value.get('rules'), list) and isinstance(value.get('units'), list)
                     and isinstance(value.get('paths'), list) and isinstance(value.get('base_oid'), str))
        if valid:
            return {'classification': 'nonstate-artifact', 'profile': {'id': 'referenced-' + kind + '-v1', 'version': 1},
                    'historical_status': None, 'historical_phase': None, 'operation_facts': {}, 'errors': [],
                    'provenance': source}
    return None
