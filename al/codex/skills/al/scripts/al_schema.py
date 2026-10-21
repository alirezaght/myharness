"""Versioned fail-closed runtime validation."""
import re
import sys
from al_sources import validate_sources, SourceError

VERSION = 2
STATUSES = {'RUNNING', 'APPROVED', 'STOPPED_WITH_BLOCKERS', 'SPEC_BLOCKED',
            'AGENT_FAILED', 'WAITING_FOR_USER', 'INTERRUPTED', 'TRANSFERRED'}

def refuse(message):
    sys.exit('al_state: ' + message)

def valid_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]+', value))

def validate_state(st):
    if not isinstance(st, dict) or st.get('schema_version') != VERSION:
        refuse('unknown/legacy state schema; start a new run, retaining the old history')
    if not valid_id(st.get('session_id')) or not valid_id(st.get('run_id')):
        refuse('invalid immutable run identity')
    if st.get('status') not in STATUSES:
        refuse('invalid run status')
    from al_phases import MAIN_PHASES, AUX_PHASES
    if st.get('phase') not in MAIN_PHASES + AUX_PHASES:
        refuse('unknown phase')
    for key in ('review_iteration', 'correction_iteration', 'handoffs'):
        if type(st.get(key)) is not int or st[key] < 0:
            refuse('invalid counter ' + key)
    for key in ('evidence', 'writers', 'identity', 'task_package', 'phase_timings'):
        if not isinstance(st.get(key), dict):
            refuse('missing or invalid ' + key)
    for key in ('completed_phases', 'phase_occurrences', 'review_history', 'amendments'):
        if not isinstance(st.get(key), list):
            refuse('missing or invalid ' + key)
    for occurrence in st['phase_occurrences']:
        if not isinstance(occurrence,dict) or occurrence.get('phase') not in MAIN_PHASES+AUX_PHASES or type(occurrence.get('completed')) is not bool:
            refuse('invalid actual phase occurrence/completion proof')
        if occurrence['completed'] and occurrence.get('ended') is None: refuse('completed occurrence has no end time')
    ev = st['evidence']
    if not isinstance(ev.get('plan'), dict) or not isinstance(ev.get('entries'), list):
        refuse('invalid evidence ledger')
    if any(not isinstance(e,dict) for e in ev['entries']): refuse('malformed evidence entry')
    ids = [e.get('id') for e in ev['entries']]
    if len(ids) != len(set(ids)) or any(not re.fullmatch(r'E[1-9][0-9]*', x or '') for x in ids):
        refuse('duplicate or invalid evidence identity')
    if type(ev.get('seq')) is not int or ev['seq'] < len(ids):
        refuse('invalid evidence sequence')
    for e in ev['entries']:
        if e.get('run') != st['run_id'] or not isinstance(e.get('argv'), list) or not isinstance(e.get('area'),str) or not e.get('reservation'):
            refuse('foreign or malformed evidence entry')
    try:
        for record in [*ev['entries'], *ev['plan'].values(), *st.get('gate_runs', [])]:
            if 'sources' in record:validate_sources(record['sources'])
    except SourceError as exc:refuse('invalid evidence sources: '+str(exc))
    for wid, writer in st['writers'].items():
        if not valid_id(wid) or writer.get('id') != wid or writer.get('state') not in {'ACTIVE','FINISHED','UNRESOLVED','RECONCILED'}:
            refuse('invalid writer registry')

    if not isinstance(st.get('task_package',{}).get('sha256'),str) or not st['identity'].get('root'):
        refuse('missing immutable task/repository identity')
    for entry in ev['entries']:
        if entry.get('status') not in {'PASS','FAIL','EMPTY','INFRA','TORN','RUNNING'} or not isinstance(entry.get('scope'),list):
            refuse('invalid evidence status/scope')
    from al_artifacts import read
    try:
        read(st['task_package'])
        for amendment in st['amendments']: read(amendment['artifact'])
    except (KeyError, OSError, ValueError) as exc:
        refuse('task artifact is corrupt: '+str(exc))

    from al_events import validate
    try:
        validate(st)
    except (ValueError, KeyError, TypeError) as exc:
        refuse('invalid execution integration: ' + str(exc))
