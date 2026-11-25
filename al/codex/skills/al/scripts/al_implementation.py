"""Implementation authority is independent of verification completeness."""
import copy

import al_task
import al_scope as S
from al_schema import refuse


def binding(st):
    # The substantive requirement lineage: an editorial revision (labels, wording) changes the document
    # hash but not the requirement, so it must not add a revalidation obligation (test_task_editorial_revision).
    # Without an editorial overlay both hashes are equal, so earlier bindings stay valid.
    return {'session_id': st['session_id'], 'run_id': st['run_id'],
            'requirement_hash': al_task.revision_hashes(st)[-1]}


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


def require_preflight(st, *, task_id=None, paths=()):
    """Validate implementation identity and authority before current-source analysis."""
    from al_inherited_lineage import require_recovery_complete
    require_recovery_complete(st)
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
    return owned


@S.decision
def _assessment(st, owned):
    """Share a stable view, or fall back to the original independent guarded reads.

    No state is published here. S.decision discards its shared view on movement or
    unavailability before recomputing this pure body through the existing nested guards.
    """
    from al_writer_progress import report
    report('readiness', force=True)
    from al_gate_currency import check
    from al_service_coverage import admission_gaps
    report('gate-currency', force=True)
    currency = check(st)
    if 'drift_paths' in currency:
        # Inputs moving under an ACTIVE writer are transient, not coverage debt to record.
        from al_writers import refuse_sibling_drift, _active
        refuse_sibling_drift(None, currency['drift_paths'], _active(st))
    reasons = currency['reasons']
    report('service-coverage', force=True)
    obligations = reasons + admission_gaps(st, current=True)
    if st.get('proof_admission', {}).get('status') == 'pending':
        obligations.append('Architect proof admission pending')
    if 'task_attribution' in st:
        from al_task_attribution import classify_changes
        report('attribution-classification', force=True)
        result = classify_changes(st)  # Corrupt/foreign provenance still refuses.
        obligations += ['attribution analysis incomplete: ' + p for p in result['ambiguous']]
        obligations += ['attribution analysis incomplete: ' + str(v) for v in result['limitations']]
    if owned:
        from al_gate_coverage import prospective_gaps
        report('prospective-coverage', force=True)
        obligations += prospective_gaps(st, owned)
    return obligations


def require_ready(st, *, task_id=None, paths=()):
    owned = require_preflight(st, task_id=task_id, paths=paths)
    before = copy.deepcopy(st)
    observed = copy.deepcopy(before)
    obligations = _assessment(observed, owned)
    if observed != before or st != before:
        refuse('implementation state changed during read-only readiness assessment')
    previous = st.get('implementation_readiness', {})
    # Debt is cumulative; only proof admission/repair observations can discharge it.
    obligations = sorted(set(obligations + previous.get('obligations', [])))
    value = {'schema_version': 1, 'binding': binding(st), 'task_id': task_id,
             'paths': owned, 'obligations': obligations}
    st['implementation_readiness'] = copy.deepcopy(value)
    st.setdefault('operational_issues', {})
    return value
