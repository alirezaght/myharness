"""Implementation authority is independent of verification completeness."""
import copy

import al_artifacts as A
import al_task
from al_schema import refuse


def binding(st):
    return {'session_id': st['session_id'], 'run_id': st['run_id'],
            'requirement_hash': al_task.task_hash(st)}


def validate(st):
    value = st.get('implementation_readiness')
    if value is not None:
        if (not isinstance(value, dict) or set(value) != {'schema_version', 'binding', 'task_id', 'paths', 'obligations'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1 or value['binding'] != binding(st)
                or not isinstance(value['obligations'], list)
                or any(not isinstance(v, str) or not v for v in value['obligations'])):
            refuse('invalid implementation readiness binding/obligations')
        if not isinstance(value['paths'], list):
            refuse('invalid implementation readiness paths')
        if value['paths']:
            from al_writers import canonical_paths
            if canonical_paths(value['paths']) != value['paths']:
                refuse('invalid implementation readiness paths')
        if value['task_id'] is not None and value['task_id'] not in st.get('integration', {}).get('tasks', {}):
            refuse('foreign implementation readiness task')
    from al_execution_contract import validate_issue
    if not isinstance(st.get('operational_issues', {}), dict):
        refuse('invalid operational issue registry')
    for key, issue in st.get('operational_issues', {}).items():
        validate_issue(issue)
        if key != issue['id'] or issue['observed_binding'] != st.get('integration', {}).get('binding'):
            refuse('foreign operational issue binding')


def require_ready(st, *, task_id=None, paths=()):
    from al_new_origin import validate as origin
    from al_rework_recovery_contract import guard
    from al_scope import validate_identity
    from al_gate_recovery import marker
    from al_writers import canonical_paths
    origin(st)
    if st.get('runtime_origin'):
        from al_new_origin import context
        transaction = context().transaction
        if (transaction['status'] != 'ACTIVE' or transaction['run_id'] != st['run_id']
                or transaction['session_id'] != st['session_id']):
            refuse('implementation issuer binding is stale or not active')
    guard(st,implementation=True)
    validate_identity(st)
    if st['status'] != 'RUNNING' or st.get('spec_status') != 'SPEC_READY' or st.get('classification') not in {'SMALL', 'STANDARD', 'COMPLEX'}:
        refuse('implementation requires running SPEC_READY and classification')
    if st.get('gate_recovery'):
        marker(st)  # Integrity only; a writer never resolves this marker.
    owned = canonical_paths(paths) if paths else []
    if task_id is not None:
        from al_execution_tasks import affected_tasks
        integration = st.get('integration', {})
        if task_id not in integration.get('tasks', {}):
            refuse('unknown implementation task')
        for blocker in integration['blockers'].values():
            if blocker['state'] != 'Resolved' and task_id in affected_tasks(st, blocker['tasks']):
                refuse('task is blocked by ' + blocker['id'])
    from al_gate_currency import check
    from al_service_coverage import admission_gaps
    obligations = check(st)['reasons'] + admission_gaps(st, current=True)
    if st.get('proof_admission', {}).get('status') == 'pending':
        obligations.append('Architect proof admission pending')
    if 'task_attribution' in st:
        from al_task_attribution import classify_changes
        result = classify_changes(st)  # Corrupt/foreign provenance still refuses.
        obligations += ['attribution analysis incomplete: ' + p for p in result['ambiguous']]
        obligations += ['attribution analysis incomplete: ' + str(v) for v in result['limitations']]
    if owned:
        from al_gate_coverage import prospective_gaps
        obligations += prospective_gaps(st, owned)
    previous = st.get('implementation_readiness', {})
    # Debt is cumulative; only proof admission/repair observations can discharge it.
    obligations = sorted(set(obligations + previous.get('obligations', [])))
    value = {'schema_version': 1, 'binding': binding(st), 'task_id': task_id,
             'paths': owned, 'obligations': obligations}
    st['implementation_readiness'] = copy.deepcopy(value)
    st.setdefault('operational_issues', {})
    return value
