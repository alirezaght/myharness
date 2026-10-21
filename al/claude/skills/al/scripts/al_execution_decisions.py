"""Acknowledged developer decisions and narrowly invalidated amendment chains."""
import copy
import time
from pathlib import Path

import al_artifacts as A
import al_events as E
import al_execution_tasks as T
from al_execution_contract import digest, validate_delivery
from al_schema import refuse


def apply_delivery(st, sid, delivery):
    validate_delivery(delivery)
    i = T.integration(st)
    if delivery['binding'] != i['binding']:
        refuse('foreign decision binding')
    dh = digest(delivery)
    old = i['deliveries'].get(delivery['id'])
    if old:
        if old['decision_hash'] != dh:
            refuse('conflicting duplicate delivery')
        return old
    # Authority checks the exact frozen private inbox and Registry bytes.
    T.authority(st, 'decision', delivery=delivery['id'])
    blocker = i['blockers'].get(delivery['blocker_id'])
    if not blocker or blocker['state'] != 'Open' or blocker['revision'] != delivery['blocker_revision']:
        refuse('stale decision blocker')
    affected = T.affected_tasks(st, blocker['tasks'])
    if set(delivery['tasks']) != affected:
        refuse('developer-authorized scope does not match dependency closure')
    from al_task import task_hash, substantive
    if delivery['base_revision'] != task_hash(st):
        refuse('decision targets a different accepted task revision')
    if delivery['verdict'] != 'reject' and not substantive(delivery['text']):
        refuse('resolution requires substantive explicit developer text')
    state = 'Rejected'
    if delivery['verdict'] != 'reject':
        for w in st['writers'].values():
            if w.get('task') in affected and w['state'] in {'ACTIVE', 'UNRESOLVED'}:
                refuse('affected writer must yield and reconcile before decision')
        criteria = set().union(*(set(i['tasks'][t]['criteria']) for t in affected))
        if not set(delivery['criteria']) <= criteria or (delivery['amendment'] and set(delivery['criteria']) != criteria):
            refuse('decision criteria scope does not cover affected work')
        if delivery['amendment']:
            from al_gate_recovery import amendment_binding
            recovery = amendment_binding(st) if 'gate_recovery' in st else None
            amendment = {'id': delivery['id'], 'text': delivery['text'], 'source': 'developer Decision Registry ' + delivery['registry']['id'], 'affected_criteria': delivery['criteria'], 'affected_tasks': sorted(affected), 'base_revision': delivery['base_revision'], 'delivery_hash': dh, 'registry': copy.deepcopy(delivery['registry']), 'scoped': True}
            raw = __import__('json').dumps(amendment, sort_keys=True).encode()
            amendment['artifact'] = A.snapshot_bytes(sid, raw, 'scoped-amendment', delivery['registry']['path'])
            st['amendments'].append(amendment)
            st['review'] = None
            st['review_status'] = None
            at = time.time()
            # Timestamped scopes also invalidate an in-flight check after it commits.
            paths = sorted({p for t in affected for p in i['tasks'][t]['files']})
            st['evidence'].setdefault('scope_invalidations', []).append({'at': at, 'scope': paths, 'amendment': delivery['id']})
            for tid in affected:
                row = i['tasks'][tid]
                row['revision'] += 1
                row.update(status='Blocked', progress=None)
                row.pop('admission', None)
                E.queue(st, 'task.updated', row)
            revision = task_hash(st)
            obligation = {'id': delivery['id'], 'tasks': sorted(affected), 'revision': revision, 'graph_revision': i['graph_revision'], 'state': 'Required', 'amendment': amendment['artifact']['sha256']}
            # A packet binds actual affected files, rather than prose about inspection.
            from al_ledger import scope_fingerprint
            obligation['candidate'] = scope_fingerprint(paths, [])
            obligation['packet_hash'] = digest(obligation)
            i['revalidations'][delivery['id']] = obligation
            if recovery:
                from al_recovery import checkpoint
                old_marker, old_ref, old_data = recovery
                new_ref = checkpoint(st, sid, 'Scoped developer amendment ' + delivery['id'], old_data['resume_instructions'])
                st.setdefault('gate_recovery_supersessions', []).append({'status': 'SUPERSEDED_BY_AMENDMENT', 'amendment': {'id': amendment['id'], 'artifact': amendment['artifact']}, 'old_checkpoint': old_ref, 'new_checkpoint': copy.deepcopy(new_ref), 'marker': old_marker, 'at': at})
                st['gate_recovery'] = dict(old_marker, checkpoint=new_ref['id'], replacement_manifest=None)
            E.queue(st, 'amendment.created', obligation)
            state = 'RevalidationRequired'
        else:
            state = 'Resolved'
        blocker['state'] = state
        blocker['delivery_id'] = delivery['id']
        if state == 'Resolved':
            E.queue(st, 'blocker.resolved', {'id': blocker['id'], 'delivery_id': delivery['id']})
    ack = {'delivery_id': delivery['id'], 'blocker_id': blocker['id'], 'state': state, 'decision_hash': dh}
    i['deliveries'][delivery['id']] = ack
    E.queue(st, 'decision.acknowledged', ack)
    return ack


def revalidate(st, sid, data):
    i = T.integration(st)
    obligation = i['revalidations'].get(data['id'])
    if not obligation or obligation['state'] != 'Required':
        refuse('no pending scoped revalidation')
    agent = i['agents'].get(data.get('agent'))
    if data.get('agent') in obligation.get('used_agents', []):
        refuse('new scoped revalidation requires an independent actual Architect invocation')
    if not agent or agent['role'] not in {'al-architect', 'al-architect-fallback'} or agent['status'] != 'Running' or agent.get('task') in obligation['tasks']:
        refuse('scoped revalidation needs distinct actual Architect invocation')
    from al_task import task_hash, substantive
    if data.get('packet_hash') != obligation['packet_hash'] or obligation['revision'] != task_hash(st) or obligation['graph_revision'] != i['graph_revision']:
        refuse('stale scoped revalidation packet')
    from al_ledger import scope_fingerprint
    paths = sorted({p for t in obligation['tasks'] for p in i['tasks'][t]['files']})
    if scope_fingerprint(paths, []) != obligation['candidate']:
        refuse('candidate changed since scoped revalidation packet')
    criteria = sorted({c for t in obligation['tasks'] for c in i['tasks'][t]['criteria']})
    if data.get('criteria') != criteria or data.get('applicable') is not True or data.get('findings') != [] or not substantive(data.get('rationale')):
        refuse('incomplete/blocked scoped Architect revalidation')
    T.authority(st, 'revalidate')
    obligation.setdefault('used_agents', []).append(data['agent'])
    obligation.update(state='Validated', artifact=A.snapshot_bytes(sid, __import__('json').dumps(data, sort_keys=True).encode(), 'scoped-revalidation', 'actual Architect ' + data['agent']))
    for b in i['blockers'].values():
        if b.get('delivery_id') == data['id'] and b['state'] == 'RevalidationRequired':
            b['state'] = 'Resolved'
            E.queue(st, 'blocker.resolved', {'id': b['id'], 'delivery_id': data['id']})
    E.queue(st, 'amendment.revalidated', obligation)
