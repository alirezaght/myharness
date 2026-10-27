"""Deterministic independent OS start bounds resolve only a historical PID reference."""
import errno
import json
import math
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from al.installer import activity_history as H, activity_observation as O, activity_pid_reuse as P
from al.installer.activity_inventory import inventory_activity
from al.installer.activity_observation import locked_activity, quiescence_context
from al.installer.registry import canonical, digest
from al.tests.activity_helpers import legacy, planned, scenario, write_state

BASE = 1792454400.0
PID = 424242
GOOD = b'424242 Tue Oct 20 00:00:00 2026\n'


class Clock:
    value = 0.0
    def monotonic(self):
        self.value += .01
        return self.value
    def time(self):return BASE + 100 + self.value
    def get_clock_info(self,name):
        return SimpleNamespace(resolution=1e-7,implementation='deterministic Darwin clock',
                               monotonic=name=='monotonic',adjustable=name=='time')


def case_with_pid(tmp_path, monkeypatch, *, end=BASE - 100, status='PASS', role='child', interrupted=False, start=BASE - 200, cleanup=None, receipt=None):
    case = scenario(tmp_path, monkeypatch)
    value = legacy();value.update(run_id='historical-run', updated_at=BASE + 50)
    entry = {'id': 'E1', 'argv': ['historical-check'], 'status': status, 'run': 'historical-run',
             'reservation': 'historical-reservation', 'started_at': start, 'ended_at': end}
    if interrupted:entry['interrupted'] = True
    entry['pid' if role == 'child' else 'owner_pid'] = PID
    if cleanup is not None:entry['cleanup'] = cleanup
    if receipt is not None:
        artifact = case['old_runtime'] / 'historical-run/artifacts/reconciliation'
        artifact.parent.mkdir(parents=True);artifact.write_bytes(b'Historical human support artifact; arbitrary bytes retained by the guarded command.')
        sha = digest(artifact.read_bytes())
        ref = {'id': sha, 'kind': 'check-reconciliation', 'sha256': sha, 'stored_path': str(artifact),
               'source': str(tmp_path / 'human-observation'), 'captured_at': BASE - 90}
        entry.update(status='INFRA', interrupted=True, note='interrupted check explicitly reconciled',
                     reconciliation={'reason': 'Original guarded check-reconcile observed direct processes absent.', 'artifact': ref})
        if receipt == 'boolean':entry['reconciliation'] = True
        elif receipt == 'wrong-kind':ref['kind'] = 'other-artifact'
        elif receipt == 'corrupt':artifact.write_bytes(b'changed supporting bytes')
        elif receipt == 'wrong-note':entry['note'] = 'manually marked resolved'
    value['evidence'] = {'seq': 1, 'plan': {}, 'entries': [entry]}
    write_state(case['old_runtime'], value)
    ref = case['request']['history']['states'];path = Path(ref['path']);states = json.loads(path.read_text())
    states['members'][1].update(sha256=digest(case['old_path'].read_bytes()), run_id='historical-run')
    path.write_bytes(canonical(states));ref['sha256'] = digest(path.read_bytes());H.ANCHORS['states'] = ref['sha256']
    case['inventory'] = inventory_activity(case['roots'], invoking_run=case['invoking'])
    case['plan'] = planned(case, tmp_path)
    real = O.kernel_identity
    def denied(pid):
        if pid == PID:raise PermissionError(errno.EPERM, 'kernel inspection denied')
        return real(pid)
    monkeypatch.setattr(O, 'kernel_identity', denied)
    monkeypatch.setattr(P, 'sys', SimpleNamespace(platform='darwin'))
    clock = Clock();monkeypatch.setattr(P, 'time', clock)
    return case, clock


def ps_rows(monkeypatch, values):
    real = P.subprocess.run;seen = []
    def run(argv, **kwargs):
        if argv[:2] != ['/bin/ps', '-p']:return real(argv, **kwargs)
        seen.append((argv, kwargs))
        value = values[min(len(seen) - 1, len(values) - 1)]
        if callable(value):value = value()
        if isinstance(value, Exception):raise value
        if isinstance(value, tuple):return subprocess.CompletedProcess(argv, value[0], value[1], value[2])
        return subprocess.CompletedProcess(argv, 0, value, b'')
    monkeypatch.setattr(P.subprocess, 'run', run)
    return seen


