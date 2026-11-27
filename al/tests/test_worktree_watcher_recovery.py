"""Request-driven recovery with owned children and controlled watcher protocol.

These tests prove recovery/currentness semantics, not native FSEvents reliability.
"""
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import subprocess
import time
from types import SimpleNamespace

import pytest

from al.installer import runtime_service as R
from al.installer.source import git_env

WATCH = r'''
import json, os, sys
os.write(1, b'Rcontrolled\0')
for line in sys.stdin.buffer:
    record = json.loads(line)
    os.write(1, record['kind'].encode() + os.fsencode(record['path']) + b'\0')
'''


class Recovery:
    def __init__(self, root, monkeypatch):
        self.root = root.resolve()
        self.git('init', '-q', str(self.root), cwd=root.parent)
        self.source = self.root / 'app.py'
        self.source.write_text('VALUE = 1\n')
        self.now = 0.0
        self.cookies = True
        self.children = []
        self.watchers = []
        self.channels = []
        self.gate = None
        self.service = R.Service.__new__(R.Service)
        s = self.service
        s.request = {'common_dir': str(self.root / '.git')}
        s.selector = selectors.DefaultSelector()
        s.worktrees, s.locations, s.pending = {}, {}, {}
        s.environment, s.packs = dict(os.environ), {'unrelated-pack': {}}
        s.pin_ready, s.pin_watch, s.stopping = True, object(), False
        s.captured = {'bytes': {'installer/runtime_service_watch.py': WATCH.encode()}}
        s.helper = self.helper
        monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'codex/skills/al/scripts'))
        monkeypatch.setattr(R, 'time', SimpleNamespace(monotonic=lambda: time.monotonic() + self.now, time=time.time,
                                                      strftime=time.strftime, gmtime=time.gmtime))
        monkeypatch.setattr(R, 'SETTLE', 0)
        real_open = os.open
        real_popen = subprocess.Popen

        def opened(path, flags, *args, **kwargs):
            fd = real_open(path, flags, *args, **kwargs)
            if Path(path).name.startswith('al-service-cookie.') and self.cookies:
                w = s.worktrees.get(str(self.root))
                if w is not None and w.process is not None:
                    self.send(w, 'E', str(path))
            return fd

        def launched(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            self.children.append(child)
            if (args and args[0][:5] == [R.sys.executable, '-I', '-S', '-B', '-c']
                    and kwargs.get('stdin') == subprocess.PIPE and kwargs.get('stdout') == subprocess.PIPE):
                self.watchers.append(child)
            return child

        monkeypatch.setattr(R.os, 'open', opened)
        monkeypatch.setattr(R.subprocess, 'Popen', launched)

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.root, env=git_env(),
                              check=True, capture_output=True, timeout=10).stdout.decode().strip()

    @property
    def worktree(self):
        return self.service.worktrees[str(self.root)]

    def request(self):
        return self.service.snapshot_answer({'repository': str(self.root)})

    def later(self):
        self.now += R.WATCH_RETRY + 0.1

    def send(self, worktree, kind, path):
        worktree.process.stdin.write((json.dumps({'kind': kind, 'path': path}) + '\n').encode())
        worktree.process.stdin.flush()

    def until(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate():
            remaining = deadline - time.monotonic()
            assert remaining > 0, 'controlled watcher/helper did not reach its barrier'
            for key, _ in self.service.selector.select(remaining):
                kind, worktree = key.data
                if kind == 'watch':
                    worktree.readable()
                elif kind == 'helper':
                    self.service.helper_done(key.fileobj, worktree)

    def ready(self):
        self.until(lambda: self.worktree.ready)

    def helper(self, root, write):
        try:
            if self.gate is not None:
                os.close(self.gate[1])
                assert os.read(self.gate[0], 1) == b'!'
            self.git('add', '-A')
            os.write(write, self.git('write-tree').encode())
        finally:
            os._exit(0)

    def snapshot(self):
        self.service.background()
        self.until(lambda: self.worktree.helper is None)
        return self.request()['tree']

    def channel(self):
        state, parent = self.service.memory({'repository': str(self.root)})
        assert state['worktree'] is self.worktree
        self.channels.extend([parent, state['channel']])
        state['channel'].settimeout(5)
        return parent, state['channel']

    def ask(self, pair, request=b'?'):
        parent, child = pair
        child.sendall(request)
        self.service.snapshot_request(parent, self.worktree)
        data = b''
        while not data.endswith(b'\n'):
            chunk = child.recv(4096)
            assert chunk
            data += chunk
        return data.rstrip(b'\n')

    def close_gate(self):
        gate, self.gate = self.gate, None
        try:
            os.close(gate[0])
        finally:
            os.close(gate[1])

    def close(self):
        errors = []
        for worktree in self.service.worktrees.values():
            if worktree.helper is not None:
                pid, _ = worktree.helper
                try:
                    try:
                        os.kill(pid, signal.SIGKILL)  # Owned, unreaped direct fork child.
                    except ProcessLookupError:
                        pass
                    deadline = time.monotonic() + 5
                    while os.waitpid(pid, os.WNOHANG)[0] == 0:
                        if time.monotonic() >= deadline:
                            raise TimeoutError('owned helper did not settle')
                        time.sleep(0.01)  # Bounded kernel-settlement poll, not a behavior barrier.
                except (OSError, TimeoutError) as exc:
                    errors.append(str(exc))
        for child in self.children:
            try:
                try:
                    if child.poll() is None:
                        child.kill()
                finally:
                    child.wait(timeout=5)
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(str(exc))
        streams = [stream for child in self.children for stream in (child.stdin, child.stdout)
                   if stream is not None]
        streams += self.channels + [key.fileobj for key in self.service.selector.get_map().values()]
        for stream in streams:
            try:
                stream.close()
            except OSError as exc:
                errors.append(str(exc))
        if self.gate:
            for fd in self.gate:
                try:
                    os.close(fd)
                except OSError as exc:
                    errors.append(str(exc))
        self.service.selector.close()
        assert not errors, errors


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    case = Recovery(tmp_path / 'repo', monkeypatch)
    try:
        yield case
    finally:
        case.close()


def test_cookie_loss_recovers_same_worktree_and_existing_channel(recovery):
    c = recovery
    assert c.request() == {'event': 'snapshot', 'tree': None}
    c.ready()
    original = c.worktree
    pair = c.channel()
    initial = c.snapshot()
    assert c.git('show', initial + ':app.py') == 'VALUE = 1'
    token = original.changes('')['token']
    failed_child = original.process
    c.cookies = False
    assert c.ask(pair) == b'-'  # Real sync waits its unchanged cookie deadline and stops.
    assert failed_child.poll() is not None
    assert original.process is None and original.tree is None
    c.source.write_text('VALUE = 2\n')  # A real unobserved gap invalidates old change tokens.
    c.later()
    assert c.ask(pair) == b'-'  # Restarting request remains unknown.
    assert c.worktree is original and original.epoch != token.split(':')[0]
    c.ready()
    assert c.ask(pair) == b'-'  # R alone is insufficient.
    assert c.snapshot() is None  # Snapshot bytes without cookie proof are insufficient too.
    c.cookies = True
    c.later()
    assert c.request()['tree'] is None
    c.ready()
    recovered = c.snapshot()
    assert c.git('show', recovered + ':app.py') == 'VALUE = 2'
    assert c.ask(pair) == recovered.encode()
    assert c.ask(pair, b'c' + token.encode() + b'\n') == b'-'
    fresh = json.loads(c.ask(pair, b'c\n'))
    c.source.write_text('VALUE = 3\n')
    c.send(original, 'E', str(c.source))
    c.until(lambda: original.tree is None)
    assert c.ask(pair) == b'-'
    delta = json.loads(c.ask(pair, b'c' + fresh['token'].encode() + b'\n'))
    assert delta['paths'] == [str(c.source)]
    assert delta['token'] != fresh['token']


def test_old_helper_settles_before_recovery_and_cannot_publish(recovery):
    c = recovery
    c.request();c.ready()
    original = c.worktree
    c.gate = os.pipe()
    c.service.background()
    helper = original.helper
    assert helper is not None
    original.stop()
    c.later()
    assert c.request()['tree'] is None
    assert original.helper == helper and original.process is None
    os.write(c.gate[1], b'!')
    c.until(lambda: original.helper is None)
    c.close_gate()
    assert original.tree is None
    with pytest.raises(ChildProcessError):
        os.waitpid(helper[0], os.WNOHANG)
    assert c.request()['tree'] is None
    c.ready()
    assert c.git('show', c.snapshot() + ':app.py') == 'VALUE = 1'


def test_memory_request_recovers_without_replacing_the_worktree(recovery):
    c = recovery
    c.request();c.ready()
    original = c.worktree
    assert original.changes('') is not None
    original.stop();c.later()
    pair = c.channel()  # memory is the request which starts recovery.
    assert c.worktree is original and c.ask(pair) == b'-'
    c.ready()
    assert c.ask(pair) == b'-'
    tree = c.snapshot()
    assert c.git('show', tree + ':app.py') == 'VALUE = 1'
    assert c.ask(pair) == tree.encode()


@pytest.mark.parametrize('gate', ['stopping', 'pin-not-ready', 'pin-monitor-gone', 'pin-monitor-loss'])
def test_ineligible_service_never_resurrects_worktree(recovery, gate):
    c = recovery
    c.request();c.ready()
    pair = c.channel()
    c.worktree.stop();epoch = c.worktree.epoch
    c.later()
    if gate == 'stopping':c.service.stopping = True
    elif gate == 'pin-not-ready':c.service.pin_ready = False
    elif gate == 'pin-monitor-gone':c.service.pin_watch = None
    else:
        left, right = socket.socketpair();c.channels.extend([left, right])
        c.service.pin_watch = SimpleNamespace(stdout=left)
        c.service.pin_monitor_failed('controlled monitor EOF')
        assert c.service.stopping and not c.service.pin_ready
    assert c.request()['tree'] is None
    assert c.ask(pair) == b'-'
    state, parent = c.service.memory({'repository': str(c.root)})
    c.channels.extend([parent, state['channel']])
    assert state['worktree'] is c.worktree
    assert c.worktree.process is None and c.worktree.epoch == epoch


@pytest.mark.parametrize('failure', ['spawn', 'registration', 'backend'])
def test_unavailable_replacement_is_throttled_and_owned(recovery, monkeypatch, failure):
    c = recovery
    c.request();c.ready();c.worktree.stop();c.later()
    old_count = len(c.children)
    old_watchers = len(c.watchers)
    assert old_watchers == 1
    attempts = []
    if failure == 'spawn':
        def denied(*args, **kwargs):
            attempts.append(args)
            raise OSError('controlled launch failure')
        monkeypatch.setattr(R.subprocess, 'Popen', denied)
    elif failure == 'registration':
        register = c.service.selector.register
        def failed_registration(file, events, data=None):
            register(file, events, data)
            raise OSError('controlled registration failure after ownership transfer')
        monkeypatch.setattr(c.service.selector, 'register', failed_registration)
    else:
        c.service.captured['bytes']['installer/runtime_service_watch.py'] = b"import os;os.write(1,b'Xunavailable\\0')"
    assert c.request()['tree'] is None
    if failure == 'backend':
        c.until(lambda: c.worktree.process is None)
    for _ in range(4):
        assert c.request()['tree'] is None
    assert len(c.children) == old_count + (failure != 'spawn')
    if failure == 'spawn':assert len(attempts) == 1
    assert c.worktree.process is None and c.worktree.tree is None
    assert all(child.poll() is not None for child in c.children)
    assert len(c.watchers) == old_watchers + (failure != 'spawn')
    assert all(child.stdin is not None and child.stdout is not None
               and child.stdin.closed and child.stdout.closed for child in c.watchers)
    assert not c.service.selector.get_map()


def test_uncertain_settlement_retains_original_handle_and_refuses_proof(recovery, monkeypatch):
    c = recovery
    c.request();c.ready()
    child = c.worktree.process
    with monkeypatch.context() as patch:
        def denied():raise PermissionError('controlled settlement denial')
        def unproven(*args, **kwargs):raise subprocess.TimeoutExpired('owned watcher', 2)
        patch.setattr(child, 'kill', denied)
        patch.setattr(child, 'wait', unproven)
        assert not c.worktree.stop()
        assert c.worktree.process is child and child.poll() is None
        assert not c.worktree.sync()
        c.later()
        assert c.request()['tree'] is None
        assert c.worktree.process is child
    c.later()
    assert c.request()['tree'] is None
    assert child.poll() is not None and c.worktree.process is not child
    c.ready()
    assert c.git('show', c.snapshot() + ':app.py') == 'VALUE = 1'


def test_missing_root_retains_unsettled_watcher_until_cleanup_succeeds(recovery, monkeypatch):
    c = recovery
    c.request();c.ready()
    original = c.worktree;child = original.process
    moved = c.root.with_name('temporarily-moved-repo')
    c.root.rename(moved)
    try:
        with monkeypatch.context() as patch:
            def denied():raise PermissionError('controlled owned-child settlement denial')
            patch.setattr(child, 'kill', denied)
            c.service.background()
            assert c.service.worktrees[str(c.root)] is original
            assert original.process is child and child.poll() is None
            assert c.request()['tree'] is None
        c.service.background()
        assert child.poll() is not None
        assert str(c.root) not in c.service.worktrees
    finally:
        moved.rename(c.root)


def test_shutdown_reports_unsettled_watcher_after_attempting_remaining_cleanup(recovery, monkeypatch):
    c = recovery
    c.request();c.ready()
    original = c.worktree;child = original.process
    c.git('add', '-A')
    c.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'private fixture')
    second_root = c.root.with_name('second-worktree')
    c.git('worktree', 'add', '--detach', str(second_root), 'HEAD')
    assert c.service.snapshot_answer({'repository': str(second_root)})['tree'] is None
    second = c.service.worktrees[str(second_root)]
    c.until(lambda: second.ready)
    second_child = second.process
    c.service.pin_watch = None
    left, right = socket.socketpair();c.channels.extend([left, right])
    c.service.subscribers = {left: []}
    c.service.directory = c.root.parent / 'service';c.service.directory.mkdir()
    for name in ('s', 'token'):(c.service.directory / name).write_text('private fixture')
    c.service.served = 0
    with monkeypatch.context() as patch:
        def denied():raise PermissionError('controlled owned-child settlement denial')
        patch.setattr(child, 'kill', denied)
        with pytest.raises(RuntimeError, match='unsettled worktree watcher'):
            c.service.shutdown()
        assert c.service.worktrees[str(c.root)] is original
        assert original.process is child and child.poll() is None
        assert c.service.stopping
        assert second_child.poll() is not None
        assert left.fileno() == -1 and not c.service.subscribers
        assert not any((c.service.directory / name).exists() for name in ('s', 'token'))
    c.service.shutdown()
    assert child.poll() is not None and original.process is None


def test_ordinary_edit_during_helper_recomputes_without_failure_backoff(recovery, monkeypatch):
    c = recovery
    monkeypatch.setattr(R, 'SETTLE', 0.3)
    c.request();c.ready()
    c.now += R.SETTLE
    c.gate = os.pipe()
    c.service.background()
    old_helper = c.worktree.helper
    generation = c.worktree.generation
    assert old_helper is not None
    c.source.write_text('VALUE = 2\n')
    c.send(c.worktree, 'E', str(c.source))
    c.until(lambda: c.worktree.generation != generation)
    os.write(c.gate[1], b'!')
    c.until(lambda: c.worktree.helper is None)
    c.close_gate()
    assert c.worktree.tree is None
    # Advance only the ordinary settle interval, not the failed-helper backoff.
    c.now += R.SETTLE
    tree = c.snapshot()
    assert tree is not None
    assert c.git('show', tree + ':app.py') == 'VALUE = 2'
