"""Bounded Darwin KERN_PROCARGS2 framing without host process observations."""
import ctypes
import errno
import select
import subprocess
import sys
from types import SimpleNamespace

import pytest

from al.installer import execution_process as processes


def framed(arguments, *, argc=None, padding=3, suffix=b'ENVIRONMENT=synthetic\0'):
    count = len(arguments) if argc is None else argc
    return (bytes(ctypes.c_int(count)) + b'/synthetic/executable\0' + b'\0' * padding
            + b'\0'.join(arguments) + b'\0' + suffix)


def kernel(monkeypatch, raw, *, query_size=None, returned_size=None, deny=None):
    calls = []

    def sysctl(mib, count, buffer, size, new, new_size):
        assert list(mib) == [1, 49, 123]
        assert (count, new, new_size) == (3, None, 0)
        calls.append(buffer is not None)
        if deny == len(calls):
            ctypes.set_errno(errno.EPERM)
            return -1
        if buffer is None:
            size._obj.value = len(raw) if query_size is None else query_size
        else:
            assert len(raw) <= len(buffer)
            ctypes.memmove(buffer, raw, len(raw))
            size._obj.value = len(raw) if returned_size is None else returned_size
        return 0

    monkeypatch.setattr(processes, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(processes.ctypes, 'CDLL', lambda *args, **kwargs: SimpleNamespace(sysctl=sysctl))
    return calls


@pytest.mark.parametrize('arguments', [
    [b'python', b'argument'],
    [b'python', b'', b'argument'],
    [b'python', b'argument', b''],
    [b'python', b'', b'', b'argument', b'', b''],
    [b'python', *([b''] * 20)],
    [b'python', *([b''] * 1023)],
    [b'python', b'\xff', b''],
])
@pytest.mark.parametrize('suffix', [b'', b'ENVIRONMENT=synthetic\0'])
def test_darwin_preserves_declared_empty_argument_fields(monkeypatch, arguments, suffix):
    calls = kernel(monkeypatch, framed(arguments, suffix=suffix))
    result = processes.process_arguments(123)
    assert [processes.os.fsencode(value) for value in result] == arguments
    assert calls == [False, True]


@pytest.mark.parametrize('argc', [-1, 0, 1025])
def test_darwin_refuses_invalid_argument_count(monkeypatch, argc):
    kernel(monkeypatch, framed([b'python'], argc=argc))
    with pytest.raises(ValueError, match='framing'):
        processes.process_arguments(123)


@pytest.mark.parametrize('raw', [
    bytes(ctypes.c_int(1)) + b'no-executable-terminator',
    bytes(ctypes.c_int(1)) + b'/executable\0\0\0',
    bytes(ctypes.c_int(2)) + b'/executable\0python\0unterminated',
    bytes(ctypes.c_int(3)) + b'/executable\0python\0argument\0',
])
def test_darwin_refuses_missing_framing_or_argument_terminators(monkeypatch, raw):
    kernel(monkeypatch, raw)
    with pytest.raises(ValueError, match='framing|incomplete'):
        processes.process_arguments(123)


@pytest.mark.parametrize('size', [0, 4, 1024 * 1024 + 1])
def test_darwin_refuses_unbounded_size_before_allocation(monkeypatch, size):
    calls = kernel(monkeypatch, b'', query_size=size)
    with pytest.raises(OSError, match='unavailable'):
        processes.process_arguments(123)
    assert calls == [False]


def test_darwin_only_parses_returned_bytes_not_zero_filled_capacity(monkeypatch):
    raw = framed([b'python', b'argument'], suffix=b'')
    kernel(monkeypatch, raw, query_size=len(raw) + 128, returned_size=len(raw) - 1)
    with pytest.raises(ValueError, match='incomplete'):
        processes.process_arguments(123)


@pytest.mark.parametrize('denied_call', [1, 2])
def test_darwin_permission_errors_remain_errors(monkeypatch, denied_call):
    calls = kernel(monkeypatch, framed([b'python', b'']), deny=denied_call)
    with pytest.raises(OSError) as error:
        processes.process_arguments(123)
    assert error.value.errno == errno.EPERM
    assert len(calls) == denied_call


@pytest.mark.parametrize('pid', [0, -1, True, '123'])
def test_invalid_pid_never_queries_kernel(monkeypatch, pid):
    calls = kernel(monkeypatch, framed([b'python']))
    with pytest.raises(ValueError, match='PID'):
        processes.process_arguments(pid)
    assert calls == []


@pytest.mark.parametrize('returned_size', [0, 4, 1024])
def test_darwin_refuses_invalid_returned_size(monkeypatch, returned_size):
    kernel(monkeypatch, framed([b'python']), returned_size=returned_size)
    with pytest.raises(ValueError, match='argument size'):
        processes.process_arguments(123)


def test_darwin_refuses_empty_executable_path(monkeypatch):
    kernel(monkeypatch, bytes(ctypes.c_int(1)) + b'\0python\0')
    with pytest.raises(ValueError, match='framing'):
        processes.process_arguments(123)


@pytest.mark.parametrize('reason', ['known', 'cwd', 'unavailable', 'cwd-unavailable'])
def test_empty_or_unavailable_fields_do_not_exempt_process(monkeypatch, tmp_path, reason):
    from al.installer import runtime_successor_census as census

    root = tmp_path / 'repository'
    root.mkdir()
    other = tmp_path / 'other'
    other.mkdir()
    transaction = {'repository_identity': {'root': str(root)}, 'session_id': 'synthetic-session',
                   'run_id': 'synthetic-run'}
    monkeypatch.setattr(census, 'require_operations', lambda *args: None)
    monkeypatch.setattr(census, 'sys', SimpleNamespace(modules={
        '_al_validator_context': SimpleNamespace(request={'mode': 'snapshot'})}))
    monkeypatch.setattr(census, 'identity', lambda pid: {'pid': pid, 'start': [1], 'zombie': False})
    def arguments(pid):
        if pid == 123 and reason == 'unavailable':
            raise PermissionError('synthetic unavailable argv')
        return ['python', '', '']

    monkeypatch.setattr(census, 'process_arguments', arguments)
    monkeypatch.setattr(census, 'process_cwd', lambda pid: None if reason == 'cwd-unavailable'
                        else root if reason == 'cwd' else other)
    monkeypatch.setattr(census.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=f'123 {census.os.getuid()}\n'.encode()))
    known = [(123, {'pid': 123, 'start': [1]})] if reason == 'known' else []
    monkeypatch.setattr(census, 'recorded_processes', lambda state: known)
    message = {'known': 'task-owned process is live', 'cwd': 'unproven repository process ownership',
               'unavailable': 'successor process ownership unavailable',
               'cwd-unavailable': 'task process cwd unavailable'}[reason]
    with pytest.raises(ValueError, match=message):
        census.require_quiescent(root, transaction=transaction, state={})


