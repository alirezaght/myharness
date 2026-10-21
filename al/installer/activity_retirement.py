"""Exact historical inactivity facts for workspace deployment, never tagged guards."""
import json
import os
from pathlib import Path

from .activity_inventory import inventory_activity
from .activity_profiles import classify_artifact, decode, SHA
from .engine import R
from .registry import canonical, digest

FACTS_SOURCE_SHA = '5327a0cce8a4341479679bd7a14d7793edd53ea139e3aeada540bbee6a07e24d'
FACTS = (
    ('other-repositories', 'All other AL sessions are inactive or abandoned'),
    ('older-myharness-sessions', 'All older myharness sessions are inactive or abandoned'),
)


def seal(value):
    json.dumps(value, allow_nan=False)
    return dict(value, digest=digest(canonical(value)))


def validate_seal(value, kind):
    if (not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('kind') != kind
            or value.get('digest') != digest(canonical({k: v for k, v in value.items() if k != 'digest'}))):
        raise ValueError('invalid immutable ' + kind)
    return value


def read_reference(reference):
    if not isinstance(reference, dict) or set(reference) != {'path', 'sha256', 'digest'} or not SHA.fullmatch(reference['sha256']):
        raise ValueError('invalid external activity artifact reference')
    path = R.safe_absolute(reference['path'])
    raw = path.read_bytes()
    if digest(raw) != reference['sha256']:raise ValueError('external activity artifact changed')
    value = decode(raw)
    if value.get('digest') != reference['digest']:raise ValueError('external activity artifact identity changed')
    return value


def publish_once(path, value):
    path = R.safe_absolute(path)
    raw = canonical(value) + b'\n'
    if path.exists():
        if path.read_bytes() != raw:raise ValueError('immutable activity artifact already binds different history')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            if path.read_bytes() != raw:raise ValueError('immutable activity artifact publication conflict')
        else:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw);stream.flush();os.fsync(stream.fileno())
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
    return {'path': str(path), 'sha256': digest(raw), 'digest': value['digest']}


def _records(inventory):
    return [(root, record) for root in inventory['roots'] for record in root['records']]


def _covered(root, record):
    return {'identity': record['identity'], 'sha256': record['sha256'], 'mode': record['mode'],
            'profile': record['profile'], 'historical_status': record['historical_status'],
            'historical_phase': record['historical_phase'],
            'pointer_bindings': [p for p in root['pointers'] if p['session_id'] == record['identity']['session_id']],
            'runtime_membership_digest': digest(canonical(root['members']))}


