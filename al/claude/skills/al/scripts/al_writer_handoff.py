"""State-bound complete writer prompts; generation is not dispatch or evidence."""
import json
import shlex
import sys
from pathlib import Path

import al_store
from al_artifacts import digest, snapshot_bytes
from al_schema import refuse

BEGIN = '<!-- AL MANAGED WRITER EVIDENCE BEGIN -->'
END = '<!-- AL MANAGED WRITER EVIDENCE END -->'


def evidence_block(st, sid, writer_id, areas):
    writer = st['writers'][writer_id]
    platform = Path(__file__).resolve().parents[3].name
    prefix = ['alctl', 'run', '--repository', st['identity']['root'], '--platform',
              platform, '--session', sid, '--existing-run', '--']
    lines = [BEGIN, 'EVIDENCE — authoritative writer bindings',
             'Writer: ' + writer_id + ' · role: ' + writer['role'] + ' · phase: ' + writer['phase'],
             'Ownership: ' + json.dumps(writer['files']),
             'Repository: ' + st['identity']['root'], 'Platform: ' + platform, 'AL session: ' + sid, 'AL run: ' + st['run_id'],
             'Run every reported check through the ledger; never run bare checks and claim evidence.',
             'Native launcher commands (run from the repository root):']
    for area in areas:
        spec = st['evidence']['plan'][area]
        if spec.get('recipe_context'):
            lines += ['Supervisor-only prepared check: ' + area + '; request the Supervisor to run this broad default.']
            continue
        if spec.get('recipe'):
            lines += ['Named managed recipe: ' + spec['recipe']['name'] + ' (independent partition evidence; CI obligations unchanged)']
        lines += ['Area ' + area + ': tier=' + spec['tier'] + '; preflight=' +
                  str(spec.get('preflight') or 'none') + '; needs=' + json.dumps(spec.get('needs') or []),
                  shlex.join(prefix + ['evidence', 'run', '--writer', writer_id, '--area', area])]
    if 'integration' in st:
        lines += ['Harness tool route: use al_command; the controller binds the worker to its own writer.']
        lines += ['al_command ' + json.dumps(['evidence', 'run', '--area', area]) for area in areas if not st['evidence']['plan'][area].get('recipe_context')]
    lines += ['Report the ledger\'s actual E<n> · area → PASS | FAIL | EMPTY | INFRA and counts, or NOT RUN with a reason.',
              'Register any additional evidence area before regenerating this handoff. Generation runs no checks.',
              'This receipt records generation, not host delivery. Regenerate after relevant plan, writer, or phase changes.', END]
    return '\n'.join(lines)


def cmd_handoff(a, sid):
    st = al_store.load(sid)
    writer = st['writers'].get(a.id)
    if st['status'] != 'RUNNING': refuse('handoff requires a running run')
    if not writer or writer['state'] != 'ACTIVE' or writer['role'] not in {'al-engineer', 'al-builder'}:
        refuse('handoff requires an active Engineer or Builder writer')
    if writer['phase'] != st['phase']: refuse('handoff writer phase does not match current phase')
    try:
        body = Path(a.body_file).read_bytes().decode('utf-8')
    except (OSError, UnicodeError):
        refuse('handoff body must be a readable UTF-8 file')
    if not body.strip(): refuse('handoff body must be nonempty')
    if BEGIN in body or END in body: refuse('handoff body contains a managed evidence block')
    plan = st['evidence']['plan']
    areas = sorted(set(a.area if a.area is not None else plan))
    if not areas: refuse('handoff requires registered evidence areas')
    if any(area not in plan for area in areas): refuse('handoff names an unknown evidence area')
    prompt = body + '\n\n' + evidence_block(st, sid, a.id, areas) + '\n'
    raw = prompt.encode('utf-8')
    plan_digest = digest(json.dumps({area: plan[area] for area in areas}, sort_keys=True,
                                  separators=(',', ':')).encode('utf-8'))
    artifact = snapshot_bytes(sid, raw, 'writer-handoff', a.body_file)
    writer.setdefault('handoffs', []).append({'artifact': artifact, 'areas': areas, 'plan_digest': plan_digest})
    import al_agents
    if getattr(a, 'agent_id', None):
        al_agents.record(st, writer['role'], a.agent_id, writer=a.id, purpose='handoff', fresh_reason=getattr(a, 'fresh_reason', None))
    al_store.save(sid, st)
    sys.stdout.write(prompt)
    # Warm by default (#30): a follow-up for this role goes to the recorded agent instance.
    al_agents.note(st, writer['role'])
