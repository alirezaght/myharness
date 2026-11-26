"""Namespace-safe observations of original check owners, never numeric PID authority."""
import ctypes
import os
from pathlib import Path
import sys
import uuid


UNKNOWN = {'owner': 'unknown', 'checks': 'unknown'}


def domain():
    if sys.platform.startswith('linux'):
        with Path('/proc/sys/kernel/random/boot_id').open() as stream:
            boot = stream.read(65).strip()
        if str(uuid.UUID(boot)) != boot:
            raise ValueError('invalid process boot identity')
        namespace = Path('/proc/self/ns/pid').stat()
        return {'platform': 'linux', 'boot': boot, 'namespace': [namespace.st_dev, namespace.st_ino]}
    if sys.platform != 'darwin':
        raise ValueError('unsupported process observation domain')

    class Timeval(ctypes.Structure):
        _fields_ = [('seconds', ctypes.c_long), ('microseconds', ctypes.c_int)]

    observed = Timeval()
    length = ctypes.c_size_t(ctypes.sizeof(observed))
    library = ctypes.CDLL(None, use_errno=True)
    code = library.sysctlbyname(b'kern.boottime', ctypes.byref(observed), ctypes.byref(length), None, 0)
    if code != 0 or length.value != ctypes.sizeof(observed) or observed.seconds <= 0 or not 0 <= observed.microseconds < 1000000:
        raise OSError('process boot observation unavailable')
    return {'platform': 'darwin', 'boot': [observed.seconds, observed.microseconds]}


def identity(pid):
    from installer.activity_process import identity as kernel_identity
    if type(pid) is not int or not 0 < pid <= 2147483647:
        raise ValueError('invalid process identity PID')
    observed = kernel_identity(pid)
    if observed is None:
        return None
    if not _valid_identity(observed) or observed['pid'] != pid:
        raise ValueError('invalid kernel process identity')
    return observed


def _valid_identity(observed):
    return (isinstance(observed, dict) and set(observed) == {'pid', 'pgid', 'sid', 'start', 'zombie'}
            and type(observed['zombie']) is bool
            and all(type(observed[k]) is int and 0 < observed[k] <= 2147483647 for k in ('pid', 'pgid', 'sid'))
            and isinstance(observed['start'], list) and len(observed['start']) == (2 if sys.platform == 'darwin' else 1)
            and all(type(v) is int and 0 <= v < 2**64 for v in observed['start'])
            and observed['start'][0] > 0)


def allocate(entry):
    if os.environ.get('HARNESS_CHECK_CONTEXT'):
        from al_harness_process import request
        return request({'action': 'register_owner', 'binding': {k: entry[k] for k in ('run', 'id', 'reservation')},
                        'coordinator': bool(entry.get('recipe'))})
    return {'schema_version': 1, 'mode': 'local', 'domain': domain(),
            'owner': identity(os.getpid()), 'children': []}


def spawned(entry, proc):
    reference = entry.get('process_observation')
    if isinstance(reference, dict) and reference.get('mode') == 'local':
        # Inspection availability must not abandon the Popen already owned by the caller.
        try:observed = identity(proc.pid)
        except (OSError, ValueError):observed = None
        children = reference.get('children')
        if not isinstance(children, list) or len(children) >= 4096:
            reference['children'] = None
        else:children.append(observed)


def _local_state(saved, *, group=False):
    if not _valid_identity(saved):
        return 'unknown'
    current = identity(saved['pid'])
    if current is not None and current['start'] == saved.get('start') and not current['zombie']:
        return 'live'
    if group:
        if type(saved.get('pgid')) is not int or saved['pgid'] != saved['pid']:
            return 'unknown'
        try:
            os.killpg(saved['pgid'], 0)
        except ProcessLookupError:
            return 'settled'
        return 'unknown'
    return 'settled'


def observe(entry):
    reference = entry.get('process_observation')
    if not isinstance(reference, dict) or type(reference.get('schema_version')) is not int or reference['schema_version'] != 1:
        return dict(UNKNOWN)
    try:
        if reference.get('mode') == 'harness':
            if (not os.environ.get('HARNESS_CHECK_CONTEXT')
                    or any(reference.get(k) != entry.get(k) for k in ('run', 'id', 'reservation'))):
                return dict(UNKNOWN)
            from al_harness_process import request
            result = request({'action': 'observe_owner', 'reference': reference})
            if set(result) != {'owner', 'checks'} or any(v not in {'live', 'settled', 'unknown'} for v in result.values()):
                return dict(UNKNOWN)
            return result
        if os.environ.get('HARNESS_CHECK_CONTEXT') or reference.get('mode') != 'local' or reference.get('domain') != domain():
            return dict(UNKNOWN)
        if set(reference) != {'schema_version', 'mode', 'domain', 'owner', 'children'}:
            return dict(UNKNOWN)
        children = reference['children']
        if not isinstance(children, list) or len(children) > 4096:
            return dict(UNKNOWN)
        states = [_local_state(child, group=True) for child in children]
        checks = ('live' if 'live' in states else 'unknown' if 'unknown' in states or not states and not entry.get('recipe') else 'settled')
        return {'owner': _local_state(reference['owner']), 'checks': checks}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError):
        return dict(UNKNOWN)


def same_owner(parent, child=None):
    left = parent.get('process_observation')
    if not isinstance(left, dict) or observe(parent)['owner'] != 'live':
        return False
    if child is None:
        # Admission before child allocation must still identify this actual owner.
        if left.get('mode') == 'local':
            try:
                return left.get('domain') == domain() and left.get('owner') == identity(os.getpid())
            except (OSError, ValueError):
                return False
        from al_harness_process import request
        try:
            return request({'action': 'same_owner', 'reference': left}) == {'same_owner': True}
        except (OSError, ValueError):
            return False
    right = child.get('process_observation')
    if (not isinstance(right, dict) or right.get('mode') != left.get('mode')
            or observe(child)['owner'] != 'live'):
        return False
    keys = ('host_instance', 'owner_ref') if left['mode'] == 'harness' else ('domain', 'owner')
    return all(left.get(k) == right.get(k) for k in keys)
