"""Workspace-only nonmutating runtime locks and fresh operational observations."""
from contextlib import contextmanager
import fcntl
import errno
import os
from pathlib import Path
import subprocess
import time

from .activity_inventory import inventory_activity, under
from .activity_retirement import read_reference, require_workspace_quiescent, seal, validate_seal
from .registry import canonical
from .activity_process import identity as kernel_identity, process_started_at
from .activity_sessions import processes as _processes
from .activity_orphans import process_subjects, physical


class _HeldLocks(list):
    active = False
    descriptors = None

    def validate(self):
        if not self.active:raise ValueError('runtime lock capability is not live')
        for path, descriptor, expected in self.descriptors:
            actual = os.fstat(descriptor)
            disk = path.stat()
            if path.is_symlink() or (actual.st_dev, actual.st_ino) != expected or (disk.st_dev, disk.st_ino) != expected:
                raise ValueError('held runtime lock identity changed')
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for path, descriptor, fingerprint in self.orphans:
            if physical(path, descriptor) != fingerprint:raise ValueError('held orphan lock fingerprint changed')


class _FreshObservation(dict):
    pass


def validate_fresh_observation(observation, inventory):
    if not isinstance(observation, _FreshObservation) or getattr(observation, '_used', True):
        raise ValueError('quiescence requires a new internal operational observation')
    observation._locks.validate()
    if observation._inventory != inventory['digest'] or observation._digest != observation['digest']:
        raise ValueError('operational observation capability binding changed')
    observation._used = True
    records = [r for root in inventory['roots'] for r in root['records']]
    subjects = {(kind, canonical(identity)): identity for kind, identity in process_subjects(inventory)}
    processes = observation['processes']
    actual = {(p.get('subject_kind'), canonical(p['record_identity'])) for p in processes}
    if actual != set(subjects):
        raise ValueError('incomplete operational process observation set')
    if any(p['record_identity'] != subjects[(p['subject_kind'], canonical(p['record_identity']))] for p in processes):
        raise ValueError('foreign operational process binding')
    for kind, ident in (s for s in process_subjects(inventory) if s[0] in {'orphan-lock', 'runtime-root'}):
        matches = [p for p in processes if p['subject_kind'] == kind and p['record_identity'] == ident]
        keys = [(p['pid'], p['observed_start']) for p in matches]
        if len(keys) != len(set(keys)):raise ValueError('duplicate operational subject process observation')
    if sorted(canonical(o['identity']) for o in observation['operations']) != sorted(canonical(r['identity']) for r in records):
        raise ValueError('incomplete operational ownership observation set')
    if observation['locks'] != list(observation._locks):raise ValueError('incomplete held runtime lock observation set')


def _requests(inventory):
    return [{k: root[k] for k in ('repository', 'platform', 'runtime_root')} for root in inventory['roots']]


def _nonstate(inventory):
    return {record['identity']['state_path']: record['provenance']
            for root in inventory['roots'] for record in root['records']
            if record['profile']['id'] == 'reviewed-nonstate-v1'}


def _known_processes(inventory, locks):
    observations = []
    for root in inventory['roots']:
        for record in root['records']:
            known = {}
            for check in record['operation_facts'].get('checks', []):
                process = check.get('process') or {}
                if not isinstance(process, dict):raise ValueError('unsupported recorded process metadata')
                for pid, metadata, role in ((check.get('pid'), process, 'child'), (process.get('pid'), process, 'child'),
                                            (check.get('owner_pid'), check.get('owner_identity') or {}, 'owner')):
                    if pid is None:continue
                    key = (pid, check['id'], role)
                    known[key] = (metadata, check)
            for (pid, check_id, role), (metadata, check) in known.items():
                ended = check.get('ended_at')
                row = {'subject_kind': 'session', 'record_identity': record['identity'], 'pid': pid, 'recorded_start': None,
                       'observed_start': None, 'result': 'unknown', 'evidence': {'kind': 'recorded-operation-pid', 'check': check_id}}
                try:
                    if type(pid) is not int or pid <= 0 or not isinstance(metadata, dict):raise ValueError('invalid recorded PID identity')
                    observed = kernel_identity(pid)
                    if observed is None or observed['zombie']:
                        row['result'] = 'absent'
                    else:
                        row['observed_start'] = canonical(observed['start']).decode()
                        row['evidence']['observed_identity'] = observed
                        recorded = metadata.get('identity', metadata)
                        if not isinstance(recorded, dict):raise ValueError('unsupported recorded process identity')
                        start = recorded.get('start')
                        if start is not None:
                            if (not isinstance(start, list) or len(start) != len(observed['start'])
                                    or any(type(n) is not int or n < 0 for n in start)
                                    or recorded.get('pid') != pid):
                                raise ValueError('unsupported recorded kernel identity')
                            row['recorded_start'] = canonical(start).decode()
                            row['result'] = 'live-relevant' if start == observed['start'] else 'different-process'
                        elif type(ended) in {int, float} and process_started_at(observed) > ended:
                            row['result'] = 'different-process'
                            row['evidence']['proof'] = 'kernel process start is later than the completed recorded operation'
                        else:raise ValueError('live PID lacks exact recorded start or proven reuse')
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    row['evidence']['reason'] = str(exc)
                    if isinstance(exc, OSError) and exc.errno in {errno.EPERM, errno.EACCES}:
                        from .activity_pid_reuse import observe_pid_reuse
                        binding = {'identity': record['identity'], 'state_sha256': record['sha256'],
                                   'check_id': check_id, 'pid': pid, 'pid_role': role}
                        reuse = observe_pid_reuse(pid, completed_operation=check, record_binding=binding,
                                                  inventory=inventory, locks=locks, kernel_error=exc)
                        row['evidence']['independent_start_observation'] = reuse['evidence']
                        row['result'] = reuse['result']
                observations.append(row)
    return observations


