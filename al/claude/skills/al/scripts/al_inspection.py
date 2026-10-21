"""Explicitly bound observational inspection; no locks, writes or current fallback."""
import hashlib
import json
from pathlib import Path

import al_store as S
import al_scope
from al_analysis_cache import ObservationContext
from al_schema import refuse

DEFAULT_SECTIONS = ('summary', 'evidence', 'gates', 'operations')
SECTIONS = (*DEFAULT_SECTIONS, 'attribution', 'publication')


def source_binding(state):
    base = state['identity']['merge_base'] if state.get('review_mode') == 'whole-mr' else state['baseline_head']
    content = {'base': base, 'target': state['identity']['target_oid'], 'files': al_scope.records(base)}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def inspect_session(sid, *, expected_run, expected_repo, sections):
    if not sid or not expected_run or not expected_repo:
        refuse('inspect requires explicit session/run/repository binding')
    S._sanitise(sid)
    if Path(expected_repo).resolve() != S.REPO_ROOT.resolve():
        refuse('inspect repository binding mismatch')
    if not sections or set(sections) - set(SECTIONS):
        refuse('invalid inspect sections')
    path = S.state_path(sid)
    before = path.read_bytes()
    state = S.load(sid)
    if path.read_bytes() != before or state['run_id'] != expected_run or state['session_id'] != sid:
        refuse('inspect state/run binding mismatch')
    if Path(state['identity']['root']).resolve() != Path(expected_repo).resolve():
        refuse('inspect pinned repository mismatch')
    source = source_binding(state)
    digest = hashlib.sha256(before).hexdigest()
    observation = {'state': digest, 'source': source, 'run': expected_run}
    output = {}
    with ObservationContext(observation, readonly_git=True):
        if 'publication' in sections:
            from al_publication import publication_readiness
            output['publication'] = publication_readiness(state)
        if 'attribution' in sections:
            from al_task_reconciliation import view
            output['attribution'] = view(state)
        if 'summary' in sections:
            output['summary'] = {k: state.get(k) for k in ('status', 'phase', 'classification', 'spec_status', 'model', 'effort', 'updated_at')}
        if 'evidence' in sections:
            import al_ledger as L
            output['evidence'] = []
            for e in L.ledger(state)['entries']:
                verdict, reason = L.entry_verdict(state, e)
                output['evidence'].append({'id': e['id'], 'area': e['area'], 'status': e['status'], 'counts': e.get('counts', {}), 'verdict': verdict, 'reason': reason})
        if 'gates' in sections:
            import al_gate_currency
            import al_gates
            output['gates'] = {'currency': al_gate_currency.check(state), 'base': al_gates.base_gaps(state), 'candidate': al_gates.candidate_gaps(state)}
        if 'operations' in sections:
            import al_writers
            output['operations'] = {'gaps': al_writers.operation_gaps(state),
                                    'writers': [w for w in state['writers'].values() if w['state'] in {'ACTIVE', 'UNRESOLVED'}],
                                    'checks': [e['id'] for e in state['evidence']['entries'] if e['status'] == 'RUNNING' or (e.get('interrupted') and not e.get('reconciliation'))]}
    stable = path.read_bytes() == before and source_binding(state) == source
    if 'attribution' in sections:
        with ObservationContext(observation, readonly_git=True):
            stable = stable and view(state) == output['attribution']
    return {'schema_version': 1, 'session_id': sid, 'run_id': expected_run, 'repository': str(S.REPO_ROOT.resolve()),
            'state_digest': digest, 'observation_binding': observation, 'stable': stable,
            'sections': output, 'errors': [] if stable else ['state or source moved during inspection']}


def cmd_inspect(a, sid):
    try:
        result = inspect_session(sid, expected_run=a.expect_run, expected_repo=a.expect_repo, sections=a.sections.split(','))
    except (OSError, ValueError, KeyError, SystemExit) as exc:
        print(json.dumps({'schema_version': 1, 'stable': False, 'errors': [str(exc)]}))
        raise SystemExit(1)
    print(json.dumps(result, sort_keys=True))
    if not result['stable']:
        raise SystemExit(1)


def register(sub):
    parser = sub.add_parser('inspect')
    parser.add_argument('--expect-run', required=True)
    parser.add_argument('--expect-repo', required=True)
    parser.add_argument('--sections', default=','.join(DEFAULT_SECTIONS))
    parser.set_defaults(fn=cmd_inspect)