def plan_acknowledgements(inventory, statements, *, excluded_run):
    validate_seal(inventory, 'workspace-activity-inventory')
    if not inventory['stable'] or any(r['errors'] for r in inventory['roots']):raise ValueError('incomplete activity inventory')
    expected = {'source_artifact', 'source_binding', 'deployment_intent_digest', 'myharness_identity', 'directory', 'history'}
    if set(statements) != expected or excluded_run != inventory['invoking_run']:
        raise ValueError('invalid scoped acknowledgement request')
    if statements['myharness_identity'] != excluded_run['repository']:
        raise ValueError('myharness acknowledgement identity mismatch')
    if not SHA.fullmatch(statements['deployment_intent_digest']):raise ValueError('missing deployment intent')
    source = statements['source_artifact']
    if set(source) != {'path', 'sha256'} or source['sha256'] != FACTS_SOURCE_SHA:raise ValueError('unsupported original factual artifact')
    path = R.safe_absolute(source['path']);raw = path.read_bytes()
    if digest(raw) != source['sha256']:raise ValueError('original factual acknowledgements changed')
    facts = decode(raw)
    if (not isinstance(facts, dict) or set(facts) != {'schema_version', 'kind', 'source', 'recorded_at', 'current_run_excluded', 'acknowledgements', 'limits'}
            or facts['schema_version'] != 1 or facts['kind'] != 'user-factual-activity-acknowledgements'
            or facts['current_run_excluded'] != excluded_run['session_id'] or not facts['source'] or not facts['recorded_at']
            or len(facts['acknowledgements']) != 2):
        raise ValueError('unsupported original factual acknowledgement artifact')
    directory = R.safe_absolute(statements['directory'])
    for root in inventory['roots']:
        repo = Path(root['repository']['root'])
        if directory == repo or repo in directory.parents:raise ValueError('acknowledgements must be outside repositories')
    from .activity_history import bind_reviewed_history
    history = bind_reviewed_history(inventory, statements['history'], directory=directory, publish=publish_once)
    result = []
    for index, (scope, answer) in enumerate(FACTS):
        statement = facts['acknowledgements'][index]
        if set(statement) != {'question', 'answer'} or statement['answer'] != answer:
            raise ValueError('factual answer/scope mismatch')
        question = statement['question']
        if (not isinstance(question, str) or not question.strip()
                or (scope == 'other-repositories' and 'other repositories' not in question)
                or (scope == 'older-myharness-sessions' and 'older AL sessions inside myharness' not in question)):
            raise ValueError('factual question/scope mismatch')
        covered = []
        for root, record in _records(inventory):
            if record['classification'] == 'unknown' or record['errors']:raise ValueError('unknown activity cannot be acknowledged')
            if record['classification'] == 'nonstate-artifact' or record['identity'] == excluded_run:continue
            same_repo = record['identity']['repository'] == statements['myharness_identity']
            if same_repo != (scope == 'older-myharness-sessions'):continue
            covered.append(_covered(root, record))
        body = {'schema_version': 1, 'kind': 'installation-inactivity-acknowledgement',
                'statement': {'source_artifact': source, 'item_index': index, **statement,
                              'conversation_provenance': {'source': facts['source'], 'recorded_at': facts['recorded_at']}},
                'scope': scope, 'reviewed_history': history, 'myharness_identity': statements['myharness_identity'], 'excluded_run': excluded_run,
                'source_binding': statements['source_binding'], 'deployment_intent_digest': statements['deployment_intent_digest'],
                'inventory_digest': inventory['digest'], 'covered_records': covered,
                'meaning': 'installation-time inactivity only',
                'does_not_authorize': ['approval', 'completion', 'evidence-repair', 'resume']}
        acknowledgement = seal(body)
        key = digest(canonical({'statement': source, 'scope': scope, 'source_binding': statements['source_binding'],
                                'deployment_intent_digest': statements['deployment_intent_digest']}))
        result.append(publish_once(directory / (key + '.json'), acknowledgement))
    return {'schema_version': 1, 'kind': 'workspace-acknowledgement-references', 'inventory_digest': inventory['digest'], 'reviewed_history': history, 'artifacts': result}


