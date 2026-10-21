"""Bounded continuation keeps failures visible and reserves each destination once."""
import al_ledger as L
import al_scope as S
from al_schema import refuse


class DestinationSettled(Exception):
    """A concurrent invocation completed work after continuation selection."""


def _identity(manifest_sha, stage, gate):
    return {'manifest': manifest_sha, 'stage': stage, 'gate': gate}


def _uncertain(entry):
    cleanup = entry.get('cleanup') or {}
    return bool(cleanup.get('unresolved') or cleanup.get('errors') or cleanup.get('complete') is False)


def _attempt(st, stage, gate):
    sha = st['gate_manifests'][-1]['sha256']
    identity = _identity(sha, stage, gate['id'])
    runs = {r['evidence']: r for r in st.get('gate_runs', [])
            if all(r.get(k) == v for k, v in identity.items())}
    entries = [e for e in st['evidence']['entries'] if e['id'] in runs or e.get('gate_reservation') == identity]
    if not entries:return None, None
    entry = max(entries, key=lambda e: int(e['id'][1:]))
    return entry, runs.get(entry['id'])


def _state(st, manifest, stage, gate, gaps, fingerprint):
    if gate['applicability'] == 'NOT_APPLICABLE':return 'not-required'
    if gate['applicability'] != 'APPLICABLE':return 'unresolved'
    entry, run = _attempt(st, stage, gate)
    if entry is None:return 'missing'
    if _uncertain(entry) or entry['status'] == 'RUNNING' or (entry.get('interrupted') and not entry.get('reconciliation')):
        return 'unresolved'
    if stage+' gate '+gate['id']+' requires passing evidence' not in gaps:
        return 'accepted-base-decision' if entry['status'] == 'FAIL' else 'valid'
    same = bool(run and run.get('fingerprint') == fingerprint and entry.get('run') == st['run_id']
                and run.get('env_fingerprint') == L.scope_fingerprint([], gate['env_names'])
                and entry.get('argv') == gate['argv'] and entry.get('cwd') == gate['cwd']
                and entry.get('scope') == gate['scope'] and entry.get('env') == sorted(gate['env_names'])
                and entry.get('sources', []) == gate.get('sources', [])
                and run.get('sources', []) == gate.get('sources', [])
                and run.get('execution_kind') == gate['execution_kind'])
    if same and entry['status'] == 'FAIL' and L.entry_verdict(st, entry)[0] == 'FAIL':
        return 'failed'
    return 'stale'


def _states(st, manifest, stage):
    from al_gates import gate_gaps
    gaps = gate_gaps(st, stage)
    fingerprint = S.capture_candidate(st)['fingerprint'] if stage == 'candidate' else manifest['base_oid']
    return {g['id']: _state(st, manifest, stage, g, gaps, fingerprint) for g in manifest['gates']}, gaps


def select_continuation(st, manifest, stage, requested_id=None, retry_failed=False):
    if st['status'] != 'RUNNING':refuse('gate continuation requires a running run')
    if requested_id and requested_id not in {g['id'] for g in manifest['gates']}:refuse('unknown gate ID')
    states, _ = _states(st, manifest, stage)
    selected = []
    for gate in manifest['gates']:
        name = gate['id']; status = states[name]
        if requested_id and name != requested_id:continue
        if status in {'not-required', 'unresolved'}:continue
        if requested_id or status in {'missing', 'stale'} or (retry_failed and status == 'failed'):
            selected.append(gate)
    return selected


def stage_report(st, stage, selected_results=(), selected=()):
    from al_gates import manifest
    current = manifest(st)
    states, gaps = _states(st, current, stage)
    decisions = {g: st.get('gate_decisions', {}).get(g) for g, s in states.items() if s == 'accepted-base-decision'}
    uncertain = ['operation:'+e['id'] for e in st['evidence']['entries'] if _uncertain(e)]
    return {'schema_version': 1, 'kind': 'continuation-report-v1',
            'selected': list(selected), 'completed': [r for r in selected_results if r['status'] == 'PASS'],
            'retained_valid': [g for g, s in states.items() if s == 'valid' and g not in selected],
            'retained_failed': [g for g, s in states.items() if s == 'failed'],
            'unresolved': [g for g, s in states.items() if s == 'unresolved']+uncertain,
            'unexecuted': [g for g, s in states.items() if s in {'missing', 'stale'}],
            'base_decisions': decisions, 'stage_ready': not gaps and not uncertain and not any(s == 'unresolved' for s in states.values())}


def admit_destination(st, context):
    """Called only inside the existing atomic evidence reservation lock."""
    from al_gates import manifest
    uncertain = [e['id'] for e in st['evidence']['entries'] if _uncertain(e)]
    if uncertain:refuse('owned operation cleanup is unresolved: '+', '.join(uncertain))
    current = manifest(st)
    if not current or st['gate_manifests'][-1]['sha256'] != context['manifest']:
        refuse('gate destination manifest changed before reservation')
    gate = next((g for g in current['gates'] if g['id'] == context['gate']), None)
    if not gate:refuse('gate destination disappeared before reservation')
    entry, _ = _attempt(st, context['stage'], gate)
    if entry and (entry['status'] == 'RUNNING' or _uncertain(entry) or (entry.get('interrupted') and not entry.get('reconciliation'))):
        refuse('gate destination has unresolved reservation '+entry['id'])
    if context.get('resume'):
        states, _ = _states(st, current, context['stage'])
        status = states[gate['id']]
        if status == 'unresolved':refuse('gate destination is unresolved')
        if not context.get('explicit') and (status in {'valid', 'accepted-base-decision'} or (status == 'failed' and not context.get('retry_failed'))):
            raise DestinationSettled(gate['id'])
    return _identity(context['manifest'], context['stage'], context['gate'])
