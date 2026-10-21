"""Activity-only UTC whole-second reuse proof, never signal-safe process identity."""
import datetime as D
import math
import re
import subprocess
import sys
import time

from .activity_profiles import decode
from .engine import R
from .registry import canonical, digest

ENV = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'LANG': 'C', 'TZ': 'UTC0'}
MONTHS = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()
WEEKDAYS = 'Mon Tue Wed Thu Fri Sat Sun'.split()
ROW = re.compile(rb'[ \t]*([1-9][0-9]*)[ \t]+([A-Z][a-z]{2})[ \t]+([A-Z][a-z]{2})[ \t]+([0-9]{1,2})[ \t]+([0-9]{2}):([0-9]{2}):([0-9]{2})[ \t]+([0-9]{4})[ \t]*\n\Z')


def finite(value):
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def _record(binding, operation, inventory):
    identity = binding['identity']
    raw = R.safe_absolute(identity['state_path']).read_bytes()
    if digest(raw) != binding['state_sha256']:raise ValueError('historical state digest changed')
    value = decode(raw)
    if value['session_id'] != identity['session_id'] or value.get('run_id') != identity['run_id']:
        raise ValueError('historical PID source identity changed')
    entries = [entry for entry in value['evidence']['entries'] if entry['id'] == binding['check_id']]
    if len(entries) != 1:raise ValueError('historical PID check binding changed')
    entry = entries[0]
    if not {'id', 'status', 'started_at', 'ended_at'} <= set(operation) or any(entry.get(k) != v for k, v in operation.items()):
        raise ValueError('historical PID operation binding changed')
    role, pid = binding['pid_role'], binding['pid']
    if role == 'child':matches = pid in (entry.get('pid'), (entry.get('process') or {}).get('pid'))
    elif role == 'owner':matches = entry.get('owner_pid') == pid
    else:matches = False
    from .activity_operations import check_blockers, reconciliation_validation
    root = next(root for root in inventory['roots'] if root['runtime_root'] == identity['runtime_root'])
    provenance = {'runtime_root': root['runtime_root'], 'members': {m['relative_path']: m for m in root['members']}}
    if check_blockers(entry, reconciliation_validation(entry, value, provenance, binding['state_sha256'])):
        raise ValueError('unresolved historical operation cannot establish a completion bound')
    start, end = entry.get('started_at'), entry.get('ended_at')
    if (type(pid) is not int or pid <= 0 or not matches or entry['status'] not in {'PASS', 'FAIL', 'EMPTY', 'INFRA', 'TORN'}
            or not finite(start) or not finite(end) or end < start
            or not finite(value.get('updated_at')) or value['updated_at'] < end or value['updated_at'] > time.time()):
        raise ValueError('unsupported completed historical PID provenance/timestamps')
    return end


def _parse(raw, pid):
    if len(raw) > 256:raise ValueError('PID start output limit')
    match = ROW.fullmatch(raw)
    if not match or int(match[1]) != pid:raise ValueError('unsupported PID/start output')
    weekday, month = match[2].decode(), match[3].decode()
    if month not in MONTHS or weekday not in WEEKDAYS:raise ValueError('unsupported PID start locale')
    value = D.datetime(int(match[8]), MONTHS.index(month) + 1, int(match[4]), int(match[5]), int(match[6]), int(match[7]), tzinfo=D.timezone.utc)
    if WEEKDAYS[value.weekday()] != weekday:raise ValueError('inconsistent PID start weekday')
    return value.timestamp()


def _clocks():
    result = {}
    for label, name, monotonic in (('wall', 'time', False), ('monotonic', 'monotonic', True)):
        info = time.get_clock_info(name)
        if (not finite(info.resolution) or not 0 < info.resolution < .001
                or info.monotonic is not monotonic or info.adjustable is not (not monotonic)
                or not isinstance(info.implementation, str) or not info.implementation):
            raise ValueError('unsupported activity clock metadata/resolution')
        result[label] = {'resolution': info.resolution, 'implementation': info.implementation,
                         'monotonic': info.monotonic, 'adjustable': info.adjustable}
    return result


