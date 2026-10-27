"""Append-only audit of a newer execution replacing obsolete evidence.

Selection never mutates a receipt or bypasses the ledger's latest-attempt rule.
Only a current rerun of the same declared obligation can replace an old result.
"""
import json
import time

import al_artifacts as A
import al_ledger as L
import al_scope as S
import al_store
import al_task
from al_schema import refuse


def binding(st):
    from al_verification_authority import authority_binding
    return {'run': st['run_id'], 'task_hash': al_task.task_hash(st),
            'candidate_hash': S.capture_candidate(st)['fingerprint'],
            'verification_authority': authority_binding(st) if 'proof_admission' in st else None}


def validate(st, original_id, replacement_id):
    original, replacement = L.find(st, original_id), L.find(st, replacement_id)
    if not original or not replacement or original is replacement:
        refuse('supersession requires two different existing evidence entries')
    if original['run'] != st['run_id'] or replacement['run'] != st['run_id']:
        refuse('supersession cannot cross runs')
    if int(original_id[1:]) >= int(replacement_id[1:]):
        refuse('supersession requires a newer execution')
    if original['area'].startswith('gate-'):
        refuse('gate replacement requires its exact sourced gate rerun')
    if not L.same_command(original, replacement):
        refuse('replacement must execute the same declared obligation; replan changed semantics separately')
    spec = L.ledger(st)['plan'].get(replacement['area'])
    if not spec or not L.same_command(replacement, spec):
        refuse('replacement does not match the current obligation')
    if L.latest(st, replacement['area'], spec) is not replacement:
        refuse('a later attempt prevents evidence replacement')
    if L.entry_verdict(st, replacement)[0] != 'VALID':
        refuse('replacement is not a current VALID PASS')
    if not replacement.get('pid') or replacement.get('reuse') or replacement.get('execution_association'):
        refuse('replacement requires its own original execution')
    for entry in [original, replacement]:
        cleanup = entry.get('cleanup') or {}
        if (entry['status'] == 'RUNNING' or entry.get('interrupted') and not entry.get('reconciliation')
                or cleanup.get('unresolved') or cleanup.get('errors')):
            refuse('supersession requires resolved execution ownership')
        if not entry.get('ended_at') or not entry.get('log') or L.file_sha(entry['log']) != entry.get('log_sha256'):
            refuse('original and replacement logs must retain their recorded integrity')
    return original, replacement


def supersede(st, original_id, replacement_id, reason):
    if not isinstance(reason, str) or not reason.strip():
        refuse('supersession requires an explicit reason')
    from al_writers import operation_gaps
    if operation_gaps(st):
        refuse('supersession requires settled operations')
    original, replacement = validate(st, original_id, replacement_id)
    current = binding(st)
    value = {'schema_version': 1, 'kind': 'evidence-supersession-v1', 'binding': current,
             'original': {'id': original_id, 'status': original['status'], 'log_sha256': original['log_sha256'],
                          'receipt': original.get('execution_receipt'), 'association': original.get('execution_association')},
             'replacement': {'id': replacement_id, 'log_sha256': replacement['log_sha256'],
                             'reservation': replacement['reservation']},
             'obligation': dict(L.ledger(st)['plan'][original['area']]), 'reason': reason.strip()}
    records = L.ledger(st).get('supersessions', [])
    for ref in records:
        prior = A.read_json(ref)
        if prior['original']['id'] == original_id:
            if {k:v for k,v in prior.items() if k!='at'} == value:
                return ref
            refuse('original evidence already has a different supersession')
    value['at'] = time.time()
    ref = A.snapshot_bytes(st['session_id'], json.dumps(value, sort_keys=True).encode(), value['kind'], reason.strip())
    L.ledger(st).setdefault('supersessions', []).append(ref)
    return ref


def cmd_supersede(a, sid):
    st = al_store.load(sid)
    ref = supersede(st, a.id, a.replaced_by, a.reason)
    al_store.save(sid, st)
    print(json.dumps({'supersession': ref, 'original_preserved': a.id, 'replacement': a.replaced_by}, sort_keys=True))
