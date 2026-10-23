"""Explicitly authorized, auditable exceptions for an existing unchanged-base gate failure.

An exception keeps the failed evidence FAIL and only lets base readiness treat that one exact
failure as accepted for implementation admission; the repair stays outside the task. It binds the
run, pinned base, manifest, gate command, declared environment, failed evidence, log and execution
identity, so any drift makes it stale. Candidate gates are unaffected and still report failures."""
import hashlib
import json
import time
from pathlib import Path

import al_artifacts as A
import al_ledger as L
from al_schema import refuse

KIND = 'baseline-exception-v1'


def _latest_base(st, gate):
    sha = st['gate_manifests'][-1]['sha256']
    runs = [r for r in st.get('gate_runs', []) if r['gate'] == gate['id'] and r['stage'] == 'base' and r['manifest'] == sha]
    if not runs:return None, None
    return runs[-1], L.find(st, runs[-1]['evidence'])


def binding(st, manifest, gate):
    """The exact current failure an exception may name, or None when there is none."""
    run, entry = _latest_base(st, gate)
    if not run or not entry or entry.get('status') != 'FAIL' or entry.get('run') != st['run_id']:return None
    if entry.get('gate_stage') != 'base' or entry.get('area') != 'gate-base-' + gate['id']:return None
    if manifest['base_oid'] != st['identity']['merge_base'] or run.get('fingerprint') != manifest['base_oid']:return None
    if not entry.get('log') or not Path(entry['log']).is_file() or L.file_sha(entry['log']) != entry.get('log_sha256'):return None
    command = {'argv': gate['argv'], 'cwd': gate['cwd'], 'scope': gate['scope'], 'sources': gate.get('sources', []),
               'execution_kind': gate['execution_kind']}
    if any(entry.get(k) != command[k] for k in ('argv', 'cwd', 'scope')) or entry.get('sources', []) != command['sources']:return None
    environment = L.scope_fingerprint([], gate['env_names'])
    if entry.get('env', []) != sorted(gate['env_names']) or run.get('env_fingerprint') != environment:return None
    key = entry.get('execution_key')
    return {'session': st['session_id'], 'run': st['run_id'], 'base_oid': manifest['base_oid'],
            'manifest': st['gate_manifests'][-1]['sha256'], 'gate': gate['id'], 'command': command,
            'env_names': sorted(gate['env_names']), 'env_fingerprint': environment,
            'evidence': entry['id'], 'reservation': entry.get('reservation'), 'exit': entry.get('exit'),
            'log_sha256': entry['log_sha256'],
            'execution_sha256': hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest() if isinstance(key, dict) else None}


def record(st, sid, manifest, gate, source, reason):
    if not isinstance(source, str) or not source.strip() or not isinstance(reason, str) or not reason.strip():
        refuse('baseline exception requires an explicit authorization source and reason')
    if st.get('status') != 'RUNNING':refuse('baseline exception requires a running run')
    if gate['applicability'] != 'APPLICABLE':refuse('baseline exception requires an applicable gate')
    bound = binding(st, manifest, gate)
    if bound is None:refuse('baseline exception requires the current exact failed base evidence of this gate')
    value = {'schema_version': 1, 'kind': KIND, 'decision': 'exception', 'source': source, 'reason': reason,
             'authorized_at': time.time(), 'binding': bound, 'repair_scope': 'outside-task'}
    reference = A.snapshot_bytes(sid, json.dumps(value, sort_keys=True).encode(), KIND, 'gate ' + gate['id'] + ' baseline exception')
    row = {'gate': gate['id'], 'evidence': bound['evidence'], 'artifact': reference}
    st.setdefault('baseline_exceptions', []).append(row)
    return row


def current(st, manifest, gate):
    """The latest exception for this gate when it still names the exact current failure."""
    rows = [r for r in st.get('baseline_exceptions', []) if isinstance(r, dict) and r.get('gate') == gate['id']]
    if not rows:return None
    row = rows[-1]
    try:
        value = json.loads(A.read(row['artifact']))
    except (OSError, ValueError, KeyError, TypeError, SystemExit):
        return None
    if not isinstance(value, dict) or value.get('kind') != KIND or value.get('decision') != 'exception' or row['artifact'].get('kind') != KIND:
        return None
    bound = binding(st, manifest, gate)
    if bound is None or value.get('binding') != bound or row.get('evidence') != bound['evidence']:return None
    return value


def view(st, manifest):
    """Exceptions shown separately from gate results; failed evidence stays FAIL."""
    out = []
    for row in st.get('baseline_exceptions', []):
        gate = next((g for g in manifest['gates'] if g['id'] == row.get('gate')), None) if manifest else None
        value = current(st, manifest, gate) if gate else None
        latest = gate is not None and [r for r in st['baseline_exceptions'] if r.get('gate') == gate['id']][-1] is row
        out.append({'gate': row.get('gate'), 'evidence': row.get('evidence'), 'evidence_status': 'FAIL',
                    'status': 'current' if value and latest else 'stale', 'artifact': row.get('artifact', {}).get('sha256'),
                    'source': value.get('source') if value else None, 'reason': value.get('reason') if value else None})
    return out
