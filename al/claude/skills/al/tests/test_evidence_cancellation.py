"""Future handled exits retain logs and never commit commit=False reservations."""
import hashlib
from pathlib import Path
import signal
import subprocess
import sys

import al_evidence as E
import pytest


def entry(command):
    return {'id': 'E900', 'area': 'owned-cancellation', 'run': 'isolated', 'kind': 'check',
            'argv': command, 'cwd': '.', 'scope': [], 'env': [], 'fp_before': None}


@pytest.mark.parametrize('command,timeout,status', [
    ([sys.executable, '-c', 'print("owned child")'], 5, 'PASS'),
    (['al-deliberately-missing-command'], 5, 'INFRA'),
    ([sys.executable, '-c', 'import time; time.sleep(60)'], 0.05, 'INFRA'),
])
def test_finalized_log_and_no_commit_for_all_outcomes(command, timeout, status, monkeypatch):
    monkeypatch.setattr(E, '_commit', lambda *a: pytest.fail('commit=False attempted state mutation'))
    value = entry(command)
    E._execute('isolated', value, timeout, commit=False)
    assert value['status'] == status
    assert value['log_sha256'] == hashlib.sha256(b'\0-' + Path(value['log']).read_bytes()).hexdigest()
    assert value['ended_at']


def test_interruption_is_finalized_before_reraising_without_commit(monkeypatch):
    original = subprocess.Popen.wait
    interrupted = False
    def once(proc, *a, **kw):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt('explicit owned interruption')
        return original(proc, *a, **kw)
    monkeypatch.setattr(subprocess.Popen, 'wait', once)
    monkeypatch.setattr(E, '_commit', lambda *a: pytest.fail('commit=False attempted state mutation'))
    value = entry([sys.executable, '-c', 'import time; time.sleep(60)'])
    with pytest.raises(KeyboardInterrupt):E._execute('isolated', value, 5, commit=False)
    assert value['status'] == 'INFRA' and value['interrupted'] and value['log_sha256']
    assert not value['cleanup']['unresolved']


def test_timeout_preserves_primary_cause_when_cleanup_signal_is_denied(monkeypatch):
    original = E.os.killpg
    def denied_once(pid, number):
        if number == signal.SIGTERM:raise PermissionError('owned fixture denial')
        return original(pid, number)
    monkeypatch.setattr(E.os, 'killpg', denied_once)
    value = entry([sys.executable, '-c', 'import time; time.sleep(60)'])
    E._execute('isolated', value, 0.05, commit=False)
    assert value['status'] == 'INFRA' and 'timed out' in value['note']
    assert value['cleanup']['errors'] and not value['cleanup']['unresolved'] and value['log_sha256']