def _interval(argv):
    clocks = _clocks()
    m0 = time.monotonic();w0 = time.time();m1 = time.monotonic()
    result = subprocess.run(argv, env=dict(ENV), capture_output=True, timeout=2, check=False)
    m2 = time.monotonic();w1 = time.time();m3 = time.monotonic()
    sample = {'clocks': clocks, 'monotonic': [m0, m1, m2, m3], 'wall': [w0, w1], 'exit': result.returncode,
              'stdout_sha256': digest(result.stdout), 'stderr_sha256': digest(result.stderr),
              'stdout_bytes': len(result.stdout), 'stderr_bytes': len(result.stderr)}
    return sample, result.stdout


def _coherent(sample):
    m0, m1, m2, m3 = sample['monotonic'];w0, w1 = sample['wall']
    clocks = sample['clocks']
    precision = 2 * (clocks['wall']['resolution'] + max(math.ulp(w0), math.ulp(w1))
                     + clocks['monotonic']['resolution'] + max(math.ulp(n) for n in (m0, m1, m2, m3)))
    sample['precision_error_bound_seconds'] = precision
    if (not all(finite(n) for n in (*sample['monotonic'], w0, w1)) or not m0 <= m1 <= m2 <= m3 or w1 < w0
            or not m2 - m1 - precision <= w1 - w0 <= m3 - m0 + precision):
        raise ValueError('incoherent activity inspection clocks')


def observe_pid_reuse(pid, *, completed_operation, record_binding, inventory, locks, kernel_error):
    evidence = {'kind': 'activity-only-independent-PID-start-bound', 'historical_binding': record_binding,
                'inventory_digest': inventory['digest'], 'kernel_error': str(kernel_error),
                'kernel_error_type': type(kernel_error).__name__, 'kernel_errno': getattr(kernel_error, 'errno', None), 'observations': [],
                'precision_margin_seconds': 1, 'signal_authority': False}
    output = {'result': 'unknown', 'evidence': evidence}
    try:
        from .activity_observation import _HeldLocks
        if sys.platform != 'darwin' or not isinstance(locks, _HeldLocks):raise ValueError('unsupported live activity inspection context')
        locks.validate()
        if pid != record_binding['pid']:
            raise ValueError('not an independently bound completed-operation PID')
        record = next((r for root in inventory['roots'] for r in root['records'] if r['identity'] == record_binding['identity']), None)
        if not record or record['sha256'] != record_binding['state_sha256'] or record['errors']:
            raise ValueError('foreign historical inventory binding')
        end = _record(record_binding, completed_operation, inventory)
        evidence['completed_operation_end'] = end
        evidence['held_locks_digest'] = digest(canonical(list(locks)))
        argv = ['/bin/ps', '-p', str(pid), '-o', 'pid=,lstart=']
        evidence.update(argv=argv, environment=dict(ENV), temporal_semantics='validated completed operation wall time; UTC whole-second process start lower bound')
        starts = []
        for _ in range(2):
            locks.validate();_record(record_binding, completed_operation, inventory)
            interval, raw = _interval(argv)
            evidence['observations'].append(interval)
            _coherent(interval)
            if interval['exit'] or interval['stderr_bytes'] or interval['stdout_bytes'] > 256:
                raise ValueError('PID start inspection failed/bounded output exceeded')
            start = _parse(raw, pid);starts.append(start)
            if start > interval['wall'][1]:raise ValueError('implausible future PID start')
        first, last = evidence['observations']
        if first['clocks'] != last['clocks']:raise ValueError('activity clock implementation/resolution changed')
        combined = {'clocks': first['clocks'],
                    'monotonic': [first['monotonic'][0], first['monotonic'][1], last['monotonic'][2], last['monotonic'][3]],
                    'wall': [first['wall'][0], last['wall'][1]]}
        _coherent(combined);evidence['combined_clock_window'] = combined
        locks.validate();_record(record_binding, completed_operation, inventory)
        if starts[0] != starts[1]:raise ValueError('PID start observation changed/disappeared')
        evidence.update(parsed_utc_starts=starts, observed_start_lower_bound=starts[0] - 1)
        if starts[0] - 1 <= end:raise ValueError('start precision overlaps historical operation completion')
        evidence['proof'] = 'different process: independently observed start lower bound exceeds recorded operation completion'
        output['result'] = 'different-process'
    except (OSError, ValueError, KeyError, TypeError, OverflowError, subprocess.SubprocessError) as exc:
        evidence['fallback_error'] = str(exc)
    return output