@pytest.mark.parametrize('role', ['child', 'owner'])
def test_two_fresh_later_starts_resolve_only_hash_bound_historical_pid(tmp_path, monkeypatch, role):
    case, _ = case_with_pid(tmp_path, monkeypatch, role=role);seen = ps_rows(monkeypatch, [GOOD])
    with quiescence_context(case['plan']) as check:
        assert len(seen) == 2  # context admission independently proves reuse
        result = check()
        assert len(seen) == 4  # explicit freshness check repeats both observations
        row = next(p for p in result['observation']['processes'] if p['pid'] == PID)
        assert row['result'] == 'different-process', row['evidence']
        proof = row['evidence']['independent_start_observation']
        assert proof['kernel_error'] and not proof['signal_authority']
        assert proof['historical_binding']['state_sha256'] == digest(case['old_path'].read_bytes())
        assert proof['historical_binding']['pid_role'] == role
        assert len(proof['observations']) == 2 and proof['observed_start_lower_bound'] == BASE - 1
        assert proof['inventory_digest'] == case['inventory']['digest'] and proof['held_locks_digest']
    assert len(seen) == 4
    assert all(argv == ['/bin/ps', '-p', str(PID), '-o', 'pid=,lstart='] and opts['env'] == P.ENV
               and opts['timeout'] == 2 and 'shell' not in opts for argv, opts in seen)


@pytest.mark.parametrize('end', [BASE, BASE - 1, BASE - .5, BASE + 1, BASE - 300, None, True, float('nan'), float('inf')])
def test_overlap_or_invalid_completion_never_proves_reuse(tmp_path, monkeypatch, end):
    if type(end) is float and not math.isfinite(end):
        with pytest.raises(ValueError, match='incomplete activity inventory'):case_with_pid(tmp_path, monkeypatch, end=end)
        return
    case, _ = case_with_pid(tmp_path, monkeypatch, end=end)
    ps_rows(monkeypatch, [GOOD])
    with pytest.raises(ValueError, match='blocked|incomplete'):
        with quiescence_context(case['plan']):pass


@pytest.mark.parametrize('status', ['RUNNING', 'INFRA'])
def test_unresolved_operations_cannot_use_start_fallback(tmp_path, monkeypatch, status):
    case, _ = case_with_pid(tmp_path, monkeypatch, status=status, interrupted=status == 'INFRA')
    ps_rows(monkeypatch, [GOOD])
    with pytest.raises(ValueError, match='blocked|incomplete'):
        with quiescence_context(case['plan']):pass


BAD = [b'', b'\n' + GOOD, GOOD + b'\n', b'424242 Tue Oct 20 00:00:00\n', b'424242 Tue Oct 20 00:00:00 2026',
       GOOD + GOOD, GOOD.replace(b'424242', b'111111'), GOOD.replace(b'Oct 20', b'Feb 31'),
       GOOD.replace(b'Tue', b'Mon'), GOOD.replace(b'Oct', b'Okt'), GOOD.rstrip() + b' root system-tool\n',
       b'x' * 257, (1, b'', b''), (0, GOOD, b'denied'), subprocess.TimeoutExpired(['/bin/ps'], 2),
       PermissionError(errno.EPERM, 'second inspection denied')]


@pytest.mark.parametrize('bad', BAD)
def test_unavailable_malformed_locale_or_owner_name_output_stays_unknown(tmp_path, monkeypatch, bad):
    case, _ = case_with_pid(tmp_path, monkeypatch);seen = ps_rows(monkeypatch, [bad])
    monkeypatch.setenv('LC_ALL', 'hostile');monkeypatch.setenv('TZ', 'Pacific/Honolulu');monkeypatch.setenv('DYLD_INSERT_LIBRARIES', '/untrusted')
    with pytest.raises(ValueError, match='blocked|incomplete'):
        with quiescence_context(case['plan']):pass
    assert len(seen) == 1  # output/OS refusal was reached with supported clock metadata


@pytest.mark.parametrize('mutation', ['different', 'disappeared', 'future', 'clock', 'state', 'membership'])
def test_changed_observation_source_or_clock_is_not_reusable(tmp_path, monkeypatch, mutation):
    case, clock = case_with_pid(tmp_path, monkeypatch)
    if mutation == 'different':values = [GOOD, GOOD.replace(b'00:00:00', b'00:00:01')]
    elif mutation == 'disappeared':values = [GOOD, b'']
    elif mutation == 'future':values = [GOOD.replace(b'00:00:00', b'00:10:00')]
    else:
        def changed():
            if mutation == 'clock':clock.value -= 10
            elif mutation == 'state':case['old_path'].write_bytes(case['old_path'].read_bytes() + b' ')
            else:(case['old_runtime'] / 'new-unreviewed-artifact').write_text('new')
            return GOOD
        values = [changed]
    ps_rows(monkeypatch, values)
    with pytest.raises(ValueError, match='blocked|incomplete'):
        with quiescence_context(case['plan']):pass


