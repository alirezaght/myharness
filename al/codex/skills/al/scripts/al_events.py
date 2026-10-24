"""Atomic semantic outbox attached to the canonical guarded AL state."""
import copy
import secrets
import time

from al_execution_contract import canonical, validate_binding, validate_event


def initialise(st, envelope, handoff_path):
    from pathlib import Path
    from al_execution_contract import digest
    binding = {key: envelope['approval'][key] for key in ('approval_id', 'spec_version', 'content_hash', 'revision', 'review_state_hash')}
    binding.update(execution_id=envelope['execution_id'], task_id=envelope['task_id'], handoff_hash=digest(Path(handoff_path).read_bytes()), al_session_id=st['session_id'], al_run_id=st['run_id'])
    validate_binding(binding)
    st['integration'] = {'schema_version': 1, 'binding': binding, 'handoff': str(Path(handoff_path).resolve()), 'sequence': 0, 'outbox': [], 'tasks': {}, 'agents': {}, 'blockers': {}, 'deliveries': {}, 'graph_revision': 0, 'revalidations': {}}


def queue(st, kind, payload):
    if 'integration' in st:
        st['integration'].setdefault('pending', []).append({'kind': kind, 'payload': copy.deepcopy(payload)})


def validate(st):
    i = st.get('integration')
    if i is None:
        return
    if i.get('schema_version') != 1:
        raise ValueError('unsupported AL integration schema')
    validate_binding(i['binding'])
    if i['binding']['al_session_id'] != st['session_id'] or i['binding']['al_run_id'] != st['run_id']:
        raise ValueError('foreign AL integration identity')
    if type(i['sequence']) is not int or i['sequence'] != len(i['outbox']):
        raise ValueError('corrupt AL outbox cursor')
    seen = set()
    for n, event in enumerate(i['outbox'], 1):
        validate_event(event)
        if event['sequence'] != n or event['binding'] != i['binding'] or event['event_id'] in seen:
            raise ValueError('corrupt AL outbox history')
        seen.add(event['event_id'])


def prepare_commit(previous, proposed):
    i = proposed.get('integration')
    old = (previous or {}).get('integration')
    if i is None:
        if old is not None:
            raise ValueError('integration binding cannot be removed')
        return proposed
    validate(proposed)
    if old and (i['binding'] != old['binding'] or i['handoff'] != old['handoff'] or i['outbox'] != old['outbox']):
        raise ValueError('integration identity/history is immutable')
    pending = i.pop('pending', [])
    if not previous or (previous['status'] != 'RUNNING' and proposed['status'] == 'RUNNING'):
        pending.insert(0, {'kind': 'run.started', 'payload': {'status': proposed['status']}})
    if not previous or previous['phase'] != proposed['phase'] or previous.get('phase_occurrences') != proposed.get('phase_occurrences'):
        pending.append({'kind': 'phase.changed', 'payload': {'phase': proposed['phase'], 'occurrence': len(proposed['phase_occurrences'])}})
    before = (previous or {}).get('writers', {})
    for wid, w in proposed['writers'].items():
        if before.get(wid) != w:
            pending.append({'kind': 'writer.updated', 'payload': copy.deepcopy(w)})
    facts = i.setdefault('evidence_facts', {})
    for e in proposed['evidence']['entries']:
        if e.get('kind') == 'preflight' and not e['scope']:
            continue  # a tool-availability preflight tests no repository path
        p = {k: e[k] for k in ('id', 'area', 'status', 'scope')}
        p['counts'] = e.get('counts')
        if e['status'] == 'RUNNING':
            kind = 'test.started'
        else:
            from al_ledger import entry_verdict
            p['validity'] = entry_verdict(proposed, e)[0]
            kind = 'test.result'
        if facts.get(e['id']) != p:
            pending.append({'kind': kind, 'payload': p})
            facts[e['id']] = copy.deepcopy(p)
    if previous and previous['status'] != proposed['status'] and proposed['status'] != 'RUNNING':
        pending.append({'kind': 'run.ended', 'payload': {'status': proposed['status'], 'reason': proposed.get('stop_reason')}})
    if proposed['status'] == 'APPROVED' and (not previous or previous['status'] != 'APPROVED'):
        if not proposed.get('approval') or proposed['phase'] != 'DONE':
            raise ValueError('implementation result requires guarded approval receipt')
        pending.append({'kind': 'implementation.result', 'payload': {'status': 'APPROVED', 'approval': copy.deepcopy(proposed['approval'])}})
    for fact in pending:
        i['sequence'] += 1
        event = {'schema_version': 1, 'event_id': 'evt-' + secrets.token_hex(12), 'sequence': i['sequence'], 'timestamp': time.time(), 'binding': copy.deepcopy(i['binding']), **fact}
        validate_event(event)
        i['outbox'].append(event)
    validate(proposed)
    return proposed


def events_after(st, after=0, limit=100):
    from al_execution_contract import integer
    integer(after, 'event cursor')
    integer(limit, 'page limit', 1)
    if limit > 200:
        raise ValueError('page limit exceeds 200')
    i = st.get('integration')
    if i is None:
        raise ValueError('run has no integration binding')
    if after > i['sequence']:
        raise ValueError('cursor beyond retained source')
    page, size = [], 0
    for event in i['outbox'][after:after + limit]:
        length = len(canonical(event).encode())
        if page and size + length > 524288:
            break
        page.append(event)
        size += length
    cursor = page[-1]['sequence'] if page else after
    return {'events': page, 'next_after': cursor, 'high_watermark': i['sequence'], 'has_more': cursor < i['sequence']}