def require_workspace_quiescent(plan, acknowledgements, *, observation):
    activity = plan['activity']
    if not {'inventory', 'reviewed_history', 'acknowledgements', 'deployment_intent_digest'} <= set(activity) or acknowledgements != activity['acknowledgements']:
        raise ValueError('incomplete independently bound workspace activity coverage')
    inventory = validate_seal(read_reference(activity['inventory']), 'workspace-activity-inventory')
    from .activity_history import reviewed_history
    history = validate_seal(read_reference(activity['reviewed_history']), 'reviewed-historical-activity-anchor')
    if reviewed_history(inventory, history['original_preparation']) != history:
        raise ValueError('historical runtime coverage/membership changed')
    validate_seal(observation, 'workspace-quiescence-observation')
    from .activity_observation import validate_fresh_observation
    validate_fresh_observation(observation, inventory)
    if (not inventory['stable'] or observation['inventory_digest'] != inventory['digest'] or not observation['stable']
            or observation['errors'] or observation.get('invoking_run') != inventory['invoking_run']):
        raise ValueError('incomplete or foreign activity observation')
    acks = [validate_seal(read_reference(ref), 'installation-inactivity-acknowledgement') for ref in acknowledgements]
    allowed = {}
    for acknowledgement in acks:
        if set(acknowledgement) != {'schema_version', 'kind', 'statement', 'scope', 'reviewed_history',
                                   'myharness_identity', 'excluded_run', 'source_binding', 'deployment_intent_digest',
                                   'inventory_digest', 'covered_records', 'meaning', 'does_not_authorize', 'digest'}:
            raise ValueError('unknown acknowledgement authority fields')
        if (acknowledgement['inventory_digest'] != inventory['digest']
                or acknowledgement['excluded_run'] != inventory['invoking_run']
                or acknowledgement['source_binding'] != plan['source']
                or acknowledgement['deployment_intent_digest'] != activity['deployment_intent_digest']):
            raise ValueError('acknowledgement deployment binding mismatch')
        scopes = [scope for scope, _ in FACTS]
        scope = acknowledgement.get('scope')
        if scope not in scopes or acknowledgement.get('myharness_identity') != inventory['invoking_run']['repository']:
            raise ValueError('foreign acknowledgement factual scope')
        index = scopes.index(scope)
        statement = acknowledgement.get('statement', {})
        source = statement.get('source_artifact', {})
        if (statement.get('item_index') != index or statement.get('answer') != FACTS[index][1]
                or source.get('sha256') != FACTS_SOURCE_SHA):
            raise ValueError('unsupported acknowledgement factual provenance')
        raw = R.safe_absolute(source['path']).read_bytes()
        facts = decode(raw)
        if digest(raw) != FACTS_SOURCE_SHA or facts['acknowledgements'][index] != {k: statement[k] for k in ('question', 'answer')}:
            raise ValueError('original acknowledgement facts changed')
        expected_records = [_covered(root, record) for root, record in _records(inventory)
                            if record['classification'] not in {'unknown', 'nonstate-artifact'}
                            and record['identity'] != inventory['invoking_run']
                            and (record['identity']['repository'] == inventory['invoking_run']['repository']) == (scope == 'older-myharness-sessions')]
        if acknowledgement['covered_records'] != expected_records:
            raise ValueError('acknowledgement widens or omits exact factual scope')
        if (acknowledgement.get('meaning') != 'installation-time inactivity only'
                or acknowledgement.get('does_not_authorize') != ['approval', 'completion', 'evidence-repair', 'resume']):
            raise ValueError('acknowledgement authority changed')
        if acknowledgement['reviewed_history'] != activity['reviewed_history']:
            raise ValueError('acknowledgement historical coverage anchor changed')
        for record in acknowledgement['covered_records']:
            key = canonical(record['identity'])
            if key in allowed:raise ValueError('overlapping historical acknowledgements')
            allowed[key] = (acknowledgement, record)
    records, blockers = [], []
    invoking_seen = False
    for root, record in _records(inventory):
        reasons = list(record['errors']) + list(root['errors'])
        ident = record['identity'];ack_digest = None
        assessment = 'blocked'
        if record['classification'] == 'nonstate-artifact':assessment = 'preserved-nonstate'
        elif record['classification'] == 'unknown':reasons.append('unknown/corrupt runtime artifact')
        elif ident == inventory['invoking_run']:
            invoking_seen = True
            if record['historical_status'] != 'APPROVED' or record['historical_phase'] != 'DONE':
                reasons.append('invoking run is not independently APPROVED/DONE')
            else:assessment = 'ordinary-terminal'
        elif record['historical_status'] == 'APPROVED' and record['historical_phase'] == 'DONE':assessment = 'ordinary-terminal'
        else:
            match = allowed.get(canonical(ident))
            if not match or match[1] != _covered(root, record):reasons.append('historical inactivity is not exactly acknowledged')
            else:assessment = 'acknowledged-inactive-history';ack_digest = match[0]['digest']
        from .activity_operations import operation_reasons
        reasons.extend(operation_reasons(record, root, observation))
        if reasons:assessment = 'blocked';blockers.extend(reasons)
        records.append({'identity': ident, 'assessment': assessment, 'acknowledgement_digest': ack_digest, 'reasons': reasons})
    if any(o.get('subject_kind') in {'orphan-lock', 'runtime-root'} and o['result'] not in {'absent', 'different-process'} for o in observation['processes']):
        blockers.append('live or unknown orphan-lock/runtime-root process')
    if not invoking_seen:blockers.append('invoking run absent from complete activity inventory')
    if any(o['result'] != 'held' for o in observation['locks']):blockers.append('required runtime lock is not held')
    if any(o['result'] != 'absent' for o in observation['leases']):blockers.append('live or unknown lease')
    assessment = seal({'schema_version': 1, 'kind': 'workspace-quiescence-assessment', 'eligible': not blockers,
                       'records': records, 'inventory_digest': inventory['digest'], 'observation_digest': observation['digest'],
                       'limitations': observation.get('limitations', []), 'blockers': blockers})
    if blockers:raise ValueError('workspace activity blocked: ' + '; '.join(sorted(set(blockers))))
    return assessment