def test_invalid_role_foreign_digest_and_expired_capability_cannot_authorize_reuse(tmp_path, monkeypatch):
    case, _ = case_with_pid(tmp_path, monkeypatch);ps_rows(monkeypatch, [GOOD])
    record = case['inventory']['roots'][1]['records'][0];operation = record['operation_facts']['checks'][0]
    binding = {'identity': record['identity'], 'state_sha256': record['sha256'], 'check_id': 'E1', 'pid': PID, 'pid_role': 'child'}
    with locked_activity(case['inventory']) as (locks, _):
        for change in ({'pid_role': 'invented'}, {'state_sha256': 'f' * 64}, {'pid': True}):
            value = P.observe_pid_reuse(PID, completed_operation=operation, record_binding={**binding, **change},
                                       inventory=case['inventory'], locks=locks, kernel_error=PermissionError('denied'))
            assert value['result'] == 'unknown' and value['evidence']['fallback_error']
    value = P.observe_pid_reuse(PID, completed_operation=operation, record_binding=binding,
                               inventory=case['inventory'], locks=locks, kernel_error=PermissionError('denied'))
    assert value['result'] == 'unknown'


@pytest.mark.parametrize('blocker', ['current-run', 'writer', 'lease'])
def test_resolved_pid_does_not_waive_other_activity(tmp_path, monkeypatch, blocker):
    case, _ = case_with_pid(tmp_path, monkeypatch);ps_rows(monkeypatch, [GOOD])
    if blocker == 'lease':(case['old_runtime'] / 'live.lease').write_text('unknown')
    else:
        value = json.loads(case['current_path'].read_text())
        if blocker == 'current-run':value.update(status='RUNNING', phase='IMPLEMENTATION')
        else:value['writers'] = {'writer': {'id': 'writer', 'state': 'ACTIVE'}}
        write_state(case['runtime'], value)
    case['inventory'] = inventory_activity(case['roots'], invoking_run=case['invoking'])
    original_inventory = (tmp_path / 'inventory.json').read_bytes()
    case['request']['directory'] = str(tmp_path / 'new-ack-directory');plan = planned(case, tmp_path / 'next-plan')
    assert (tmp_path / 'inventory.json').read_bytes() == original_inventory
    with pytest.raises(ValueError, match='blocked'):
        with quiescence_context(plan):pass


def test_independent_pid_reuse_does_not_waive_actual_new_al_process(tmp_path, monkeypatch):
    from al.tests.test_activity_orphans import process
    case, _ = case_with_pid(tmp_path, monkeypatch);ps_rows(monkeypatch, [GOOD])
    with process(case, tmp_path, session='new-unreviewed-run'):
        with pytest.raises(ValueError, match='runtime-root process'):
            with quiescence_context(case['plan']):pass


@pytest.mark.parametrize('start', [None, True, BASE + 1])
def test_missing_or_contradictory_operation_start_is_unknown(tmp_path, monkeypatch, start):
    case, _ = case_with_pid(tmp_path, monkeypatch, start=start);ps_rows(monkeypatch, [GOOD])
    with pytest.raises(ValueError, match='blocked|incomplete'):
        with quiescence_context(case['plan']):pass


def test_unsupported_platform_has_no_independent_start_fallback(tmp_path, monkeypatch):
    case, _ = case_with_pid(tmp_path, monkeypatch);ps_rows(monkeypatch, [GOOD])
    monkeypatch.setattr(P, 'sys', SimpleNamespace(platform='unsupported'))
    with pytest.raises(ValueError, match='blocked|incomplete'):
        with quiescence_context(case['plan']):pass


def test_recorded_os_clock_quantization_is_not_misclassified_as_discontinuity():
    sample = {'monotonic': [1866207.388843708, 1866207.388843833, 1866207.392375416, 1866207.392375791],
              'wall': [1792456121.395419, 1792456121.3989499],
              'clocks': {'wall': {'resolution': 1.0000000000000002e-6},
                         'monotonic': {'resolution': 4.166666666666666e-8}}}
    P._coherent(sample)
    assert 2e-6 < sample['precision_error_bound_seconds'] < 3e-6
    sample['wall'][1] += 10e-6
    with pytest.raises(ValueError, match='incoherent'):P._coherent(sample)


@pytest.mark.parametrize('resolution', [0, -1, True, float('nan'), float('inf'), .001])
def test_unsupported_clock_resolution_cannot_relax_precision(tmp_path, monkeypatch, resolution):
    case, _ = case_with_pid(tmp_path, monkeypatch);seen = ps_rows(monkeypatch, [GOOD])
    original = P.time.get_clock_info
    def info(name):
        value = original(name)
        return SimpleNamespace(resolution=resolution, implementation=value.implementation,
                               monotonic=value.monotonic, adjustable=value.adjustable)
    monkeypatch.setattr(P.time, 'get_clock_info', info)
    with pytest.raises(ValueError, match='blocked|incomplete'):
        with quiescence_context(case['plan']):pass

    assert not seen  # unsupported metadata refuses before invoking ps
