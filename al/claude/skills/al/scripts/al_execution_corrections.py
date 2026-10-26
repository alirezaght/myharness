"""Explicit Harness-authorized correction generation of a completed AL run."""

import copy
import time

import al_events as E
import al_execution_tasks as T
import al_store as S
from al_execution_contract import digest
from al_schema import refuse


def apply_correction(st, sid, request):
    i = T.integration(st)
    required = {'schema_version', 'id', 'binding', 'generation', 'candidate_sha', 'pipeline_id',
                'prior_completion_digest', 'observation_digest', 'observation', 'tasks'}
    if (set(request) != required or request['schema_version'] != 1
            or request['binding'] != i['binding'] or not request['tasks']
            or digest(request['observation']) != request['observation_digest']):
        refuse('invalid delivery correction request')
    existing = i.setdefault('corrections', {}).get(request['id'])
    if existing:
        if existing['request_digest'] != digest(request):
            refuse('conflicting delivery correction replay')
        T.authority(st, 'delivery-correction', delivery=request['id'])
        return existing
    if st['status'] != 'APPROVED' or st['phase'] != 'DONE' or not st.get('approval'):
        refuse('delivery correction requires completed guarded implementation')
    prior = {'status': 'APPROVED', 'approval': st['approval']}
    if digest(prior) != request['prior_completion_digest']:
        refuse('delivery correction differs from previous completion')
    if (type(request['generation']) is not int
            or request['generation'] != i.get('correction_generation', 0) + 1):
        refuse('delivery correction generation is stale')
    from al_writers import operation_gaps
    gaps = operation_gaps(st)
    if gaps:
        refuse('settle writer/check before delivery correction: ' + str(gaps))
    T.authority(st, 'delivery-correction', delivery=request['id'])
    affected = T.affected_tasks(st, request['tasks'])
    from al_transitions import require_delivery_correction
    require_delivery_correction(st)
    ack = {'request_id': request['id'], 'request_digest': digest(request),
           'generation': request['generation'],
           'prior_completion_digest': request['prior_completion_digest']}
    # This event MUST precede generated run.started and task updates at commit.
    E.queue(st, 'delivery.correction.acknowledged', ack)
    i['corrections'][request['id']] = copy.deepcopy(ack)
    i['correction_generation'] = request['generation']
    st.setdefault('delivery_completion_history', []).append({
        'generation': request['generation'] - 1, 'approval': copy.deepcopy(st['approval']),
        'review': copy.deepcopy(st.get('review')), 'request': copy.deepcopy(request),
        'completed_phases': list(st['completed_phases'])})
    st.pop('approval', None)
    st.pop('review', None)
    st['review_status'] = 'pending'
    st['status'] = 'RUNNING'
    st['evidence']['invalidated_at'] = time.time()
    st['completed_phases'] = []
    i['graph_revision'] += 1
    for tid in sorted(affected):
        task = i['tasks'][tid]
        task.update(status='Waiting', revision=task['revision'] + 1, progress=None)
        task.pop('admission', None)
        task.pop('completed_at', None)
        E.queue(st, 'task.updated', task)
    # Remote failure proves no local cause. Re-enter genuine Architect planning.
    S.set_phase(st, 'PLANNING', ['al-architect'])
    if 'proof_admission' in st:
        from al_new_origin import FIELDS, occurrence
        admission = st['proof_admission']
        st['proof_admission'] = {key: admission[key] for key in FIELDS}
        st['proof_admission'].update(status='pending', planning_occurrence=occurrence(st),
                                    invalidation_generation=admission['invalidation_generation'] + 1)
    return ack