def observe_activity(inventory, *, locks, limitations):
    if not isinstance(locks, _HeldLocks):raise ValueError('operational observation requires held runtime lock capability')
    locks.validate()
    result = {'schema_version': 1, 'kind': 'workspace-quiescence-observation',
              'inventory_digest': inventory['digest'], 'invoking_run': inventory['invoking_run'],
              'processes': [], 'locks': list(locks), 'leases': [], 'operations': [],
              'observed_at': time.time(), 'stable': True, 'errors': [], 'limitations': limitations}
    try:
        fresh = inventory_activity(_requests(inventory), invoking_run=inventory['invoking_run'], nonstate=_nonstate(inventory), orphan_review=inventory.get('orphan_review'))
        if fresh != inventory:raise ValueError('runtime bytes/modes/pointers/membership changed from immutable inventory')
        result['processes'] = [*_processes(inventory), *_known_processes(inventory, locks)]
        for root in inventory['roots']:
            for member in root['members']:
                if 'leases' in Path(member['relative_path']).parts or member['relative_path'].endswith('.lease'):
                    result['leases'].append({'identity': str(Path(root['runtime_root']) / member['relative_path']),
                                             'result': 'unknown', 'evidence': 'unsupported relevant lease artifact'})
            for record in root['records']:
                facts = record['operation_facts'];reasons = []
                for auxiliary in facts.get('aux_runs') or []:
                    if (not isinstance(auxiliary, dict) or set(auxiliary) != {'role', 'kind', 'count', 'at'}
                            or type(auxiliary['count']) is not int or auxiliary['count'] < 1):
                        reasons.append('unknown auxiliary operation ownership')
                integration = facts.get('integration')
                if integration and integration.get('pending'):reasons.append('uncommitted execution outbox operations')
                result['operations'].append({'identity': record['identity'], 'result': 'unknown' if reasons else 'quiescent',
                                             'reason': '; '.join(reasons)})
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        result['stable'] = False;result['errors'].append(str(exc))
    try:
        locks.validate()
        if inventory_activity(_requests(inventory), invoking_run=inventory['invoking_run'], nonstate=_nonstate(inventory),
                              orphan_review=inventory.get('orphan_review')) != inventory:
            raise ValueError('runtime inventory moved during activity process inspection')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result['stable'] = False;result['errors'].append(str(exc))
    observation = _FreshObservation(seal(result))
    observation._locks = locks;observation._inventory = inventory['digest']
    observation._digest = observation['digest'];observation._used = False
    return observation


@contextmanager
def locked_activity(inventory):
    """Never create/truncate a missing lock, pointer or runtime directory."""
    validate_seal(inventory, 'workspace-activity-inventory')
    orphans = {subject['identity']['lock_path']: subject for root in inventory['roots'] for subject in root.get('orphan_locks', [])}
    expected = {name: subject['profile']['id'] for name, subject in orphans.items()}
    limitations = ['Legacy runtimes may not share installer coordination; pre/post observations cannot exclude an uncoordinated later start.']
    for root in inventory['roots']:
        runtime = Path(root['runtime_root'])
        for record in root['records']:
            if record['classification'] in {'unknown', 'nonstate-artifact'}:continue
            path = runtime / ('.' + record['identity']['session_id'] + '.lock')
            if not path.exists():
                if record['classification'] == 'current-session':raise ValueError('missing supported modern runtime lock')
                limitations.append('Recognized legacy record lacks supported lock: ' + record['identity']['state_path'])
            else:expected[str(path)] = 'al-session-lock-v1'
        for pointer in root['pointers']:
            if pointer['kind'] == 'claude-conversation':expected[str(Path(pointer['path']).with_suffix('.lock'))] = 'claude-conversation-lock-v1'
        for member in root['members']:
            if member['relative_path'].endswith('.lock') and str(runtime / member['relative_path']) not in expected:
                raise ValueError('unknown runtime lock profile: ' + member['relative_path'])
    held = []
    observations = _HeldLocks();observations.descriptors = [];observations.orphans = []
    try:
        for name, profile in sorted(expected.items()):
            path = Path(name)
            root = next(Path(root['runtime_root']) for root in inventory['roots'] if Path(root['runtime_root']) in path.parents)
            path = under(root, path)
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            held.append(descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if name in orphans:
                observed = physical(path, descriptor)
                if observed != orphans[name]['fingerprint']:raise ValueError('orphan lock moved before acquisition')
                observations.orphans.append((path, descriptor, observed))
            fingerprint = os.fstat(descriptor)
            observations.descriptors.append((path, descriptor, (fingerprint.st_dev, fingerprint.st_ino)))
            observations.append({'path': str(path), 'profile': profile, 'result': 'held'})
        observations.active = True
        yield observations, limitations
    finally:
        observations.active = False
        for descriptor in reversed(held):
            try:fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:os.close(descriptor)


@contextmanager
def quiescence_context(plan):
    inventory = validate_seal(read_reference(plan['activity']['inventory']), 'workspace-activity-inventory')
    with locked_activity(inventory) as (locks, limitations):
        def check():
            observation = observe_activity(inventory, locks=locks, limitations=limitations)
            assessment = require_workspace_quiescent(plan, plan['activity']['acknowledgements'], observation=observation)
            return {'observation': observation, 'assessment': assessment}
        check()
        yield check
