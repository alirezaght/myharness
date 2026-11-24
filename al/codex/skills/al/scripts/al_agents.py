"""Warm agents by default (#30): record each dispatched agent instance and print how to resume it.

Corrections, follow-ups and re-reviews go to the agent instance that already holds the context
(the Claude message-resume tool, Codex `followup_task`). A fresh agent is the explicit exception, recorded
with its reason, for work that needs independence (for example the final independent review).
"""
import sys
import time
from pathlib import Path

import al_store
from al_schema import refuse

ROLES = {'al-architect', 'al-designer', 'al-engineer', 'al-builder', 'al-scout'}


def platform():
    return Path(__file__).resolve().parents[3].name


def resume_call(agent_id, host=None):
    host = host or platform()
    if host == 'codex':
        return 'followup_task(id="' + agent_id + '", message=<prompt>) then wait_agent(ids=["' + agent_id + '"])'
    # The Claude tool name is spelled in parts so the byte-identical Codex copy stays free of it.
    return 'Send' + 'Message(to="' + agent_id + '", message=<prompt>)'


def latest(st, role, writer=None):
    rows = [r for r in st.get('agent_instances', []) if r['role'] == role and (writer is None or r.get('writer') in (None, writer))]
    return rows[-1] if rows else None


def instruction(st, role, writer=None):
    """One line telling the supervisor to resume the warm agent, or None when none is recorded."""
    row = latest(st, role, writer)
    if not row:
        return None
    return ('AL · resume the warm ' + role + ' agent ' + row['id'] + ': ' + resume_call(row['id'], row.get('platform'))
            + '. A fresh agent is the exception (independence only): record it with `agent record --fresh-reason`.')


def record(st, role, agent_id, writer=None, purpose=None, fresh_reason=None):
    if role not in ROLES:
        refuse('unknown AL role ' + str(role) + '; use one of ' + ', '.join(sorted(ROLES)))
    if not isinstance(agent_id, str) or not agent_id.strip() or len(agent_id) > 200:
        refuse('agent id must be the nonempty id the host returned for the dispatched agent')
    rows = st.setdefault('agent_instances', [])
    previous = latest(st, role, writer)
    if previous and previous['id'] != agent_id and not fresh_reason:
        # Bookkeeping never blocks: record it, but say that the warm agent was the default.
        print('AL · warning: a warm ' + role + ' agent ' + previous['id'] + ' was recorded; follow-ups resume it with '
              + resume_call(previous['id'], previous.get('platform')) + '. Pass --fresh-reason when a new agent is '
              'required for independence.', file=sys.stderr)
    if previous and previous['id'] == agent_id:
        previous['uses'] = previous.get('uses', 1) + 1
        previous['last_at'] = time.time()
        if purpose:
            previous.setdefault('purposes', []).append(purpose)
        return previous
    row = {'role': role, 'id': agent_id.strip(), 'platform': platform(), 'at': time.time(), 'uses': 1}
    if writer:
        row['writer'] = writer
    if purpose:
        row['purposes'] = [purpose]
    if fresh_reason:
        row['fresh_reason'] = fresh_reason
    rows.append(row)
    return row


def cmd_agent(a, sid):
    st = al_store.load(sid)
    if a.action == 'record':
        row = record(st, a.role, a.id, a.writer, a.purpose, a.fresh_reason)
        al_store.save(sid, st)
        print('AL · recorded ' + row['role'] + ' agent ' + row['id'] + (' (fresh: ' + row['fresh_reason'] + ')' if row.get('fresh_reason') else '')
              + '; follow-ups: ' + resume_call(row['id'], row.get('platform')))
    elif a.action == 'resume':
        line = instruction(st, a.role, a.writer)
        if not line:
            print('AL · no ' + a.role + ' agent recorded yet: dispatch one, then `agent record --role ' + a.role + ' --id <agent-id>`')
            return
        print(line)
    elif a.action == 'usage':
        if a.role not in ROLES:
            refuse('unknown AL role ' + str(a.role))
        if a.output_tokens < 0 or (a.input_tokens or 0) < 0:
            refuse('token counts must be nonnegative')
        row = st.setdefault('output_tokens', {}).setdefault(a.role, {'output': 0, 'input': 0, 'invocations': 0})
        row['output'] += a.output_tokens
        row['input'] += a.input_tokens or 0
        row['invocations'] += 1
        al_store.save(sid, st)
        print('AL · ' + a.role + ' output tokens ' + str(row['output']) + ' over ' + str(row['invocations']) + ' invocation(s)')
    else:
        rows = st.get('agent_instances', [])
        if not rows:
            print('AL · no agents recorded')
        for r in rows:
            print(r['role'] + ' ' + r['id'] + ' uses=' + str(r.get('uses', 1)) + (' writer=' + r['writer'] if r.get('writer') else '')
                  + (' fresh: ' + r['fresh_reason'] if r.get('fresh_reason') else ''))


def note(st, role, writer=None):
    """Print the warm-agent instruction to stderr (stdout may carry a prompt that must stay intact)."""
    line = instruction(st, role, writer)
    if line:
        print(line, file=sys.stderr)


def register(sub):
    p = sub.add_parser('agent', help='record dispatched agent instances and print how to resume them (warm by default)')
    s = p.add_subparsers(dest='action', required=True)
    r = s.add_parser('record')
    r.add_argument('--role', required=True)
    r.add_argument('--id', required=True, help='the agent id the host returned (Claude Agent result / Codex spawn_agent id)')
    r.add_argument('--writer')
    r.add_argument('--purpose')
    r.add_argument('--fresh-reason', help='why a new agent is required instead of resuming (independence)')
    r = s.add_parser('resume')
    r.add_argument('--role', required=True)
    r.add_argument('--writer')
    r = s.add_parser('usage', help="add one invocation's token usage as the host reported it (per role; inspect shows it)")
    r.add_argument('--role', required=True)
    r.add_argument('--output-tokens', type=int, required=True)
    r.add_argument('--input-tokens', type=int)
    r.add_argument('--id')
    s.add_parser('list')
    p.set_defaults(fn=cmd_agent)
