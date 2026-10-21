"""Immutable original executions and independently bound destination associations."""
import copy
import json
import al_artifacts as A
import al_execution_projection as Projection
import al_ledger as L
import al_verification_inputs as Inputs
import al_verification_prepare as Preparation
import al_execution_versions as Versions

execution_identity = Versions.execution_identity

project_execution = Projection.project_execution


def _archive(st, value):
    return A.snapshot_bytes(st['session_id'], json.dumps(value, sort_keys=True, allow_nan=False).encode(),
                            value['kind'], 'immutable run-bound execution proof')


def _read(ref, kind):
    value = A.read_json(ref)
    if not isinstance(value, dict):raise ValueError('unsupported execution proof artifact')
    Versions.version(value, kind)
    if ref.get('kind') != value['kind']:raise ValueError('execution artifact reference kind differs')
    return value


def seal_execution(st, entry, projection, completion):
    if Projection.current_gaps(st, projection):raise ValueError('execution inputs changed before sealing')
    if (entry.get('status') != 'PASS' or entry.get('exit') != 0 or not entry.get('pid')
            or not entry.get('reservation') or not entry.get('ended_at') or entry.get('interrupted')
            or entry.get('reuse') or entry.get('execution_association') or entry.get('fingerprint_error')
            or entry.get('fp_before') != entry.get('fp_after')):
        raise ValueError('only stable completed original execution can be sealed')
    if (completion.get('complete') is not True or completion.get('errors') or completion.get('unresolved')
            or completion.get('pid') != entry['pid'] or completion.get('exit') != 0
            or completion.get('ownership') != 'live-Popen-start_new_session'
            or not completion.get('token') or completion.get('group_absent') is not True):
        raise ValueError('owned execution cleanup is not positively complete')
    if not entry.get('log_sha256') or L.file_sha(entry['log']) != entry['log_sha256']:
        raise ValueError('execution log changed before sealing')
    number = Versions.version(projection, 'execution-projection')
    result = {'schema_version': number, 'kind': 'execution-receipt-v'+str(number),
              'projection': copy.deepcopy(projection), 'projection_digest': Inputs.digest(projection),
              'original_evidence_id': entry['id'], 'original_reservation': entry['reservation'],
              'before_observation': entry['fp_before'], 'after_observation': entry['fp_after'],
              'process_ownership': copy.deepcopy(completion), 'cleanup': copy.deepcopy(completion),
              'status': entry['status'], 'exit_code': entry['exit'], 'counts': entry.get('counts', {}),
              'log_path': entry['log'], 'log_sha256': entry['log_sha256'],
              'generated_output_disposition': 'unchanged',
              'measured_durations': {'started_at': entry['started_at'], 'ended_at': entry['ended_at']}}
    if number == 2:result['execution_identity_digest'] = execution_identity(projection)
    return _archive(st, result)


def _later_gaps(st, original, projection, exclude_reservation=None):
    entries = L.ledger(st)['entries'];position = entries.index(original)
    identity = execution_identity(projection);argv = projection['execution_profile']['argv']
    for entry in entries[position+1:]:
        if entry.get('reservation') == exclude_reservation:continue
        same = entry.get('execution_projection_digest') == identity
        pending = entry.get('status') == 'RUNNING' and entry.get('argv') == argv
        if not same and not pending:continue
        cleanup = entry.get('cleanup') or {}
        if (entry.get('status') != 'PASS' or entry.get('interrupted')
                or cleanup.get('errors') or cleanup.get('unresolved')):
            return ['later failed, running or uncertain execution prevents fallback']
    return []


def _receipt_gaps(st, ref, projection, exclude_reservation=None):
    receipt = _read(ref, 'execution-receipt')
    number = Versions.version(receipt, 'execution-receipt')
    original_projection = receipt['projection']
    if (Versions.version(original_projection, 'execution-projection') != number
            or receipt['projection_digest'] != Inputs.digest(original_projection)
            or not Versions.equivalent(st, original_projection, projection, 'execution-projection')
            or number == 2 and receipt.get('execution_identity_digest') != execution_identity(original_projection)):
        return ['execution projection differs']
    gaps = Projection.current_gaps(st, projection)
    original = L.find(st, receipt['original_evidence_id'])
    if (not original or original.get('execution_receipt') != ref or original.get('reuse')
            or original.get('execution_association') or original.get('status') != 'PASS'
            or original.get('exit') != 0 or original.get('interrupted') or not original.get('ended_at')
            or original.get('reservation') != receipt['original_reservation']
            or original.get('execution_projection_digest') != execution_identity(original_projection)):
        return gaps + ['original execution provenance differs']
    if (original.get('argv') != projection['execution_profile']['argv']
            or original.get('execution_completion') != receipt['process_ownership']
            or original.get('counts', {}) != receipt['counts']
            or receipt['measured_durations'] != {'started_at':original['started_at'],'ended_at':original['ended_at']}):
        gaps.append('original command, ownership or result differs')
    for field, key in [('log', 'log_path'), ('log_sha256', 'log_sha256'),
                       ('fp_before', 'before_observation'), ('fp_after', 'after_observation')]:
        if original.get(field) != receipt[key]:gaps.append('original execution field differs: '+field)
    proof = receipt['process_ownership']
    if (receipt['cleanup'] != proof or not proof.get('complete') or proof.get('errors')
            or proof.get('unresolved') or not proof.get('group_absent') or not proof.get('token')
            or proof.get('pid') != original.get('pid') or proof.get('exit') != 0
            or receipt['status'] != 'PASS' or receipt['exit_code'] != 0
            or receipt['before_observation'] != receipt['after_observation']):
        gaps.append('original execution ownership/cleanup incomplete')
    if L.file_sha(receipt['log_path']) != receipt['log_sha256']:gaps.append('original execution log altered')
    return gaps + _later_gaps(st, original, projection, exclude_reservation)


