"""Closed immutable accepted-source receipts; never raw log/report import authority."""

import copy

import al_artifacts as A
from al_execution_contract import canonical

PURPOSES = {'verification', 'unit', 'integration', 'security', 'ci', 'staging', 'build', 'lint'}


def prepare_results(st, previous=None):
    integration = st.get('integration')
    if integration is None:
        return
    from al_agent_exchange import consume_verification
    from al_ledger import entry_verdict

    results = integration.setdefault('incident_results', {})
    old = (previous or {}).get('integration', {})
    for name in ('incident_results', 'incident_reviews'):
        if any(integration.get(name, {}).get(key) != value for key, value in old.get(name, {}).items()):
            raise ValueError('accepted incident source history is immutable')
    for entry in st['evidence']['entries']:
        if entry['status'] == 'RUNNING' or not entry.get('ended_at') or (entry.get('kind') == 'preflight' and not entry['scope']):
            continue
        if entry['id'] in results:
            A.read(results[entry['id']])
            continue
        consume_verification(st, entry)
        purpose = entry.get('purpose', 'verification')
        if purpose not in PURPOSES:
            raise ValueError('unsupported registered check purpose')
        counts = entry.get('counts') or {}
        counts = {key: value for key, value in counts.items()
                  if key in {'passed', 'failed', 'skipped', 'errors', 'flaky', 'todo'}
                  and type(value) is int and 0 <= value <= 1000000000}
        value = {'schema_version': 1, 'source_kind': 'al_test_result',
                 'binding': copy.deepcopy(integration['binding']), 'result_id': entry['id'],
                 'status': entry['status'], 'counts': counts, 'purpose': purpose,
                 'validity_at_capture': entry_verdict(st, entry)[0]}
        results[entry['id']] = A.snapshot_bytes(st['session_id'], canonical(value).encode(),
                                               'accepted-ledger-result-v1', 'guarded terminal ledger result')


def review_accepted(st, reference, stage):
    from al_events import queue
    if 'integration' not in st:
        return
    st['integration'].setdefault('incident_reviews', {})[reference['sha256']] = copy.deepcopy(reference)
    queue(st, 'review.accepted', {'review_id': st['review']['id'], 'stage': stage,
                                 'reference': {key: reference[key] for key in ('kind', 'id', 'sha256')}})
