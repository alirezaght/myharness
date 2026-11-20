"""Typed repair observations never create developer business decisions."""
import copy

import al_artifacts as A
import al_events as E
from al_execution_contract import validate_issue
from al_schema import refuse


def update(st, data):
    validate_issue(data)
    integration = st['integration']
    if data['observed_binding'] != integration['binding']:
        refuse('foreign operational issue binding')
    if not set(data['task_ids']) <= integration['tasks'].keys():
        refuse('unknown operational issue task')
    if data['paths']:
        from al_writers import canonical_paths
        canonical_paths(data['paths'])
    from al_execution_tasks import authority, _overlap
    for task_id in data['task_ids']:
        authority(st, 'revalidate', task_id)
    if not data['task_ids']:
        authority(st, 'revalidate')
    if data['task_ids'] and any(not any(_overlap(path, scope) for tid in data['task_ids']
                   for scope in integration['tasks'][tid]['files']) for path in data['paths']):
        refuse('operational issue paths exceed current task scope')
    ref = data['diagnostic_ref']
    if ref not in st.get('recorded_artifacts', []):
        refuse('issue diagnostic must be a retained run artifact')
    A.read(ref)
    old = st.setdefault('operational_issues', {}).get(data['id'])
    if old:
        from al_agent_exchange import consume_issue
        consume_issue(st, data['id'], old)
    if old == data:
        return
    if data['revision'] != (old['revision'] + 1 if old else 1):
        refuse('stale operational issue revision')
    if old and any(old[k] != data[k] for k in ('cause', 'task_ids', 'paths', 'capability_or_check_ref', 'observed_binding')):
        refuse('operational issue scope/binding cannot change')
    if old and old['state'] == 'Resolved':
        refuse('resolved issue is immutable; register a new observation')
    if data['state'] == 'Resolved':
        from al_ledger import entry_verdict
        resolution = data['resolution_ref']
        if set(resolution) != {'evidence_id', 'diagnostic_sha256'} or resolution['diagnostic_sha256'] != ref['sha256']:
            refuse('resolution does not bind its fresh diagnostic')
        entry = next((e for e in st['evidence']['entries'] if e['id'] == resolution['evidence_id']), None)
        if (not old or ref == old['diagnostic_ref'] or not entry or entry['status'] != 'PASS'
                or entry_verdict(st, entry)[0] != 'VALID'
                or entry['area'] != data['capability_or_check_ref']
                or entry.get('started_at', 0) <= old['diagnostic_ref']['captured_at']
                or entry.get('ended_at', 0) > ref['captured_at']):
            refuse('issue resolution requires fresh matching current PASS evidence')
        from al_parallel import overlaps
        if data['paths'] and not overlaps(data['paths'], entry['scope']):
            refuse('repair evidence does not cover issue scope')
    if old and st.get('agent_contracts', {}).get('failure-report:' + data['id']):
        st.setdefault('agent_contract_history', []).append(copy.deepcopy(
            st['agent_contracts']['failure-report:' + data['id']]))
    from al_agent_exchange import produce
    produce(st, 'failure-report', data['id'], data, status='resolved' if data['state'] == 'Resolved' else 'observed')
    st['operational_issues'][data['id']] = copy.deepcopy(data)
    E.queue(st, 'issue.updated', data)
