"""Coordinator diagnostics preserve the isolated writer dispatch contract."""
from contextlib import contextmanager
import io
import sys
from types import SimpleNamespace

import pytest

from al.installer import runtime_run as R
from al.installer import runtime_successor_store
from al.tests.runtime_run_helpers import launched as launched, public_run, start


class Delegated(BaseException):
    pass


class Diagnostic(io.StringIO):
    flushes = 0

    def flush(self):
        self.flushes += 1
        return super().flush()


@pytest.fixture
def coordinator(tmp_path, monkeypatch):
    """Stop exactly at execv; exercise coordinator boundaries without native authority."""
    monkeypatch.delenv('AL_RUNTIME_DIR', raising=False)
    monkeypatch.delenv('CLAUDE_PROJECT_DIR', raising=False)
    stderr = Diagnostic()
    isolated = SimpleNamespace(flags=SimpleNamespace(isolated=True, no_site=True),
                               dont_write_bytecode=True, stderr=stderr, modules=sys.modules)
    monkeypatch.setattr(R, 'sys', isolated)
    value = {'repository_identity': {'root': str(tmp_path)}}
    transaction = {'status': 'ACTIVE', 'pin': {'fixture': 'exact binding'}}
    path = tmp_path / 'binding.json'
    path.write_text('fixture')
    events = []
    monkeypatch.setattr(R, 'subject', lambda *args: value)
    monkeypatch.setattr(R, 'storage', lambda **kwargs: tmp_path)
    monkeypatch.setattr(runtime_successor_store, 'require_ordinary_authority', lambda value: None)
    @contextmanager
    def coordinated(*args, **kwargs):
        events.append(('lock', stderr.getvalue()))
        yield path
    monkeypatch.setattr(R, 'coordinated', coordinated)
    monkeypatch.setattr(R, 'read', lambda path: transaction)
    monkeypatch.setattr(R, 'validate', lambda *args: None)
    manifest = {'interpreter': {'executable': '/fixture/python'}, 'interpreter_execution_identity': {}}
    def verify(*args):
        events.append(('verify', stderr.getvalue()))
        return {'manifest': manifest}
    monkeypatch.setattr(R, 'verify_pin', verify)
    monkeypatch.setattr(R, 'validate_live', lambda *args: None)
    monkeypatch.setattr(R, 'observe', lambda *args: {})
    requests, executions = [], []
    def program(request):
        requests.append(request)
        return 'EXACT FIXTURE PROGRAM'
    monkeypatch.setattr(R, 'program', program)
    def execute(executable, argv):
        executions.append((executable, argv))
        raise Delegated()
    monkeypatch.setattr(R.os, 'execv', execute)
    return SimpleNamespace(stderr=stderr, sys=isolated, events=events, requests=requests,
                           executions=executions, transaction=transaction, root=tmp_path)


def dispatch(coordinator, argv):
    with pytest.raises(Delegated):
        R.run(str(coordinator.root), 'codex', 'fixture-session', argv)


def test_writer_start_reports_real_boundaries_and_keeps_exec_contract(coordinator, capsys):
    argv = ['writer', 'start', '--files', 'private-path', '--role', 'private-role']
    dispatch(coordinator, argv)
    lines = coordinator.stderr.getvalue().splitlines()
    assert len(lines) == 4
    assert coordinator.stderr.flushes == 4
    assert all(line.startswith('AL writer start [') and 's] ' in line for line in lines)
    assert 'validating repository and authority' in lines[0]
    assert 'acquiring protected admission lock' in lines[1]
    assert 'verifying immutable runtime resources' in lines[2]
    assert 'delegating to pinned runtime' in lines[3]
    assert 'acquiring protected admission lock' in coordinator.events[0][1]
    assert 'verifying immutable runtime resources' in coordinator.events[1][1]
    assert 'private-' not in coordinator.stderr.getvalue()
    assert str(coordinator.root) not in coordinator.stderr.getvalue()
    assert capsys.readouterr().out == ''
    assert coordinator.executions == [('/fixture/python',
        ['/fixture/python', '-I', '-S', '-B', '-c', 'EXACT FIXTURE PROGRAM'])]
    assert coordinator.requests == [{'transaction': coordinator.transaction, 'argv': argv,
        'storage': str(coordinator.root), 'existing_run': False, 'native_implicit': False}]


def test_elapsed_diagnostics_use_the_monotonic_clock(coordinator, monkeypatch):
    times = iter([100.0, 101.0, 102.5, 104.0, 109.0])
    monkeypatch.setattr(R, 'time', SimpleNamespace(monotonic=lambda: next(times)))
    dispatch(coordinator, ['writer', 'start'])
    assert [line.split(']')[0] for line in coordinator.stderr.getvalue().splitlines()] == [
        'AL writer start [1.0s', 'AL writer start [2.5s',
        'AL writer start [4.0s', 'AL writer start [9.0s']


@pytest.mark.parametrize('argv', [['show', '--json'], ['writer', 'finish'], ['start'], ['writer', 'handoff']])
def test_other_commands_are_noise_free(coordinator, capsys, argv):
    dispatch(coordinator, argv)
    assert coordinator.stderr.getvalue() == ''
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('failure', ['closed', 'broken-pipe', 'absent'])
def test_unavailable_diagnostic_stream_cannot_change_dispatch(coordinator, failure):
    if failure == 'closed':
        coordinator.sys.stderr = io.StringIO()
        coordinator.sys.stderr.close()
    elif failure == 'broken-pipe':
        class Broken:
            def write(self, text):
                raise BrokenPipeError('closed diagnostic pipe')
            def flush(self):
                raise BrokenPipeError('closed diagnostic pipe')
        coordinator.sys.stderr = Broken()
    else:
        coordinator.sys.stderr = None
    dispatch(coordinator, ['writer', 'start'])
    assert len(coordinator.executions) == 1


def test_authority_error_still_refuses(coordinator, monkeypatch):
    def refuse(value):
        raise ValueError('exact authority refusal')
    monkeypatch.setattr(runtime_successor_store, 'require_ordinary_authority', refuse)
    with pytest.raises(ValueError, match='exact authority refusal'):
        R.run(str(coordinator.root), 'codex', 'fixture-session', ['writer', 'start'])
    assert not coordinator.executions
    assert 'delegating' not in coordinator.stderr.getvalue()


def test_real_isolated_writer_refusal_preserves_stdout_and_state(launched):
    state = start(launched)
    before = state.read_bytes()
    shown = public_run(launched, 'codex', 'show', '--json')
    assert shown.returncode == 0 and 'AL writer start [' not in shown.stderr
    result = public_run(launched, 'codex', 'writer', 'start', '--id', 'progress-fixture',
                        '--role', 'al-engineer', '--files', 'three-owned-paths')
    assert result.returncode != 0
    assert len([line for line in result.stderr.splitlines() if line.startswith('AL writer start [')]) == 4
    assert 'AL writer start [' not in result.stdout
    assert state.read_bytes() == before
