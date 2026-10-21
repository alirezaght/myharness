"""The existing factual answers are bounded to the already observed historical set."""
from pathlib import Path

from .activity_profiles import decode
from .engine import R
from .registry import canonical, digest

ANCHORS = {
    'scope': 'c9cf4878fee1492f1b39fae7a4fe17f3ee4df4eb50360a14a3c7859744b13445',
    'states': '3e2bb00c19fce7bac1220393d172dca430e431e7fdaf2d9848d0d90b05ce5abc',
    'nonstate': '3ce42e0a995c87417d16bd1bfc6d191be63b28322da1792d1adb1e08ac794109',
}


def _read(kind, reference):
    if not isinstance(reference, dict) or set(reference) != {'path', 'sha256'} or reference['sha256'] != ANCHORS[kind]:
        raise ValueError('unsupported original historical preparation anchor')
    raw = R.safe_absolute(reference['path']).read_bytes()
    if digest(raw) != ANCHORS[kind]:raise ValueError('original historical preparation changed')
    return decode(raw)


def reviewed_history(inventory, references):
    """Preparation is only an upper bound; live profiles/references must validate first."""
    if set(references) != set(ANCHORS):raise ValueError('missing original historical scope/bytes anchors')
    scope = _read('scope', references['scope'])
    states = _read('states', references['states'])
    nonstate = _read('nonstate', references['nonstate'])
    if not isinstance(scope.get('rows'), list) or not isinstance(states.get('members'), list) or not isinstance(nonstate.get('rows'), list):
        raise ValueError('unsupported historical preparation shape')
    original = {str(Path(row['path']).resolve()): row for row in scope['rows']}
    expected = {str(Path(row['path']).resolve()): row for row in [*states['members'], *nonstate['rows']]}
    if set(original) != set(expected) or len(original) != len(scope['rows']) or len(expected) != len(states['members']) + len(nonstate['rows']):
        raise ValueError('original historical receipt membership is incomplete')
    roots = {str(Path(row['path']).resolve().parent) for row in scope['rows']}
    invoking = inventory['invoking_run']
    observed_roots = [root['runtime_root'] for root in inventory['roots']]
    if len(set(observed_roots)) != len(observed_roots) or not roots <= set(observed_roots):
        raise ValueError('incomplete original historical runtime-root coverage')
    current = [row for row in states['members'] if row.get('is_current_run') is True]
    if (len(current) != 1 or str(Path(current[0]['path']).resolve()) != invoking['state_path']
            or current[0]['session_id'] != invoking['session_id'] or current[0]['run_id'] != invoking['run_id']):
        raise ValueError('original invoking run exclusion changed')
    reviewed = []
    memberships = []
    for root in inventory['roots']:
        runtime = root['runtime_root']
        actual = {record['identity']['state_path']: record for record in root['records'] if record['identity'] != invoking}
        wanted = {path for path in expected if str(Path(path).parent) == runtime
                  and not states_current(path, states['members'])}
        if set(actual) != wanted:
            raise ValueError('new/missing historical sessions are outside the existing factual answers')
        for path, record in actual.items():
            reference = expected[path]
            if (record['errors'] or record['classification'] == 'unknown'
                    or record['sha256'] != reference['sha256'] or record['mode'] != reference['mode']):
                raise ValueError('reviewed historical state bytes/mode/profile changed')
            if record['classification'] != 'nonstate-artifact':
                for key, historical in (('session_id', 'session_id'), ('run_id', 'run_id')):
                    if record['identity'][key] != reference[historical]:raise ValueError('historical immutable identity changed')
            reviewed.append({'identity': record['identity'], 'sha256': record['sha256'], 'mode': record['mode'],
                             'profile': record['profile'],
                             'pointers': [p for p in root['pointers'] if p['session_id'] == record['identity']['session_id']]})
        if runtime not in roots:
            if root['members']:raise ValueError('unreviewed runtime membership outside original factual scope')
            continue
        excluded = next((r for r in root['records'] if r['identity'] == invoking), None)
        owned = set()
        if excluded:
            session = invoking['session_id']
            owned.update({session, session + '.json', '.' + session + '.lock'})
            owned.update(p['path'][len(runtime) + 1:] for p in root['pointers'] if p['session_id'] == session)
            for entry in excluded['operation_facts'].get('checks', []):
                log = entry.get('log')
                if log:
                    path = Path(log).resolve()
                    try:relative = path.relative_to(runtime).as_posix()
                    except ValueError:raise ValueError('excluded run log outside its runtime')
                    parts = Path(relative).parts
                    if len(parts) == 3 and parts[:2] == (session, 'logs'):continue
                    if len(parts) != 2 or parts[0] != 'logs':
                        raise ValueError('unsupported excluded-run log ownership layout')
                    owned.add(relative)
            log_members = [m['relative_path'] for m in root['members'] if m['relative_path'].startswith('logs/')]
            if log_members and all(path in owned for path in log_members):owned.add('logs')
        memberships.append({'runtime_root': runtime,
                            'members': [m for m in root['members'] if m['relative_path'] not in owned
                                        and not (excluded and m['relative_path'].startswith(invoking['session_id'] + '/'))]})
    body = {'schema_version': 1, 'kind': 'reviewed-historical-activity-anchor',
            'original_preparation': references, 'invoking_run': invoking, 'records': reviewed, 'runtime_membership': memberships}
    body['digest'] = digest(canonical(body))
    return body


def bind_reviewed_history(inventory, references, *, directory, publish):
    body = reviewed_history(inventory, references)
    return publish(Path(directory) / 'historical-scope-v1.json', body)


def states_current(path, rows):
    return any(row.get('is_current_run') is True and str(Path(row['path']).resolve()) == path for row in rows)
