"""Agent communication (#30): progress checkpoints, early typed questions, structured handoff records.

* `progress --note TEXT [--done N --total M]` records a real milestone. The latest one shows in the
  status line, in `inspect` (summary) and, under Harness, on the agent's dashboard row
  (`agent.updated` activity/progress), so nobody has to poll a model for it.
* `ask --blocking|--non-blocking --question TEXT` records a question an agent raises mid-run; the
  agent keeps working on independent parts. `ask --answer QID --text TEXT` answers it and prints how
  to send the answer to the same warm agent; `ask --forwarded QID` records that it was sent. Until then
  `next` names the answer to forward (an answer may come from the Harness dashboard, #31).
* `handoff-record --file JSON` validates and stores an agent's structured handoff record
  (`agent-handoff-record-v1`): decisions with reasons, assumptions, files touched, verified claims
  with evidence IDs, unverified claims, open questions and risks. The next writer handoff and the
  comparison stage of a delta review receive the records by hash, never a paraphrase.
"""
import json
import time
from pathlib import Path

import al_artifacts as A
import al_store
from al_schema import refuse

KEEP_NOTES = 50
RECORD_KEYS = {'schema_version', 'kind', 'role', 'agent_id', 'decisions', 'assumptions', 'files_touched',
               'verified', 'unverified', 'open_questions', 'risks'}
REQUIRED_KEYS = RECORD_KEYS - {'agent_id'}
ROLES = {'al-architect', 'al-designer', 'al-engineer', 'al-builder', 'al-scout'}


