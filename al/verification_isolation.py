"""Reviewed subprocess grouping requires the complete current source closure."""
from al.verification_profile import digest

SPLIT_FILES = frozenset({'tests/test_runtime_batch13_remote.py', 'tests/test_runtime_batch13_checkout.py',
                         'tests/test_runtime_batch6.py', 'tests/test_gate_reuse.py'})
PROOF_SCOPE = 'complete-source-except-obligations-v1'


def closure_proof(binding):
    files = {path: row for path, row in binding['files'].items() if path != 'al/verification-obligations.json'}
    return {'version': 1, 'scope': PROOF_SCOPE, 'members': len(files), 'digest': digest(files)}


def isolation_current(declarations, binding):
    return declarations.get('isolation_closure') == closure_proof(binding)


def chunk_size(declarations, suite, file, isolated):
    if not isolated or suite not in {'codex', 'claude'} or file not in SPLIT_FILES:
        return None
    row = declarations.get('node_groups', {}).get(suite + ':' + file, {})
    size = row.get('max_nodes')
    if type(size) is not int or not 1 <= size <= 32 or not row.get('reason', '').strip():
        return None
    return size
