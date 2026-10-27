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
    from al_gates import gate_result
    return gate_result(st, manifest, gate, stage, fingerprint)


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
        if status in {'not-required', 'unresolved', 'unverified'}:continue
        if requested_id or status in {'missing', 'stale', 'blocked'} or (retry_failed and status == 'failed'):
            selected.append(gate)
    return selected


def stage_report(st, stage, selected_results=(), selected=()):
    from al_gates import manifest
    current = manifest(st)
    states, gaps = _states(st, current, stage)
    import al_baseline_exceptions as Exceptions
    decisions = {}
    for g, s in states.items():
        if s != 'accepted-base-decision':continue
        gate = next(item for item in current['gates'] if item['id'] == g)
        exception = Exceptions.current(st, current, gate)
        decisions[g] = ({'action': 'exception', 'source': exception['source'], 'reason': exception['reason'],
                         'evidence': exception['binding']['evidence']} if exception else st.get('gate_decisions', {}).get(g))
    uncertain = ['operation:'+e['id'] for e in st['evidence']['entries'] if _uncertain(e)]
    return {'schema_version': 1, 'kind': 'continuation-report-v1',
            'selected': list(selected), 'completed': [r for r in selected_results if r['status'] == 'PASS'],
            'retained_valid': [g for g, s in states.items() if s == 'valid' and g not in selected],
            'accepted_existing_failures': [g for g,s in states.items() if s=='accepted-existing-failure'],
            'retained_failed': [g for g, s in states.items() if s == 'failed'],
            'unresolved': [g for g, s in states.items() if s == 'unresolved']+uncertain,
            'unverified': [g for g, s in states.items() if s == 'unverified'],
            'blocked': [g for g,s in states.items() if s=='blocked'],
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
        if not context.get('explicit') and (status in {'valid', 'accepted-base-decision', 'accepted-existing-failure'} or (status == 'failed' and not context.get('retry_failed'))):
            raise DestinationSettled(gate['id'])
    return _identity(context['manifest'], context['stage'], context['gate'])