def validate_receipt(st, ref, projection, exclude_reservation=None):
    try:return _receipt_gaps(st, ref, projection, exclude_reservation)
    except (OSError, ValueError, KeyError, TypeError, SystemExit, L.FingerprintError):
        return ['unreadable or malformed original execution receipt']


def reuse_candidate(st, projection, *, exclude_reservation=None):
    identity = execution_identity(projection)
    argv = projection['execution_profile']['argv']
    # Newest relevant attempt wins, even when it has not reached projection
    # publication yet. There is no search past a later failed/running attempt.
    for entry in reversed(L.ledger(st)['entries']):
        if entry.get('reservation') == exclude_reservation:continue
        same = entry.get('execution_projection_digest') == identity
        pending = entry.get('status') == 'RUNNING' and entry.get('argv') == argv
        if not same and not pending:continue
        if entry.get('status') != 'PASS' or entry.get('interrupted'):return None
        if entry.get('execution_association'):
            # Associations never become receipt sources or conceal an original.
            if validate_association(st, entry, exclude_reservation):return None
            continue
        ref = entry.get('execution_receipt')
        return ref if ref and not validate_receipt(st, ref, projection, exclude_reservation) else None
    return None


def associate(st, destination, receipt_ref, reservation, observation):
    projection = observation['projection'];preparation = observation['preparation']
    entry = next((e for e in L.ledger(st)['entries'] if e.get('reservation') == reservation), None)
    if (not entry or entry['status'] != 'RUNNING' or entry.get('pid') is not None
            or entry.get('run') != st['run_id'] or preparation['destination'] != destination):
        raise ValueError('destination association lacks its own live reservation')
    if validate_receipt(st, receipt_ref, projection, reservation):raise ValueError('original execution receipt is not reusable')
    latest = reuse_candidate(st, projection, exclude_reservation=reservation)
    if latest != receipt_ref:raise ValueError('a later attempt prevents execution reuse')
    selected = {'area': destination['area_or_gate']} if destination['kind'] == 'area' else {
        'gate': destination['area_or_gate'], 'stage': destination['stage']}
    current = Preparation.prepare(st, **selected, no_cache=not preparation['cache_policy']['enabled'])
    if not Preparation.preparation_equivalent(st, preparation, current) or current['blockers']:raise ValueError('destination inputs changed before association')
    number = Versions.version(projection, 'execution-projection')
    value = {'schema_version': number, 'kind': 'execution-association-v'+str(number),
             'receipt_ref': receipt_ref, 'repository_identity': st['identity'],
             'run_id': st['run_id'], 'destination': destination, 'reservation': reservation,
             'current_observation': copy.deepcopy(observation)}
    if number == 1:value['task_hash'] = projection['task_hash']
    else:
        original_projection = _read(receipt_ref, 'execution-receipt')['projection']
        value.update(document_hash=projection['document_hash'],
                     verification_authority=projection['verification_authority'],
                     editorial_lineage=Versions.bridge(st, original_projection, projection))
    return _archive(st, value)


def validate_association(st, entry, exclude_reservation=None):
    try:
        value = _read(entry['execution_association'], 'execution-association')
        observation = value['current_observation'];projection = observation['projection']
        number = Versions.version(value, 'execution-association')
        if Versions.version(projection, 'execution-projection') != number:
            return ['association projection version differs']
        if number == 2:
            original_projection = _read(value['receipt_ref'], 'execution-receipt')['projection']
            if (value['document_hash'] != projection['document_hash']
                    or value['verification_authority'] != projection['verification_authority']
                    or value['editorial_lineage'] != Versions.bridge(st, original_projection, projection)):
                return ['association editorial lineage differs']
        if (value['repository_identity'] != st['identity'] or value['run_id'] != st['run_id']
                or number == 1 and value['task_hash'] != projection['task_hash']
                or value['reservation'] != entry['reservation']
                or entry.get('pid') is not None or entry.get('execution_receipt') or entry.get('reuse')
                or entry.get('status') != 'PASS' or entry.get('exit') != 0):
            return ['foreign or chained execution association']
        preparation = observation['preparation'];target = value['destination']
        if preparation['destination'] != target:return ['association destination differs']
        selected = {'area': target['area_or_gate']} if target['kind'] == 'area' else {
            'gate': target['area_or_gate'], 'stage': target['stage']}
        current = Preparation.prepare(st, **selected, no_cache=not preparation['cache_policy']['enabled'])
        if not Preparation.preparation_equivalent(st, preparation, current) or current['blockers']:return ['destination preparation changed']
        expected = 'gate-'+target['stage']+'-'+target['area_or_gate'] if target['kind'] == 'gate' else target['area_or_gate']
        fields = {'argv':'argv','cwd':'cwd','scope':'scope','sources':'sources','env':'env'}
        if entry['area'] != expected or any(entry.get(k,[]) != preparation['spec'].get(v,[]) for k,v in fields.items()):
            return ['association destination execution differs']
        return validate_receipt(st, value['receipt_ref'], projection, exclude_reservation)
    except (OSError, ValueError, KeyError, TypeError, SystemExit, L.FingerprintError):
        return ['unreadable or malformed execution association']
