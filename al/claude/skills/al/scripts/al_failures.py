"""Separate service availability, refusals and permissions from implementation retries."""
import time
from pathlib import Path

import al_artifacts as A
import al_models
import al_store
from al_schema import refuse
from al_writers import operation_gaps

KINDS = ('auto', 'implementation', 'model-capacity', 'model-unavailable',
         'model-refusal', 'permission')


def classify(reason, requested='auto'):
    text = reason.lower()
    if any(p in text for p in ('possible cybersecurity risk', 'content was flagged',
                               'content policy', 'safety policy', 'model service refused')):
        detected = 'model-refusal'
    elif any(p in text for p in ('model is at capacity', 'selected model is at capacity',
                                 'model overloaded', 'model service overloaded',
                                 'model rate limit', 'model rate-limit')):
        detected = 'model-capacity'
    elif any(p in text for p in ('unknown model', 'unsupported model', 'model is not available',
                                 'agent type is currently not available', 'for spawn_agent. available models:')):
        detected = 'model-unavailable'
    elif any(p in text for p in ('permission denied', 'operation not permitted',
                                 'approval review rejected', 'automatic approval review rejected')):
        detected = 'permission'
    else:
        detected = 'implementation'
    if requested != 'auto' and detected != 'implementation' and requested != detected:
        refuse('failure kind conflicts with the concrete service/permission error: ' + detected)
    return detected if requested == 'auto' else requested


def next_step(kind):
    if kind == 'model-refusal':
        return ('No user approval is pending: a model-service refusal cannot be overridden by approval. '
                'Preserve the checkpoint and report the refusal and supported recovery options; '
                'do not automatically retry or switch models to evade it.')
    if kind == 'permission':
        return ('Identify the denied operation and permission source. Reuse existing authorization '
                'or an allowed alternative. If additional permission is actually required, present '
                'that concrete action, its reason and the permission source to the user.')
    return 'Reconcile operations and peer state, then use guarded retry only for a transient implementation failure.'


def guard_record(reason, requested='auto'):
    kind = classify(reason, requested)
    if kind == 'model-capacity':
        refuse('model capacity is not an implementation failure; implementation retry budget unchanged. '
               'Reconcile outstanding writers/checks, then use model-retry <agent> --reason <error> '
               '--artifact <reconciliation-proof> for one same-model availability retry. '
               'After another availability failure, use model-unavailable under Model policy.')
    if kind == 'model-unavailable':
        refuse('model availability/entitlement is not an implementation failure; retry budget unchanged. '
               'Use model-unavailable <model> --reason <verbatim-error> under Model policy.')
    return kind


def cmd_model_retry(a, sid):
    st = al_store.load(sid)
    if a.agent not in al_models.ROLES or not a.reason.strip():
        refuse('model retry requires an approved role and concrete capacity error')
    if classify(a.reason, a.kind) != 'model-capacity':
        refuse('model-retry only handles transient model capacity/rate-limit/overload errors')
    if st.get('failure') or st['status'] != 'RUNNING' or st['phase'] == 'DONE':
        refuse('model retry requires a running run with no pending implementation failure')
    if operation_gaps(st):
        refuse('reconcile outstanding writers/checks before model availability retry')
    slot = al_models.resolve(st, a.agent)
    if slot['model'] in st.get('unavailable_models', []):
        refuse('model already latched unavailable; follow Model policy, never re-probe it')
    key = a.agent + '@' + slot['model']
    if st.get('model_retries', {}).get(key, 0):
        refuse('same-model availability retry already used; implementation retry budget unchanged. '
               'Record repeated unavailability with model-unavailable under Model policy.')
    proof = A.snapshot_artifact(sid, Path(a.artifact), 'model-availability-reconciliation')
    st.setdefault('model_retries', {})[key] = 1
    st.setdefault('model_retry_history', []).append({
        'agent': a.agent, 'model': slot['model'], 'reason': a.reason,
        'artifact': proof, 'at': time.time(),
    })
    al_store.save(sid, st)
    print('AL · Model availability retry · ' + al_models.display(a.agent) + ' · ' + al_models.label(slot))
    print('Same approved model only (1/1); implementation retry budget unchanged. No user approval pending.')


def register(sub):
    p = sub.add_parser('model-retry')
    p.add_argument('agent')
    p.add_argument('--reason', required=True)
    p.add_argument('--artifact', required=True)
    p.add_argument('--kind', choices=['auto', 'model-capacity'], default='auto')
    p.set_defaults(fn=cmd_model_retry)