def _text(value, name, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        refuse(name + ' must be nonempty text of at most ' + str(limit) + ' characters')
    return value.strip()


# --- progress ---------------------------------------------------------------------------------

def latest_progress(st):
    notes = st.get('progress_notes') or []
    return notes[-1] if notes else None


def _harness_progress(st, row):
    """Mirror the checkpoint on the Harness dashboard when the agent is a registered Harness agent."""
    agents = (st.get('integration') or {}).get('agents') or {}
    agent = row.get('agent')
    if not agent or agent not in agents:
        return
    from al_events import queue
    payload = {'id': agent, 'status': (agents[agent] or {}).get('status') or 'Running', 'activity': row['note']}
    if row.get('total'):
        payload['progress'] = {'completed': row['done'], 'total': row['total'], 'unit': 'milestones'}
    queue(st, 'agent.updated', payload)


def cmd_progress(a, sid):
    st = al_store.load(sid)
    note = _text(a.note, 'progress --note', 300)
    if (a.done is None) != (a.total is None):
        refuse('progress needs --done and --total together')
    if a.total is not None and not (a.total >= 1 and 0 <= a.done <= a.total):
        refuse('progress needs 0 <= --done <= --total and --total >= 1')
    row = {'at': time.time(), 'note': note, 'phase': st.get('phase')}
    for key in ('role', 'agent', 'done', 'total'):
        if getattr(a, key, None) is not None:
            row[key] = getattr(a, key)
    notes = st.setdefault('progress_notes', [])
    notes.append(row)
    del notes[:-KEEP_NOTES]
    _harness_progress(st, row)
    al_store.save(sid, st)
    print('AL · progress: ' + render_progress(row))


def render_progress(row):
    count = (' (' + str(row['done']) + '/' + str(row['total']) + ')') if row.get('total') else ''
    who = (row.get('role') or row.get('agent') or '')
    return (who + ': ' if who else '') + row['note'] + count


# --- questions --------------------------------------------------------------------------------

def open_questions(st):
    return [q for q in st.get('agent_questions', []) if q.get('state') == 'open']


def unforwarded(st):
    """Answered questions whose answer has not yet been sent to the agent that asked."""
    return [q for q in st.get('agent_questions', []) if q.get('state') == 'answered' and not q.get('forwarded')]


def forward_line(st, q):
    """How the supervisor sends an answer to the same warm agent that asked."""
    import al_agents
    head = 'AL · forward the answer to ' + q['id'] + ' (' + q['answer'] + ')'
    if q.get('agent'):
        call = al_agents.resume_call(q['agent'])
    else:
        row = al_agents.latest(st, q['role']) if q.get('role') else None
        call = al_agents.resume_call(row['id'], row.get('platform')) if row else None
    return (head + ' to its waiting agent: ' + call if call else head + ' in the next handoff of ' + (q.get('role') or 'its role')) \
        + '; then `ask --forwarded ' + q['id'] + '`'


def cmd_ask(a, sid):
    st = al_store.load(sid)
    questions = st.setdefault('agent_questions', [])
    if a.forwarded:
        q = next((x for x in questions if x['id'] == a.forwarded), None)
        if not q or q['state'] != 'answered':
            refuse('question ' + a.forwarded + ' has no answer to forward')
        q['forwarded'] = time.time()
        al_store.save(sid, st)
        print('AL · answer to ' + q['id'] + ' forwarded')
        return
    if a.answer:
        q = next((x for x in questions if x['id'] == a.answer), None)
        if not q:
            refuse('unknown question ' + a.answer + '; `ask --list` shows them')
        if q['state'] != 'open':
            refuse('question ' + q['id'] + ' is already ' + q['state'])
        q.update(state='answered', answer=_text(a.text, 'ask --text'), answered_at=time.time())
        al_store.save(sid, st)
        print('AL · answered ' + q['id'])
        import al_agents
        if q.get('agent'):
            print('AL · send the answer to the asking agent: ' + al_agents.resume_call(q['agent']))
        elif q.get('role'):
            line = al_agents.instruction(st, q['role'])
            print(line or ('AL · no warm ' + q['role'] + ' agent recorded; include the answer in its next handoff'))
        return
    if a.list:
        rows = questions if a.all else open_questions(st)
        if not rows:
            print('AL · no open questions')
        for q in rows:
            print(q['id'] + ' [' + q['kind'] + ', ' + q['state'] + '] ' + (q.get('role') or q.get('agent') or '') + ': ' + q['question']
                  + (' → ' + q['answer'] if q.get('answer') else ''))
        return
    if a.blocking == a.non_blocking:
        refuse('ask needs exactly one of --blocking or --non-blocking')
    q = {'id': 'Q' + str(len(questions) + 1), 'kind': 'blocking' if a.blocking else 'non-blocking', 'state': 'open',
         'question': _text(a.question, 'ask --question'), 'phase': st.get('phase'), 'at': time.time()}
    for key in ('role', 'agent', 'context'):
        if getattr(a, key, None):
            q[key] = getattr(a, key)
    questions.append(q)
    al_store.save(sid, st)
    print('AL · ' + q['id'] + ' recorded (' + q['kind'] + '). The agent continues independent work; answer with '
          '`ask --answer ' + q['id'] + ' --text "<answer>"` and send it to the same agent.')


# --- structured handoff records ----------------------------------------------------------------

def validate_record(st, value):
    """Closed `agent-handoff-record-v1` (see reference/schemas/agent-handoff-record.json)."""
    if not isinstance(value, dict):
        refuse('handoff record must be a JSON object')
    missing, extra = REQUIRED_KEYS - value.keys(), set(value) - RECORD_KEYS
    if missing or extra:
        refuse('invalid handoff record: missing=' + str(sorted(missing)) + ' extra=' + str(sorted(extra)))
    if value['schema_version'] != 1 or value['kind'] != 'agent-handoff-record-v1':
        refuse('handoff record requires schema_version 1 and kind agent-handoff-record-v1')
    if value['role'] not in ROLES:
        refuse('handoff record role must be an AL role')
    if 'agent_id' in value:
        _text(value['agent_id'], 'agent_id', 200)
    def rows(name, fields, optional=()):
        items = value[name]
        if not isinstance(items, list):
            refuse('handoff record ' + name + ' must be an array')
        for item in items:
            if not isinstance(item, dict) or not set(fields) <= item.keys() or set(item) - set(fields) - set(optional):
                refuse('handoff record ' + name + ' entries need exactly ' + ', '.join(fields)
                       + (' (optional ' + ', '.join(optional) + ')' if optional else ''))
            for f in fields:
                if f != 'evidence_ids':
                    _text(item[f], name + '.' + f)
        return items
    def texts(name):
        items = value[name]
        if not isinstance(items, list) or any(not isinstance(x, str) or not x.strip() for x in items):
            refuse('handoff record ' + name + ' must be an array of nonempty strings')
        return items
    rows('decisions', ('decision', 'reason'))
    texts('assumptions')
    for path in texts('files_touched'):
        p = Path(path)
        if p.is_absolute() or '..' in p.parts:
            refuse('files_touched must be repository-relative paths: ' + path)
    import al_ledger as L
    for item in rows('verified', ('claim', 'evidence_ids')):
        ids = item['evidence_ids']
        if not isinstance(ids, list) or not ids:
            refuse('a verified claim needs evidence_ids; put claims without evidence under unverified')
        for eid in ids:
            if not L.find(st, eid):
                refuse('verified claim cites unknown evidence ' + str(eid))
    rows('unverified', ('claim', 'reason'))
    texts('open_questions')
    texts('risks')
    return value


def cmd_handoff_record(a, sid):
    st = al_store.load(sid)
    try:
        raw = Path(a.file).read_bytes()
        value = json.loads(raw)
    except (OSError, ValueError) as exc:
        refuse('handoff record must be a readable JSON file: ' + str(exc))
    validate_record(st, value)
    ref = A.snapshot_bytes(sid, json.dumps(value, sort_keys=True).encode(), 'agent-handoff-record', a.file)
    entry = {'artifact': ref, 'role': value['role'], 'agent_id': value.get('agent_id'), 'phase': st.get('phase'),
             'review_iteration': st.get('review_iteration', 0), 'at': time.time()}
    if a.writer:
        entry['writer'] = a.writer
    st.setdefault('handoff_records', []).append(entry)
    al_store.save(sid, st)
    print('AL · handoff record ' + ref['sha256'][:12] + ' stored (' + value['role'] + ': ' + str(len(value['decisions']))
          + ' decision(s), ' + str(len(value['verified'])) + ' verified, ' + str(len(value['unverified'])) + ' unverified, '
          + str(len(value['open_questions'])) + ' open question(s))')


def records_since(st, review_iteration=None, exclude_writer=None):
    out = []
    for entry in st.get('handoff_records', []):
        if review_iteration is not None and entry.get('review_iteration', 0) < review_iteration:
            continue
        if exclude_writer and entry.get('writer') == exclude_writer:
            continue
        out.append({'artifact': {k: entry['artifact'][k] for k in ('kind', 'sha256', 'stored_path')},
                    'role': entry['role'], 'content': A.read_json(entry['artifact'])})
    return out


def render_records(rows):
    """Prompt block: the records' own JSON by hash, never a paraphrase."""
    if not rows:
        return ''
    lines = ['PRIOR AGENT HANDOFF RECORDS (agent-handoff-record-v1, original JSON by hash)']
    for row in rows[-5:]:
        lines.append('- ' + row['role'] + ' ' + row['artifact']['sha256'] + ': ' + json.dumps(row['content'], sort_keys=True))
    return '\n'.join(lines)


def status_suffix(st):
    """Status-line text for the latest checkpoint and open questions ('' when none)."""
    parts = []
    row = latest_progress(st)
    if row and row.get('phase') == st.get('phase'):
        parts.append(render_progress(row))
    qs = open_questions(st)
    if qs:
        blocking = sum(q['kind'] == 'blocking' for q in qs)
        parts.append(str(len(qs)) + ' open question(s)' + (' (' + str(blocking) + ' blocking)' if blocking else ''))
    pending = unforwarded(st)
    if pending:
        parts.append(str(len(pending)) + ' answer(s) to forward')
    return ' │ '.join(parts)


def summary(st):
    """`inspect` summary fields."""
    return {'progress': latest_progress(st), 'open_questions': open_questions(st),
            'handoff_records': len(st.get('handoff_records', [])), 'output_tokens': st.get('output_tokens') or {}}


def register(sub):
    p = sub.add_parser('progress', help='record a real milestone (status line, inspect, Harness dashboard)')
    p.add_argument('--note', required=True)
    p.add_argument('--done', type=int)
    p.add_argument('--total', type=int)
    p.add_argument('--role')
    p.add_argument('--agent')
    p.set_defaults(fn=cmd_progress)
    p = sub.add_parser('ask', help='record an early typed question from an agent, or answer one')
    p.add_argument('--blocking', action='store_true')
    p.add_argument('--non-blocking', action='store_true')
    p.add_argument('--question')
    p.add_argument('--context')
    p.add_argument('--role')
    p.add_argument('--agent')
    p.add_argument('--answer', metavar='QID')
    p.add_argument('--text')
    p.add_argument('--forwarded', metavar='QID')
    p.add_argument('--list', action='store_true')
    p.add_argument('--all', action='store_true')
    p.set_defaults(fn=cmd_ask)
    p = sub.add_parser('handoff-record', help='validate and store an agent\'s structured handoff record')
    p.add_argument('--file', required=True)
    p.add_argument('--writer')
    p.set_defaults(fn=cmd_handoff_record)
