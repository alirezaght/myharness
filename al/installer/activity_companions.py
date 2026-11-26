"""Closed historical exchange observations; recognition grants no authority."""
import hashlib
import os
from pathlib import Path
import stat

from . import activity_contract_schema as C

LIMIT = 4096
RAW_CACHE_BYTES = 64 * 1024 * 1024


def validate(state, provenance):
    from . import activity_profiles as P

    companions = state.get('agent_contracts', {})
    history = state.get('agent_contract_history', [])
    if (not isinstance(companions, dict) or len(companions) > LIMIT
            or not isinstance(history, list) or len(history) > LIMIT):
        raise ValueError('unsupported companion collection')
    if not companions and not history:
        return
    for key, value in companions.items():
        if not isinstance(key, str):
            raise ValueError('malformed companion mapping key')
        C.validate(value)
    for value in history:
        C.validate(value)
    runtime = Path(provenance['runtime_root'])
    amendments_state = state.get('amendments', [])
    planning = state.get('planning_authorship', {})
    planning_history = state.get('planning_history', [])
    evidence = state.get('evidence', {})
    if (not isinstance(amendments_state, list) or len(amendments_state) > LIMIT
            or any(not isinstance(a, dict) or 'artifact' not in a for a in amendments_state)
            or not isinstance(planning, dict) or not isinstance(planning_history, list)
            or len(planning_history) > LIMIT or any(not isinstance(p, dict) for p in planning_history)
            or not isinstance(evidence, dict)):
        raise ValueError('malformed companion source containers')
    integration = state.get('integration', {})
    if (not isinstance(integration, dict)
            or not isinstance(integration.get('binding', {}), dict)
            or integration.get('graph_revision') is not None
            and (type(integration['graph_revision']) is not int or integration['graph_revision'] < 0)):
        raise ValueError('malformed companion integration context')
    plans = [planning, *planning_history]
    entries = evidence.get('entries', [])
    if (not isinstance(entries, list) or len(entries) > LIMIT
            or any(not isinstance(e, dict) for e in entries)):
        raise ValueError('malformed companion evidence collection')
    full = [state['task_package'], *[a['artifact'] for a in amendments_state]]
    # Only producer-declared full references can resolve compact input identities.
    for plan in plans:
        for key in ('report', 'contract_observation'):
            if key in plan:
                if not isinstance(plan[key], dict):
                    raise ValueError('malformed planning companion source')
                full.append(plan[key])
    for entry in entries:
        reconciliation = entry.get('reconciliation')
        if reconciliation is not None:
            if not isinstance(reconciliation, dict):
                raise ValueError('malformed companion reconciliation container')
            full.extend(ref for key, ref in reconciliation.items() if key in {'artifact', 'observation'})
    if len(full) > LIMIT:
        raise ValueError('companion reference limit')
    catalog = {}
    for ref in full:
        if (not isinstance(ref, dict) or set(ref) != {'kind', 'id', 'sha256', 'stored_path', 'source', 'captured_at'}
                or ref['id'] != ref['sha256'] or not P._time(ref['captured_at'])
                or not isinstance(ref['source'], str) or not isinstance(ref['stored_path'], str)):
            raise ValueError('malformed companion source')
        C.reference(C.ref(ref))
        key = tuple(C.ref(ref)[k] for k in ('kind', 'id', 'sha256'))
        if key in catalog and catalog[key] != ref:
            raise ValueError('ambiguous companion source')
        catalog[key] = ref
    cache = {}
    cached_bytes = 0

    def read(ref):
        nonlocal cached_bytes
        C.reference(ref)
        source = catalog.get(tuple(ref[k] for k in ('kind', 'id', 'sha256')))
        if source is None:
            raise ValueError('unbound compact companion reference')
        path = Path(source['stored_path'])
        P._references(source, provenance['members'], runtime)
        relative = path.relative_to(runtime).as_posix()
        row = provenance['members'][relative]
        if row['mode'] != 0o600:
            raise ValueError('companion source privacy')
        if relative in cache:
            return cache[relative]
        # Reject observed symlink components before the guarded leaf read.
        current = runtime
        for part in Path(relative).parts:
            current = current / part
            if stat.S_ISLNK(current.lstat().st_mode):
                raise ValueError('companion source symlink')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o600
                    or before.st_size > 64 * 1024 * 1024):
                raise ValueError('companion source type/size/privacy')
            raw = stream.read(64 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if (any(getattr(before, k) != getattr(after, k) for k in fields)
                or any(getattr(after, k) != getattr(path.lstat(), k) for k in fields)
                or hashlib.sha256(raw).hexdigest() != source['sha256']):
            raise ValueError('companion source changed')
        if len(raw) <= RAW_CACHE_BYTES - cached_bytes:
            cache[relative] = raw
            cached_bytes += len(raw)
        return raw

    def data(ref):
        return P.decode(read(C.ref(ref)))

    specification = C.ref(state['task_package'])
    amendments = [C.ref(a['artifact']) for a in state.get('amendments', [])]

    def check(value, kind, identifier, record, producer=None):
        C.validate(value, resolve=read)
        ctx = value['context']
        binding = state.get('integration', {}).get('binding') or {}
        expected = {'al_run': state['run_id'], 'al_session': state['session_id'],
                    'task': binding.get('task_id'), 'execution': binding.get('execution_id'),
                    'epoch': binding.get('epoch_id'), 'authority_generation': binding.get('authority_generation'),
                    'subtask': None, 'task_revision': None, 'producer': producer}
        reason = 'not-observed' if binding else 'standalone'
        if any(ctx['applicability'].get(k) != reason for k, v in ctx.items()
               if k != 'applicability' and v is None):
            raise ValueError('foreign companion applicability')
        if any(ctx[k] != v for k, v in expected.items()):
            raise ValueError('foreign companion context')
        graph = state.get('integration', {}).get('graph_revision')
        if ((ctx['graph_revision'] is None) != (graph is None)
                or graph is not None and not 0 <= ctx['graph_revision'] <= graph):
            raise ValueError('foreign companion graph')
        spec = value['source_specification']
        if spec['reference'] != specification or spec['amendments'] != amendments[:len(spec['amendments'])]:
            raise ValueError('foreign companion specification')
        if (value['kind'] != kind or value['payload']['record_type'] != kind
                or value['payload']['record_id'] != str(identifier)
                or value['payload']['record_digest'] != C.fingerprint(record)):
            raise ValueError('companion producer record mismatch')

    def analysis(value):
        spec = value['source_specification']
        original = data(state['task_package'])
        record = original if not spec['amendments'] else {
            'original': original, 'amendments': [P.decode(read(r)) for r in spec['amendments']]}
        check(value, 'requirement-analysis', state['task_package']['id'], record)

    for key, value in companions.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise ValueError('malformed companion mapping')
        kind = value.get('kind')
        identifier = value.get('payload', {}).get('record_id')
        if key != kind + ':' + str(identifier):
            raise ValueError('companion mapping identity mismatch')
        if kind == 'requirement-analysis':
            analysis(value)
        elif kind == 'implementation-plan':
            matches = [p for p in plans if p.get('report', {}).get('id') == identifier]
            if len(matches) != 1:
                raise ValueError('unbound planning companion')
            plan = matches[0]
            if (plan['report']['kind'] != 'architect-planning-report-v1'
                    or plan.get('contract_observation', {}).get('kind', 'implementation-plan-contract-v1')
                    != 'implementation-plan-contract-v1'):
                raise ValueError('unsupported planning companion source')
            report = data(plan['report'])
            if not isinstance(report, dict) or not isinstance(report.get('invocation_id'), str):
                raise ValueError('malformed planning companion record')
            check(value, kind, identifier, report, report['invocation_id'])
            if plan.get('contract_observation') and data(plan['contract_observation']) != value:
                raise ValueError('planning companion publication mismatch')
        elif kind == 'verification-result':
            matches = [e for e in entries if e.get('id') == identifier]
            if len(matches) != 1:
                raise ValueError('unbound verification companion')
            entry = matches[0]
            check(value, kind, identifier, entry)
            reconciliation = entry.get('reconciliation')
            if reconciliation and 'schema_version' in reconciliation:
                if (set(reconciliation) != {'schema_version', 'reason', 'artifact', 'observation'}
                        or type(reconciliation['schema_version']) is not int or reconciliation['schema_version'] != 1
                        or entry['status'] != 'INFRA' or not isinstance(reconciliation['reason'], str)
                        or not reconciliation['reason'].strip()):
                    raise ValueError('unsupported companion reconciliation')
                if (reconciliation['observation']['kind'] != 'check-observation-v1'
                        or reconciliation['artifact']['kind'] != 'check-reconciliation'):
                    raise ValueError('unsupported reconciliation source')
                retained = data(reconciliation['observation'])
                if (not isinstance(retained, dict) or set(retained) != {'schema_version', 'entry', 'contract'}
                        or type(retained['schema_version']) is not int or retained['schema_version'] != 1):
                    raise ValueError('unsupported retained companion')
                original = retained['entry']
                allowed = {'status', 'note', 'ended_at', 'reconciliation'}
                if (not isinstance(original, dict) or original.get('reconciliation') or original.get('id') != identifier
                        or {k: v for k, v in original.items() if k not in allowed}
                        != {k: v for k, v in entry.items() if k not in allowed}
                        or entry['note'] != 'interrupted check explicitly reconciled'):
                    raise ValueError('retained verification mismatch')
                if retained['contract'] is not None:
                    check(retained['contract'], kind, identifier, original)
                read(C.ref(reconciliation['artifact']))
        else:
            raise ValueError('unsupported companion producer family')
    for value in history:
        if not isinstance(value, dict) or value.get('kind') != 'requirement-analysis':
            raise ValueError('unsupported archived companion')
        analysis(value)


def validate_process_observation(value, entry):
    """Recognize historical selectors; never query or grant process authority."""
    import re
    import uuid

    if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise ValueError('unsupported historical process observation')
    if value.get('mode') == 'harness':
        if (set(value) != {'schema_version', 'mode', 'host_instance', 'owner_ref', 'run', 'id', 'reservation'}
                or any(not isinstance(value[k], str) or not re.fullmatch('[0-9a-f]{48}', value[k])
                       for k in ('host_instance', 'owner_ref'))
                or any(not isinstance(value[k], str) or not 0 < len(value[k]) <= 256
                       or value[k] != entry.get(k) for k in ('run', 'id', 'reservation'))):
            raise ValueError('malformed historical harness observation')
        return
    if value.get('mode') != 'local' or set(value) != {'schema_version', 'mode', 'domain', 'owner', 'children'}:
        raise ValueError('malformed historical local observation')
    domain = value['domain']
    if not isinstance(domain, dict):
        raise ValueError('malformed historical process domain')
    platform = domain.get('platform')
    if platform == 'darwin':
        boot = domain.get('boot')
        valid = (set(domain) == {'platform', 'boot'} and isinstance(boot, list) and len(boot) == 2
                 and all(type(n) is int for n in boot) and boot[0] > 0 and 0 <= boot[1] < 1000000)
    elif platform == 'linux':
        boot = domain.get('boot');namespace = domain.get('namespace')
        valid = (set(domain) == {'platform', 'boot', 'namespace'} and isinstance(boot, str)
                 and re.fullmatch('[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot) is not None
                 and str(uuid.UUID(boot)) == boot and isinstance(namespace, list) and len(namespace) == 2
                 and all(type(n) is int and 0 <= n < 2**64 for n in namespace))
    else:
        valid = False
    if not valid:
        raise ValueError('unsupported historical process domain')

    def identity(item):
        if item is None:  # Producer could not inspect the original process.
            return
        if (not isinstance(item, dict) or set(item) != {'pid', 'pgid', 'sid', 'start', 'zombie'}
                or type(item['zombie']) is not bool
                or any(type(item[k]) is not int or not 0 < item[k] <= 2147483647 for k in ('pid', 'pgid', 'sid'))
                or not isinstance(item['start'], list) or len(item['start']) != (2 if platform == 'darwin' else 1)
                or any(type(n) is not int or not 0 <= n < 2**64 for n in item['start']) or item['start'][0] <= 0):
            raise ValueError('malformed historical process identity')
    identity(value['owner'])
    children = value['children']
    if children is None:  # Producer records unavailable/overflowed inspection explicitly.
        return
    if not isinstance(children, list) or len(children) > 4096:
        raise ValueError('malformed historical process children')
    for child in children:
        identity(child)
