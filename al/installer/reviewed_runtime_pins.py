"""Read-only independence proof for protected runtimes during one global update."""
import hashlib
import os
from pathlib import Path
import re
import stat
import sys

from .activity_operations import check_blockers, finite, reconciliation_validation, setup_checks
from .guards import ActiveRuns, activity_roots, scan_activity
from .registry import canonical, digest
from .runtime_pin import _directory, verify_pin
from . import runtime_pin_transaction as P
from .runtime_pin_initialization import read_initialization
from .validator_origin import capture, decode, executable_state, safe, verify_snapshot


def metadata(path):
    path = safe(path)
    if path != path.resolve(strict=True):
        raise ValueError('noncanonical protected runtime metadata path')
    before = path.stat()
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
            or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600):
        raise ValueError('protected runtime metadata ownership/mode/alias differs: ' + str(path))
    raw, info = capture(path)
    after = path.stat()
    if any(getattr(before, key) != getattr(after, key) for key in
           ('st_dev', 'st_ino', 'st_uid', 'st_nlink', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')):
        raise ValueError('protected runtime metadata moved during observation')
    return raw, info


def _bridge_manifest_bytes(path):
    """The existing authority snapshot producer permits a read-only 0644 manifest."""
    path = safe(path)
    before = path.stat()
    if (path != path.resolve(strict=True) or before.st_uid != os.getuid() or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) not in {0o600, 0o644}):
        raise ValueError('SUPERSEDED archived authority manifest ownership/mode/alias differs')
    raw, _ = capture(path)
    after = path.stat()
    if any(getattr(before, key) != getattr(after, key) for key in
           ('st_dev', 'st_ino', 'st_uid', 'st_nlink', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')):
        raise ValueError('SUPERSEDED archived authority manifest moved')
    return raw


def selected_storage(state_root):
    context = sys.modules.get('_al_validator_context')
    if (not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode
            or context is None or context.request.get('mode') != 'snapshot'
            or sys.modules.get('_al_run_pin_context') is not None
            or Path(context.request['root']) != Path(__file__).resolve().parents[1]):
        raise ValueError('reviewed runtime update requires the genuine managed installer launcher')
    trusted = verify_snapshot(context.request['root'], context.request['expected'])
    root = P.storage(readonly=True)
    if root != safe(Path(state_root) / 'al-run-pins'):
        raise ValueError('reviewed runtime registry differs from the selected protected launcher')
    _directory(root, create=False)
    return root, trusted['expected']


def _index(root):
    directory = root / 'runs'
    if not directory.exists():
        return {}
    _directory(directory, create=False)
    paths = sorted(directory.glob('*.json'))
    if len(paths) > 10000:
        raise ValueError('protected runtime index exceeds bounded enumeration')
    result = {}
    for path in paths:
        raw, _ = metadata(path)
        result[str(path)] = digest(raw)
    return result


def _state(transaction):
    path = safe(P.state_path(transaction))
    if not path.exists():
        return None
    raw, _ = metadata(path)
    value = decode(raw)
    if not isinstance(value, dict):
        raise ValueError('protected runtime state is not an object')
    try:
        observed = P.read_state(transaction)
    except AttributeError as error:
        raise ValueError('protected runtime state became malformed during validation') from error
    if observed != value:
        raise ValueError('protected runtime state moved during validation')
    identity = observed.get('identity')
    if (not isinstance(identity, dict)
            or any(identity.get(key) != expected for key, expected in transaction['repository_identity'].items())
            or identity.get('cwd', transaction['repository_identity']['root']) != transaction['repository_identity']['root']):
        raise ValueError('state stable repository identity differs from independently protected binding')
    return observed


def _subject(value, path):
    if not isinstance(value, dict) or type(value.get('schema_version')) is not int:
        raise ValueError('protected runtime index schema is malformed')
    subject = {key: value[key] for key in ('repository_identity', 'platform', 'session_id')}
    repository = subject['repository_identity']
    if (not isinstance(repository, dict) or set(repository) != {'root', 'git_dir', 'common_dir'}
            or not isinstance(repository['root'], str) or not Path(repository['root']).is_absolute()
            or any(v is not None and (not isinstance(v, str) or not Path(v).is_absolute()) for v in repository.values())
            or subject['platform'] not in {'codex', 'claude'}
            or not isinstance(subject['session_id'], str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,159}', subject['session_id'])
            or path.name != P.key(subject) + '.json'):
        raise ValueError('protected runtime index subject/filename differs')
    for name in repository.values():
        if name is not None:
            if str(safe(name).resolve(strict=False)) != name:
                raise ValueError('noncanonical protected repository identity')
    return subject


def _pin(root, binding, platform):
    verified = verify_pin(root / 'pins', binding, readonly=True)
    manifest = verified['manifest']
    if manifest['platform'] != platform:
        raise ValueError('historical pin platform differs from protected binding')
    _directory(verified['root'], create=False)
    for path in verified['root'].rglob('*'):
        info = safe(path).stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022 or path.is_file() and info.st_nlink != 1:
            raise ValueError('historical pin ownership/mode/alias differs')
    # Stored profile verification is read-only. It does not launch/probe or adopt
    # this installer's live interpreter as an older run's interpreter authority.
    interpreter, profile = manifest['interpreter'], manifest['interpreter_execution_identity']
    launcher = Path(interpreter['executable']).resolve(strict=True)
    if (str(launcher) != interpreter['canonical_executable']
            or executable_state(launcher) != {k: interpreter[k] for k in ('sha256', 'mode')}
            or executable_state(Path(profile['image']['path']).resolve(strict=True)) !=
               {k: profile['image'][k] for k in ('sha256', 'mode')}):
        raise ValueError('historical pinned interpreter/image changed or unavailable')
    return {'pin': dict(binding), 'closure_digest': manifest['closure_digest'],
            'distribution_identity_sha256': digest(canonical(manifest['distribution_identity'])),
            'interpreter_sha256': digest(canonical(interpreter)),
            'profile_sha256': digest(canonical(profile))}


def _checks(state, runtime, state_digest):
    for entry in state['evidence']['entries']:
        provenance = {'runtime_root': str(runtime), 'members': {}}
        reference = (entry.get('reconciliation') or {}).get('artifact')
        if reference:
            path = safe(reference['stored_path']).resolve(strict=True)
            if str(path) != reference['stored_path']:
                raise ValueError('noncanonical reconciliation reference')
            relative = path.relative_to(runtime).as_posix()
            raw, _ = metadata(path)
            provenance['members'][relative] = {'type': 'file', 'sha256': digest(raw)}
        validation = (reconciliation_validation(entry, state, provenance, state_digest)
                      if entry.get('reconciliation') is not None or entry.get('interrupted') else None)
        reasons = check_blockers(entry, validation)
        if reasons:
            raise ActiveRuns('reviewed runtime refuses unsettled evidence in ' + str(runtime) + '/' +
                             state['session_id'] + ': ' + entry['id'] + ': ' + '; '.join(reasons))
    if any(check_blockers(check) for check in setup_checks(state['evidence']['entries'])):
        raise ActiveRuns('reviewed runtime refuses unsettled dependency setup evidence in ' + str(runtime))


def _superseded(root, value, subject, index):
    """Correlate a fenced non-capability, never current epoch/approval authority.

    This intentionally does not call require_active/_authority. Those execution
    guards remain mandatory when a run is actually used. Only future global file
    replacement is being assessed here; every referenced publication stays intact.
    """
    from . import runtime_successor_store as Store
    from .runtime_successor import _request
    from .runtime_successor_start import _context
    fields = {'schema_version', 'kind', 'status', 'repository_identity', 'platform', 'session_id', 'run_id',
              'predecessor_binding_ref', 'successor_subject', 'activation_ref'}
    if (set(value) != fields or value['schema_version'] != 1 or value['kind'] != 'superseded-run-binding-v1'
            or value['status'] != 'SUPERSEDED'):
        raise ValueError('unsupported SUPERSEDED runtime metadata')
    files = {}
    def read(path):
        path = safe(path)
        raw, _ = metadata(path)
        files[str(path)] = digest(raw)
        value = decode(raw)
        if not isinstance(value, dict):
            raise ValueError('SUPERSEDED metadata publication is not an object')
        return value
    def reference(ref, expected):
        if (not isinstance(ref, dict) or set(ref) != {'stored_path', 'sha256'}
                or ref['stored_path'] != str(expected) or not re.fullmatch(r'[0-9a-f]{64}', str(ref['sha256']))):
            raise ValueError('SUPERSEDED protected reference location/schema differs')
        result = read(expected)
        if files[str(expected)] != ref['sha256']:
            raise ValueError('SUPERSEDED protected reference hash differs')
        return result
    activation_ref = value['activation_ref']
    if not isinstance(activation_ref, dict) or set(activation_ref) != {'stored_path', 'sha256'}:
        raise ValueError('SUPERSEDED activation reference is malformed')
    directory = safe(activation_ref['stored_path']).parent
    identifier = Store.request_id(directory.name)
    if directory != root / 'successors' / identifier:
        raise ValueError('SUPERSEDED activation escaped protected storage')
    _directory(directory, create=False)
    activation = reference(activation_ref, directory / 'activation-plan.json')
    if (set(activation) != {'kind', 'request_id', 'receipt', 'selector', 'selector_ref'}
            or activation['kind'] != 'successor-activation-plan-v1' or activation['request_id'] != identifier):
        raise ValueError('SUPERSEDED activation plan is malformed')
    journal = read(directory / 'activation-journal.json')
    if (set(journal) - {'state', 'activation_ref', 'error_type', 'safe_diagnostic'}
            or journal.get('state') != 'ACTIVE' or journal.get('activation_ref') != activation_ref):
        raise ValueError('SUPERSEDED activation journal is not the exact ACTIVE publication')
    if (read(directory / 'predecessor-tombstone.json') != value
            or files[str(directory / 'predecessor-tombstone.json')] != index[str(root / 'runs' / (P.key(subject) + '.json'))]):
        raise ValueError('SUPERSEDED tombstone differs from protected publication')
    predecessor = reference(value['predecessor_binding_ref'], directory / 'predecessor-binding.json')
    P.validate(predecessor, subject)
    if predecessor['status'] != 'ACTIVE' or predecessor['run_id'] != value['run_id']:
        raise ValueError('SUPERSEDED archived predecessor identity differs')
    successor_subject = value['successor_subject']
    successor_path = root / 'runs' / (P.key(successor_subject) + '.json')
    if str(successor_path) not in index:
        raise ValueError('SUPERSEDED immediate successor index is unavailable')
    successor = read(successor_path)
    if files[str(successor_path)] != index[str(successor_path)] or _subject(successor, successor_path) != successor_subject:
        raise ValueError('SUPERSEDED successor index changed or is foreign')
    if successor['status'] != 'ACTIVE' or successor['schema_version'] not in {2, 3}:
        raise ValueError('SUPERSEDED requires an immediate supported ACTIVE successor; chains are not inferred')
    P.validate(successor, successor_subject)
    if (successor['repository_identity'] != predecessor['repository_identity']
            or successor['platform'] != predecessor['platform'] or successor_subject == subject):
        raise ValueError('SUPERSEDED successor repository/platform/subject differs')
    successor_pin = _pin(root, successor['pin'], successor['platform'])
    successor_state = _state(successor)
    if successor_state is None:
        raise ValueError('SUPERSEDED successor ACTIVE state is unavailable')
    plan = read(directory / 'plan.json')
    plan_ref = {'stored_path': str(directory / 'plan.json'), 'sha256': files[str(directory / 'plan.json')]}
    recovery = plan.get('kind') == 'missing-state-successor-start-plan-v1'
    plan_fields = {'schema_version', 'kind', 'request', 'storage', 'authority_directory', 'predecessor_transaction',
                   'successor_transaction', 'predecessor_binding_ref', 'source_anchors', 'predecessor_initialization'}
    plan_fields |= {'authority_handoff_ref', 'recovery_lineage_ref'} if recovery else {'predecessor_state_ref'}
    if (set(plan) != plan_fields or type(plan['schema_version']) is not int or plan['schema_version'] != 1
            or plan['kind'] not in {'successor-start-plan-v1', 'missing-state-successor-start-plan-v1'}
            or plan['storage'] != str(root) or plan['predecessor_transaction'] != predecessor
            or plan['predecessor_binding_ref'] != value['predecessor_binding_ref']
            or plan['successor_transaction'] != dict(successor, initialization=None, status='RESERVED')):
        raise ValueError('SUPERSEDED preparation plan does not bind exact predecessor/successor')
    request = _request(read(directory / 'request.json'), identifier)
    if (type(request['schema_version']) is not int or plan['request'] != request
            or request['predecessor_subject'] != {'session_id': predecessor['session_id'], 'run_id': predecessor['run_id']}
            or plan['predecessor_initialization'] != read_initialization(predecessor, storage_root=root)):
        raise ValueError('SUPERSEDED request/initialization differs')
    if (recovery and request['schema_version'] != 2
            or request['schema_version'] == 2 and recovery != (request['transition_mode'] == 'fresh-planning-missing-state')):
        raise ValueError('SUPERSEDED preparation/recovery request profile differs')
    if predecessor['schema_version'] == 3:
        previous_epoch = predecessor['harness_binding']
        if (request['execution_id'] != previous_epoch['execution_id']
                or request['expected_epoch_id'] != previous_epoch['epoch_id']
                or request['expected_authority_generation'] != previous_epoch['authority_generation']):
            raise ValueError('SUPERSEDED request/predecessor durable epoch metadata differs')
    candidate = request['expected_candidate_identity']
    if (not isinstance(candidate, dict) or set(candidate) != {'sha256', 'opaque_limitations'}
            or not re.fullmatch(r'[0-9a-f]{64}', str(candidate['sha256']))
            or not isinstance(candidate['opaque_limitations'], list)
            or any(not isinstance(item, str) for item in candidate['opaque_limitations'])
            or not re.fullmatch(r'[0-9a-f]{64}', str(request['approved_requirement_hash']))
            or request['expected_epoch_id'] not in {'legacy-0', 'epoch-' + predecessor['run_id']}):
        raise ValueError('SUPERSEDED request candidate/requirement/epoch metadata is unsupported')
    receipt = read(directory / 'receipt.json')
    receipt_fields = {'schema_version', 'kind', 'state', 'request_id', 'request_sha256', 'preparation_sha256',
                      'predecessor_binding_ref', 'lineage_ref', 'successor_subject', 'successor_run_id',
                      'successor_transaction', 'successor_state_ref', 'successor_handoff_ref',
                      'prepared_classification_ref', 'candidate_identity', 'distribution_identity',
                      'native_resumed', 'product_authority'}
    receipt_fields |= {'recovery_lineage_ref'} if recovery else {'predecessor_state_ref'}
    if request['schema_version'] == 2:
        receipt_fields |= {'transition_mode', 'preview_sha256'}
    if (set(receipt) != receipt_fields or type(receipt['schema_version']) is not int or receipt['schema_version'] != 1
            or receipt['kind'] != 'prepared-successor-v1' or receipt['state'] != 'PREPARED'
            or receipt['request_id'] != identifier or receipt != activation['receipt']
            or receipt['request_sha256'] != digest(canonical(request)) or receipt['preparation_sha256'] != plan_ref['sha256']
            or receipt['predecessor_binding_ref'] != value['predecessor_binding_ref']
            or receipt['successor_subject'] != successor_subject or receipt['successor_run_id'] != successor['run_id']
            or receipt['successor_transaction'] != successor or receipt['native_resumed'] is not False
            or receipt['product_authority'] is not False
            or receipt['distribution_identity'] != successor_pin['closure_digest']
            or request['distribution_identity'] != successor_pin['closure_digest']
            or receipt['candidate_identity'] != candidate):
        raise ValueError('SUPERSEDED receipt/activation/preparation identities differ')
    if request['schema_version'] == 2 and any(receipt[k] != request[k] for k in ('transition_mode', 'preview_sha256')):
        raise ValueError('SUPERSEDED fresh-planning receipt differs')
    prepared = read(directory / 'journal.json')
    if (type(prepared.get('schema_version')) is not int or prepared.get('schema_version') != 1 or prepared.get('kind') != 'successor-preparation-journal-v1'
            or prepared.get('state') != 'PREPARED' or prepared.get('successor_subject') != successor_subject
            or prepared.get('request_ref') != {'stored_path': str(directory / 'request.json'), 'sha256': files[str(directory / 'request.json')]}
            or prepared.get('receipt_ref') != {'stored_path': str(directory / 'receipt.json'), 'sha256': files[str(directory / 'receipt.json')]}):
        raise ValueError('SUPERSEDED preparation journal references differ')
    fence = read(root / 'successor-fences' / (P.key(successor_subject) + '.json'))
    if type(fence.get('schema_version')) is not int or fence != {'schema_version': 1, 'kind': 'prepared-successor-fence-v1', 'subject': successor_subject,
                 'request_id': identifier, 'plan_sha256': plan_ref['sha256']}:
        raise ValueError('SUPERSEDED successor fence differs')
    selector_path = root / 'successor-active' / (P.key(successor_subject) + '.json')
    selector = read(selector_path)
    selector_fields = {'schema_version', 'kind', 'activated_at', 'predecessor_child_ids', 'execution_id', 'request_id',
                       'storage', 'epoch_id', 'authority_generation', 'successor_subject', 'successor_transaction', 'preparation_sha256'}
    if (set(selector) != selector_fields or type(selector['schema_version']) is not int or selector['schema_version'] != 1
            or selector['kind'] != 'active-successor-epoch-v1' or not finite(selector['activated_at'])
            or selector != activation['selector'] or activation['selector_ref'] !=
               {'stored_path': str(selector_path), 'sha256': files[str(selector_path)]}
            or selector['request_id'] != identifier or selector['storage'] != str(root)
            or selector['execution_id'] != request['execution_id'] or selector['epoch_id'] != 'epoch-' + successor['run_id']
            or type(selector['authority_generation']) is not int
            or selector['authority_generation'] != request['expected_authority_generation'] + 1
            or selector['successor_subject'] != successor_subject or selector['successor_transaction'] != successor
            or selector['preparation_sha256'] != plan_ref['sha256']):
        raise ValueError('SUPERSEDED selector is foreign or differs from exact activation')
    if successor['schema_version'] == 3 and any(successor['harness_binding'][k] != selector[k]
                                              for k in ('execution_id', 'epoch_id', 'authority_generation')):
        raise ValueError('SUPERSEDED selector/durable epoch metadata differs')
    predecessor_state = _state(predecessor)
    if recovery:
        if predecessor_state is not None or selector['predecessor_child_ids'] != []:
            raise ValueError('SUPERSEDED missing-state recovery no longer has its exact absence profile')
        if receipt['recovery_lineage_ref'] != plan['recovery_lineage_ref']:
            raise ValueError('SUPERSEDED recovery lineage reference differs')
        reference(plan['recovery_lineage_ref'], directory / 'recovery-lineage.json')
    else:
        archived = reference(plan['predecessor_state_ref'], directory / 'predecessor-state.json')
        integration = archived.get('integration')
        if not isinstance(integration, dict) or not isinstance(integration.get('agents'), dict):
            raise ValueError('SUPERSEDED predecessor integration metadata is malformed')
        if (receipt['predecessor_state_ref'] != plan['predecessor_state_ref'] or predecessor_state != archived
                or digest(metadata(P.state_path(predecessor))[0]) != plan['predecessor_state_ref']['sha256']
                or selector['predecessor_child_ids'] != sorted(integration['agents'])
                or request['schema_version'] == 2 and request['expected_predecessor_state_sha256'] != plan['predecessor_state_ref']['sha256']):
            raise ValueError('SUPERSEDED retained predecessor state/child identities differ')
    successor_archived = reference(receipt['successor_state_ref'], directory / 'successor-state.json')
    if (successor_archived.get('session_id') != successor['session_id']
            or successor_archived.get('run_id') != successor['run_id']
            or successor_archived.get('runtime_origin') != _state(successor)['runtime_origin']):
        raise ValueError('SUPERSEDED archived successor origin differs')
    lineage = reference(receipt['lineage_ref'], directory / 'lineage.json')
    lineage_fields = {'schema_version', 'kind', 'request_id', 'preparation_sha256', 'requirements_ref',
                      'predecessor_binding_ref', 'source_anchors', 'settled_candidate_identity', 'successor_transaction'}
    lineage_fields |= {'recovery_lineage_ref'} if recovery else {'predecessor_state_ref'}
    inherited_key = 'recovery_lineage_ref' if recovery else 'predecessor_state_ref'
    if (set(lineage) != lineage_fields or type(lineage['schema_version']) is not int or lineage['schema_version'] != 1
            or lineage['kind'] != ('missing-state-successor-source-lineage-v1' if recovery else 'successor-source-lineage-v1')
            or lineage['request_id'] != identifier or lineage['preparation_sha256'] != plan_ref['sha256']
            or lineage['predecessor_binding_ref'] != value['predecessor_binding_ref']
            or lineage[inherited_key] != plan[inherited_key] or lineage['source_anchors'] != plan['source_anchors']
            or lineage['settled_candidate_identity'] != candidate
            or lineage['successor_transaction'] != plan['successor_transaction']
            or read(directory / 'lineage-reference.json') != receipt['lineage_ref']):
        raise ValueError('SUPERSEDED source lineage metadata differs')
    requirements = reference(lineage['requirements_ref'], directory / 'requirements.json')
    if (set(requirements) != {'schema_version', 'kind', 'predecessor', 'successor', 'original_package',
                             'successor_package', 'amendments', 'successor_amendments', 'revision_hashes',
                             'requirement_lineage_hash', 'scoped_acceptance'}
            or type(requirements.get('schema_version')) is not int or requirements.get('schema_version') != 1
            or requirements.get('kind') != 'successor-requirement-lineage-v1'
            or requirements.get('predecessor') != {k: predecessor[k] for k in
                                                  ('repository_identity', 'platform', 'session_id', 'run_id', 'pin', 'initialization')}
            or requirements.get('successor') != {k: successor[k] for k in
                                                ('repository_identity', 'platform', 'session_id', 'run_id', 'pin')}
            or requirements.get('requirement_lineage_hash') != request['approved_requirement_hash']):
        raise ValueError('SUPERSEDED requirement lineage metadata differs')
    if read(directory / 'context.json') != _context(plan_ref, plan):
        raise ValueError('SUPERSEDED preparation context differs')
    # Resource refs are opaque immutable metadata. Hash them; never call the
    # sealed bridge, inspect conversation prose, or ask SQLite for current authority.
    resources = read(directory / 'resources.json')
    if (set(resources) != {'handoff_ref', 'package_ref', 'classification_ref'}
            or resources['handoff_ref'] != receipt['successor_handoff_ref']
            or resources['classification_ref'] != receipt['prepared_classification_ref']):
        raise ValueError('SUPERSEDED prepared resource references differ')
    successor_integration = successor_state.get('integration')
    if (not isinstance(successor_integration, dict)
            or successor_integration.get('handoff') != receipt['successor_handoff_ref']['path']):
        raise ValueError('SUPERSEDED current successor handoff location differs')
    authority_directory = safe(plan['authority_directory']).resolve(strict=True)
    if str(authority_directory) != plan['authority_directory'] or not authority_directory.is_dir():
        raise ValueError('SUPERSEDED authority metadata directory is noncanonical')
    def external(ref, *, stored=False):
        field = 'stored_path' if stored else 'path'
        if not isinstance(ref, dict) or set(ref) != {field, 'sha256'}:
            raise ValueError('SUPERSEDED external metadata reference is malformed')
        path = safe(ref[field])
        if not path.is_relative_to(authority_directory):
            raise ValueError('SUPERSEDED external metadata reference is foreign')
        raw, _ = metadata(path)
        expected = ref['sha256']
        if not isinstance(expected, str) or expected.removeprefix('sha256:') != digest(raw):
            raise ValueError('SUPERSEDED external metadata reference hash differs')
        files[str(path)] = digest(raw)
        return raw
    for ref in resources.values():
        external(ref)
    authority_ref = request['authority_snapshot_ref']
    authority = decode(external(authority_ref, stored=True))
    authority_path = Path(authority_ref['stored_path'])
    authority_base = authority_directory / 'prepared-authority'
    versioned = (authority_path.parent.parent == authority_base / 'bridge-revisions'
                 and re.fullmatch(r'[0-9a-f]{64}', authority_path.parent.name))
    if (authority_path.name != 'authority.json' or authority_path != authority_base / 'authority.json' and not versioned
            or not isinstance(authority, dict) or set(authority) != {'schema_version', 'kind', 'snapshot', 'manifest_sha256', 'interpreter'}
            or type(authority['schema_version']) is not int or authority['schema_version'] != 1
            or authority['kind'] != 'prepared-harness-authority-v1'
            or authority['snapshot'] != str(authority_path.parent / 'trusted')):
        raise ValueError('SUPERSEDED archived authority metadata profile/location differs')
    bridge = safe(authority['snapshot'])
    bridge_manifest_path = bridge / 'manifest.json'
    bridge_raw = _bridge_manifest_bytes(bridge_manifest_path)
    if digest(bridge_raw) != authority['manifest_sha256']:
        raise ValueError('SUPERSEDED archived authority manifest changed')
    files[str(bridge_manifest_path)] = digest(bridge_raw)
    bridge_manifest = decode(bridge_raw)
    if (not isinstance(bridge_manifest, dict) or set(bridge_manifest) != {'fingerprint', 'files'}
            or not isinstance(bridge_manifest['files'], dict) or len(bridge_manifest['files']) > 10000):
        raise ValueError('SUPERSEDED archived authority closure is malformed')
    actual = set()
    for path in (bridge / 'harness').rglob('*'):
        safe(path)
        if path.is_file(): actual.add(path.relative_to(bridge / 'harness').as_posix())
        elif not path.is_dir(): raise ValueError('SUPERSEDED archived authority special member')
    if actual != set(bridge_manifest['files']):
        raise ValueError('SUPERSEDED archived authority membership differs')
    bridge_fingerprint = hashlib.sha256()
    for name, sha in sorted(bridge_manifest['files'].items()):
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or relative.as_posix() != name:
            raise ValueError('SUPERSEDED archived authority unsafe member')
        path = bridge / 'harness' / relative
        raw, _ = metadata(path)
        if digest(raw) != sha:
            raise ValueError('SUPERSEDED archived authority source bytes changed')
        files[str(path)] = digest(raw)
        bridge_fingerprint.update(name.encode() + raw)
    if (bridge_fingerprint.hexdigest() != bridge_manifest['fingerprint']
            or versioned and authority_path.parent.name != bridge_manifest['fingerprint']):
        raise ValueError('SUPERSEDED archived authority fingerprint/path differs')
    interpreter = authority['interpreter']
    if (not isinstance(interpreter, dict) or set(interpreter) != {'executable', 'sha256', 'mode'}
            or executable_state(interpreter['executable']) != {k: interpreter[k] for k in ('sha256', 'mode')}):
        raise ValueError('SUPERSEDED archived authority interpreter changed')
    if recovery:
        if plan['authority_handoff_ref'] != request['authority_handoff_ref']:
            raise ValueError('SUPERSEDED recovery authority handoff reference differs')
        external(plan['authority_handoff_ref'])
        recovery_raw = external(request['recovery_lineage_ref'], stored=True)
        if decode(recovery_raw) != reference(plan['recovery_lineage_ref'], directory / 'recovery-lineage.json'):
            raise ValueError('SUPERSEDED retained recovery lineage differs')
    elif Path(predecessor_state.get('integration', {}).get('handoff', '')).parent != authority_directory:
        raise ValueError('SUPERSEDED predecessor authority directory differs')
    if request['schema_version'] == 2:
        external(request['settlement_ref'], stored=True)
        external(request['authority_handoff_ref'])
        if recovery and request['expected_predecessor_state_sha256'] is not None:
            raise ValueError('SUPERSEDED missing-state recovery carries a foreign state binding')
    for name, sha in files.items():
        raw = _bridge_manifest_bytes(name) if name == str(bridge_manifest_path) else metadata(name)[0]
        if digest(raw) != sha:
            raise ValueError('SUPERSEDED referenced evidence changed during observation')
    summary = {'classification': 'protected-superseded-non-capability',
               'current_sqlite_epoch_and_approval_authority': 'NOT_ASSESSED',
               'predecessor_binding_ref': value['predecessor_binding_ref'],
               'successor_subject': successor_subject, 'activation_ref': activation_ref,
               'bridge_manifest_path': str(bridge_manifest_path),
               'referenced_metadata': files}
    return predecessor, summary, recovery


def observe(state_root, rows, target, explicit=(), invoking=None):
    root, coordinator = selected_storage(state_root)
    initial = _index(root)
    contexts, bindings, state_digests, initialization_refs, absent_states, roots = [], {}, {}, {}, [], []
    for name, record_digest in initial.items():
        path = Path(name)
        value = P.read(path)
        raw, _ = metadata(path)
        if digest(raw) != record_digest or decode(raw) != value:
            raise ValueError('protected runtime binding moved before validation')
        subject = _subject(value, path)
        index_schema = value['schema_version']
        superseded, missing_predecessor = None, False
        status = value.get('status')
        if status == 'SUPERSEDED':
            value, superseded, missing_predecessor = _superseded(root, value, subject, initial)
        P.validate(value, subject)
        runtime = P.runtime_directory(value)
        if superseded is None and value['schema_version'] == 3 and (value['status'] == 'RESERVED' or not P.state_path(value).is_file()):
            # Preserve the existing durable discover() refusal, including RESERVED.
            raise ValueError('durable run is reserved or its active state is unavailable')
        pin = _pin(root, value['pin'], value['platform'])
        state = _state(value)
        state_digest = None
        if state is None:
            if value['status'] == 'ACTIVE' and not missing_predecessor:
                raise ValueError('ACTIVE protected runtime state is unavailable')
            absent_states.append(P.state_path(value))
        else:
            state_path = P.state_path(value)
            raw, _ = metadata(state_path)
            if decode(raw) != state:
                raise ValueError('protected runtime state moved before operational inspection')
            state_digest = digest(raw)
            _checks(state, runtime, state_digest)
            key = str(state_path)
            if key in bindings:
                raise ValueError('ambiguous independently protected runtime state')
            bindings[key] = value
            state_digests[key] = state_digest
        initialization = None
        if value['initialization'] is not None:
            metadata(value['initialization']['stored_path'])
            initialization_refs[value['initialization']['stored_path']] = value['initialization']['sha256']
            initialized = read_initialization(value, storage_root=root)
            initialization = digest(canonical(initialized))
            if state is not None:
                admission = state.get('proof_admission')
                if (not isinstance(admission, dict) or admission.get('status') not in {'pending', 'admitted'}
                        or admission.get('initialization') != value['initialization']
                        or admission.get('runtime_pin') != value['pin']
                        or admission.get('original_task_hash') != initialized['original_task_package']['sha256']
                        or admission.get('verifier_policy') != initialized['verifier_policy']
                        or state.get('task_package') != initialized['original_task_package']):
                    raise ValueError('historical state proof initialization differs from protected binding')
        elif state is not None and 'proof_admission' in state:
            raise ValueError('historical proof admission lacks protected initialization')
        roots.append((runtime, subject['repository_identity']['root']))
        context = {'subject': subject, 'index_schema_version': index_schema,
                         'schema_version': value['schema_version'], 'status': status,
                         'run_id': value['run_id'], 'binding_sha256': record_digest,
                         'state_sha256': state_digest, 'initialization_sha256': initialization,
                         'classification': ('pinned-initialization-settlement-unknown' if value['status'] == 'RESERVED'
                                            else 'pinned-history-workflow-unchanged'), **pin}
        if superseded is not None:
            context.update(superseded)
        contexts.append(context)
    roots.extend(activity_roots(rows, target, explicit, invoking, state_root=state_root))
    report = scan_activity(roots)
    if any(item.get('category') != 'terminal-workflow' for item in report['blockers']):
        raise ActiveRuns('reviewed runtime refuses malformed/unsettled activity: ' +
                         '; '.join(item['path'] + ': ' + item['reason'] for item in report['blockers']))
    for runtime in report['roots']:
        for path in sorted(Path(runtime).glob('*.json')):
            if str(path) not in bindings:
                raise ValueError('discovered history lacks an unambiguous independently protected binding: ' + str(path))
            metadata(path)
    if _index(root) != initial:
        raise ValueError('protected runtime index changed during observation')
    # Repeat state-origin correlation after closure/profile and activity inspection.
    for path, value in bindings.items():
        _state(value)
        if digest(metadata(path)[0]) != state_digests[path]:
            raise ValueError('protected runtime state changed during observation')
    for context in contexts:
        for path, sha in context.get('referenced_metadata', {}).items():
            raw = _bridge_manifest_bytes(path) if path == context.get('bridge_manifest_path') else metadata(path)[0]
            if digest(raw) != sha:
                raise ValueError('SUPERSEDED referenced metadata changed before observation completed')
    for path, sha in initialization_refs.items():
        if digest(metadata(path)[0]) != sha:
            raise ValueError('protected initialization changed before observation completed')
    for path in absent_states:
        if safe(path).exists():
            raise ValueError('pinned initialization/predecessor state appeared during observation')
    if _index(root) != initial:
        raise ValueError('protected runtime index changed before observation completed')
    body = {'activity': report, 'contexts': contexts, 'coordinator': coordinator,
            'execution_authority': 'NOT_ASSESSED; existing run/resume/current-epoch/approval guards remain required on use',
            'limitation': 'Future payload files only; RESERVED process settlement is unknown. No run/epoch/approval authority is granted.'}
    return dict(body, digest=digest(canonical(body)))