def test_linux_retains_empty_fields_with_no_darwin_fallback(monkeypatch):
    monkeypatch.setattr(processes, 'sys', SimpleNamespace(platform='linux'))
    monkeypatch.setattr(processes, 'Path', lambda path: SimpleNamespace(read_bytes=lambda: b'python\0\0value\0\0'))
    assert processes.process_arguments(123) == ['python', '', 'value', '']


@pytest.mark.skipif(sys.platform != 'darwin', reason='actual Darwin KERN_PROCARGS2 observation')
def test_owned_disposable_child_preserves_empty_arguments(tmp_path):
    # Fixed synthetic argv and empty environment; no ambient process is inspected.
    program = 'import sys; sys.stdout.write("ready\\n"); sys.stdout.flush(); sys.stdin.read(1)'
    child = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', program, '', 'synthetic', ''],
                             cwd=tmp_path, env={}, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    try:
        ready, _, _ = select.select([child.stdout], [], [], 5)
        assert ready, 'synthetic child did not become ready'
        assert child.stdout.readline() == b'ready\n'
        observed = processes.process_arguments(child.pid)
        assert len(observed) == 9
        assert observed[-3:] == ['', 'synthetic', '']
    finally:
        if child.poll() is None:
            child.terminate()
        try:
            child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=5)
    assert child.poll() is not None
