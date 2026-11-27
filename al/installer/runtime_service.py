"""Resident per-repository AL service (#27), running inside one verified pinned runtime.

`runtime_service_boot.SERVICE` verifies the pin exactly as the one-shot run child does,
installs the closed loader over the verified bytes and calls `serve()`. The process then
serves one repository (Git common directory) for one pinned runtime and one launcher
installation, so runs pinned to different runtimes never share a process.

Every command runs in a child forked from the warm service. The child imports every AL
module afresh from the verified bytes (only their compiled code objects are reused), so
module state is exactly that of a one-shot run; it re-reads and re-validates the run's
protected binding and refuses to run anything whose binding names a different pin. Any
condition the service cannot vouch for is answered with "fallback" before the command
starts, and the launcher then takes its ordinary in-process path. Once started, the
child runs the same tail of the one-shot program text, so output, tracebacks and exit
status are those of the one-shot run.

The service records nothing itself. In the background it only keeps working-copy
snapshot trees and fact-store pack headers current (see al_service_cache). It holds an
exclusive lock while alive; its socket, token and log are recreated at start, so a
crashed service leaves nothing a successor has to repair."""

import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import select
import selectors
import signal
import socket
import stat
import subprocess
import sys
import time
import traceback
import types

from .runtime_service_client import PROTOCOL, namespace

WIRE = namespace()
TAIL_MARKER = 'import al_runtime_resources'
COOKIE_TIMEOUT = 2.0
# Completion-spaced fallback; native pin events still trigger immediate full verification.
PIN_VERIFY_INTERVAL = 5.0
WATCH_RETRY = 1.0
SETTLE = 0.3
MAX_WORKTREES = 16
MAX_PIN_RECORD = 1 << 16
# Changed paths a worktree keeps for change-set answers (#35); older ones are forgotten (lost).
MAX_EVENTS = 20000
# A change-set answer longer than this is no answer: the command lists the repository itself.
MAX_CHANGES = 4096
MAX_SUBSCRIBERS = 16
MAX_SUBSCRIBED_PATHS = 8
SUBSCRIBED_TICK = 0.1


def log(message):
    try:
        sys.stderr.write(time.strftime('%Y-%m-%dT%H:%M:%SZ ', time.gmtime()) + message + '\n')
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def _git(root, *args):
    result = subprocess.run(['git', '--no-optional-locks', *args], cwd=root, capture_output=True, timeout=10)
    if result.returncode:
        raise OSError(result.stderr.decode(errors='replace').strip() or 'git failed')
    return result.stdout.decode().splitlines()


def _stdlib_imports(codes):
    """Absolute module-level imports of the given module code objects, other than AL's own."""
    import dis
    names = set()
    for code in codes:
        previous = []
        for instruction in dis.get_instructions(code):
            if instruction.opname == 'IMPORT_NAME' and len(previous) >= 2 and previous[-2].argval == 0:
                name = instruction.argval
                if name and not name.startswith(('al_', 'installer', '_al_')):
                    names.add(name)
            previous = (previous + [instruction])[-2:]
    return names


def _asker(channel, root):
    """In a command child: (current, changes) over the child's channel to the service.

    `current(path)` is the service's proven-current snapshot tree of `root`, or None.
    `changes(path, since)` is the service's change set of `root` (#35): `{'token': T, 'paths':
    [...]}` with every path an event named since the token `since` (T alone for an empty
    `since`), proven complete by a cookie, or None when that is unknown."""
    import threading
    lock = threading.Lock()
    pending = [b'']

    def ask(request):
        """One request, one answer line (answers arrive in request order)."""
        with lock:
            try:
                channel.sendall(request)
                data = pending[0]
                while b'\n' not in data:
                    chunk = channel.recv(65536)
                    if not chunk:
                        return None
                    data += chunk
            except OSError:
                return None
            line, pending[0] = data.split(b'\n', 1)
        return line.decode(errors='replace')

    def current(path):
        if os.path.realpath(path) != root:
            return None
        tree = (ask(b'?') or '').strip()
        return tree if len(tree) in (40, 64) else None

    def changes(path, since):
        if os.path.realpath(path) != root or not isinstance(since, str) or '\n' in since or len(since) > 128:
            return None
        answer = ask(b'c' + since.encode() + b'\n')
        if not answer or answer == '-':
            return None
        try:
            value = json.loads(answer)
        except ValueError:
            return None
        if (not isinstance(value, dict) or not isinstance(value.get('token'), str)
                or not isinstance(value.get('paths'), list) or not all(isinstance(p, str) for p in value['paths'])):
            return None
        return value

    return current, changes


def tail_program(child_text):
    """The one-shot program with every line before the command tail blanked (same line numbers)."""
    lines = child_text.split('\n')
    if TAIL_MARKER not in lines:
        raise ValueError('run child program has no command tail')
    start = lines.index(TAIL_MARKER)
    return '\n'.join([''] * start + lines[start:])


class Worktree:
    """Watcher-backed snapshot state of one worktree."""

    def __init__(self, service, root, git_dir, common_dir):
        self.service, self.root, self.git_dir, self.common_dir = service, root, git_dir, common_dir
        self.git_dirs = sorted({git_dir, common_dir}, key=len, reverse=True)
        self.process = None
        self.buffer = b''
        self.backend = None
        self.ready = False
        self.generation = 0
        self.tree = None
        self.tree_generation = None
        self.last_event = 0.0
        self.cookies = {}
        self.counter = 0
        self.helper = None
        self.watch_failed = False
        self.retry_after = 0.0
        # Change set (#35): every relevant event is numbered; `lost` is the latest number at or
        # before which events may be missing (before the watcher was ready, a lost-events
        # notice, or forgotten old events). A token is `<epoch>:<number>`.
        self.epoch = os.urandom(8).hex()
        self.sequence = 0
        self.lost = 0
        self.events = []

    def invalidate(self):
        self.ready = False
        self.tree = self.tree_generation = None
        self.generation += 1
        self.buffer = b''
        self.cookies.clear()

    def start(self, watch_source):
        import al_service_cache
        if self.process is not None or self.helper is not None:
            return False
        self.invalidate()
        self.epoch = os.urandom(8).hex()
        self.sequence = self.lost = 0
        self.events.clear()
        self.last_event = time.monotonic()
        self.retry_after = self.last_event + WATCH_RETRY
        self.watch_failed = True
        request = {'deep': [self.root], 'shallow': [d for d in self.git_dirs
                   if sys.platform.startswith('linux') or not d.startswith(self.root + '/')],
                   'skip': list(al_service_cache.EXCLUDED)}
        try:
            self.process = subprocess.Popen(
                [sys.executable, '-I', '-S', '-B', '-c', watch_source, json.dumps(request)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True)
            os.set_blocking(self.process.stdout.fileno(), False)
            self.service.selector.register(self.process.stdout, selectors.EVENT_READ, ('watch', self))
        except (OSError, ValueError, KeyError) as exc:
            log('worktree watcher launch failed for ' + self.root + ': ' + str(exc))
            self.stop()
            return False
        self.watch_failed = False
        return True

    def stop(self):
        self.watch_failed = True
        self.invalidate()
        self.retry_after = max(self.retry_after, time.monotonic() + WATCH_RETRY)
        if self.process is None:
            return True
        self.service.forget(self.process.stdout)
        try:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=2)
            try:
                self.process.stdout.close()
            finally:
                self.process.stdin.close()
        except (OSError, subprocess.SubprocessError) as exc:
            log('worktree watcher settlement uncertain for ' + self.root + ': ' + str(exc))
            return False  # Retain ownership; a later request may try settlement again.
        self.process = None
        return True

    def readable(self):
        try:
            data = os.read(self.process.stdout.fileno(), 1 << 16)
        except BlockingIOError:
            return
        except OSError:
            data = b''
        if not data:
            log('watcher ended for ' + self.root)
            self.stop()
            return
        self.buffer += data
        *records, self.buffer = self.buffer.split(b'\0')
        import al_service_cache
        for record in records:
            kind, path = record[:1], os.fsdecode(record[1:])
            if kind == b'R':
                self.ready, self.backend = True, path
                self.generation += 1
                self.sequence += 1
                self.lost = self.sequence
            elif kind == b'X':
                log('watcher unavailable for ' + self.root + ': ' + path)
                self.backend = 'none'
                self.stop()
                return
            elif kind == b'O':
                self.changed()
                self.sequence += 1
                self.lost = self.sequence
            elif kind == b'E':
                name = os.path.basename(path)
                if name.startswith(al_service_cache.COOKIE) and os.path.dirname(path) == self.git_dir:
                    self.cookies.pop(name, None)
                elif al_service_cache.relevant(path, self.root, self.git_dirs):
                    self.changed()
                    self.note(path)

    def note(self, path):
        """Number one relevant event for change-set answers; forget the oldest beyond the bound."""
        self.sequence += 1
        self.events.append((self.sequence, path))
        if len(self.events) > MAX_EVENTS:
            keep = MAX_EVENTS // 2
            self.lost = max(self.lost, self.events[-keep - 1][0])
            self.events = self.events[-keep:]

    def changes(self, since):
        """Every relevant path an event named after the token `since`, proven complete now
        (a cookie came back through the watcher), with the current token; None when unknown
        (another epoch, a lost-events notice or forgotten events since, no watcher)."""
        if not self.sync():
            return None
        now = self.epoch + ':' + str(self.sequence)
        if not since:
            return {'token': now, 'paths': []}
        epoch, _, number = since.partition(':')
        try:
            number = int(number)
        except ValueError:
            return None
        if epoch != self.epoch or number < self.lost or number > self.sequence:
            return None
        paths = sorted({p for n, p in self.events if n > number})
        if len(paths) > MAX_CHANGES:
            return None
        return {'token': now, 'paths': paths}

    def changed(self):
        self.generation += 1
        self.tree = None
        self.last_event = time.monotonic()

    def sync(self):
        """Wait until every event before now has been delivered; False when that is unknown."""
        if self.process is None or self.watch_failed or not self.ready:
            return False
        import al_service_cache
        self.counter += 1
        name = al_service_cache.COOKIE + str(os.getpid()) + '.' + str(self.counter)
        path = os.path.join(self.git_dir, name)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        except OSError:
            return False
        self.cookies[name] = True
        deadline = time.monotonic() + COOKIE_TIMEOUT
        try:
            while name in self.cookies and self.process is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    log('watcher missed a cookie for ' + self.root)
                    # Latency is not a dead monitor: discard proof without restarting its stream.
                    self.tree = self.tree_generation = None
                    self.generation += 1
                    self.epoch = os.urandom(8).hex()
                    self.sequence = self.lost = 0
                    self.events.clear()
                    self.last_event = time.monotonic()
                    return False
                ready, _, _ = select.select([self.process.stdout.fileno()], [], [], left)
                if ready:
                    self.readable()
        finally:
            self.cookies.pop(name, None)
            try:
                os.unlink(path)
            except OSError:
                pass
        return self.process is not None and not self.watch_failed and self.ready

    def current(self):
        """The snapshot tree proven current now, or None."""
        if self.tree is None or not self.sync():
            return None
        return self.tree if self.tree_generation == self.generation else None


class Service:
    def __init__(self, request, manifest, captured, loader, child_text):
        if request.get('protocol') != PROTOCOL:
            raise ValueError('unsupported AL service protocol')
        self.request, self.manifest, self.captured, self.loader = request, manifest, captured, loader
        self.child_text = child_text
        self.tail = compile(tail_program(child_text), '<string>', 'exec')
        self.directory = Path(request['service_directory'])
        self.base = Path(request['storage'])
        self.pin = request['pin']
        self.platform = request['platform']
        self.prefix = self.platform + '/skills/al/scripts/'
        self.payload = self.base / 'pins' / self.pin['pin_identity'] / 'payload'
        self.selector = selectors.DefaultSelector()
        self.children = {}
        self.channels = {}
        self.pending = {}
        self.worktrees = {}
        self.locations = {}
        self.packs = {}
        self.pack_directories = set()
        self.served = 0
        self.started = time.time()
        self.last_active = time.monotonic()
        self.idle = float(request.get('idle_seconds') or 1800)
        self.stopping = False
        self.environment = dict(os.environ)
        self.pin_watch = None
        self.pin_buffer = b''
        self.pin_ready = False
        self.manifest_identity = None
        self.pin_verify_deadline = None
        self.subscribers = {}

    # ------------------------------------------------------------------ lifecycle

    def acquire(self):
        state = os.lstat(self.directory)
        if (stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode) or state.st_uid != os.getuid()
                or stat.S_IMODE(state.st_mode) != 0o700):
            raise ValueError('AL service directory must be private and owned')
        self.lock = os.open(self.directory / 'lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def listen(self):
        token = secrets.token_hex(32)
        temporary = self.directory / ('.token.' + str(os.getpid()))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(token)
        self.token = token
        here = os.open('.', os.O_RDONLY)
        try:
            os.chdir(self.directory)
            try:
                os.unlink('s')
            except FileNotFoundError:
                pass
            self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.listener.bind('s')
            os.chmod('s', 0o600)
            self.listener.listen(64)
        finally:
            os.fchdir(here)
            os.close(here)
        os.replace(temporary, self.directory / 'token')
        self.listener.setblocking(False)
        self.selector.register(self.listener, selectors.EVENT_READ, ('accept', None))

    def precompile(self):
        """Compile every module once; children execute the cached code objects."""
        codes = {}
        for name, relative in self.loader.modules.items():
            raw = self.captured['bytes'][relative]
            filename = str(self.loader.root / relative)
            codes[name] = (filename, hashlib.sha256(raw).hexdigest(), compile(raw, filename, 'exec'))
        loader = self.loader

        def exec_module(module):
            filename, digest, code = codes[module.__name__]
            module.__file__ = filename
            loader.loaded[module.__name__] = {'origin': filename, 'sha256': digest}
            exec(code, module.__dict__)
            hook = getattr(loader, 'service_hook', None)
            if hook is not None:
                hook(module)

        loader.exec_module = exec_module
        # Standard-library modules the runtime imports at module level are imported once here:
        # they hold no repository, run or environment state, and children skip their import cost.
        for name in sorted(_stdlib_imports(code for _, _, code in codes.values())):
            try:
                __import__(name)
            except Exception:
                pass

    def watch_pin(self):
        source = self.captured['bytes']['installer/runtime_service_watch.py'].decode()
        root = str(self.base / 'pins' / self.pin['pin_identity'])
        request = {'deep': [os.path.realpath(root)], 'shallow': [], 'skip': []}
        self.pin_watch = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', source, json.dumps(request)],
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, close_fds=True)
        os.set_blocking(self.pin_watch.stdout.fileno(), False)
        self.selector.register(self.pin_watch.stdout, selectors.EVENT_READ, ('pin', None))

    def pin_manifest_identity(self):
        state = os.lstat(self.base / 'pins' / self.pin['pin_identity'] / 'manifest.json')
        return (state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns, state.st_mode)

    def verify_pin(self, reason):
        from installer.runtime_pin import verify_pin
        try:
            before = self.pin_manifest_identity()
            verified = verify_pin(self.base / 'pins', self.pin, readonly=True)
            if verified['bytes'] != self.captured['bytes']:
                raise ValueError('runtime pin bytes differ from the verified service runtime')
            if self.pin_manifest_identity() != before:
                raise ValueError('runtime pin manifest moved during verification')
            self.manifest_identity = before
            self.pin_verify_deadline = time.monotonic() + PIN_VERIFY_INTERVAL
        except (OSError, ValueError, KeyError) as exc:
            log('pinned runtime failed verification after ' + reason + ': ' + str(exc) + '; stopping')
            self.stopping = True
            self.pin_ready = False
            return False
        return True

    def audit_pin_if_due(self):
        if (self.stopping or not self.pin_ready or self.pin_watch is None or self.pin_buffer):
            return False
        deadline = getattr(self, 'pin_verify_deadline', None)
        if deadline is not None and time.monotonic() < deadline:
            return True
        return self.verify_pin('its scheduled audit')

    def pin_monitor_failed(self, reason):
        self.pin_ready = False
        self.stopping = True
        self.forget(self.pin_watch.stdout)
        log('pin watcher ' + reason + '; stopping')
        # Keep the child handle until shutdown proves bounded settlement.

    def pin_event(self):
        try:
            data = os.read(self.pin_watch.stdout.fileno(), 1 << 16)
        except BlockingIOError:
            return
        except OSError:
            data = b''
        if not data:
            self.pin_monitor_failed('ended')
            return
        self.pin_buffer += data
        *records, self.pin_buffer = self.pin_buffer.split(b'\0')
        if len(self.pin_buffer) > MAX_PIN_RECORD or any(len(row) > MAX_PIN_RECORD for row in records):
            self.pin_monitor_failed('record exceeded its limit')
            return
        ready = changed = False
        for record in records:
            kind = record[:1]
            if kind == b'R' and len(record) > 1:
                ready = True
            elif kind in (b'E', b'O'):
                changed = True
            else:
                self.pin_monitor_failed('unavailable or malformed: ' + os.fsdecode(record))
                return
        if ready or changed:
            reason = 'watcher readiness' if ready else 'a change in its pin directory'
            if self.verify_pin(reason) and ready and not self.stopping:
                self.pin_ready = True

    def stop_pin_watch(self):
        self.pin_ready = False
        process = self.pin_watch
        if process is None:
            return
        self.forget(process.stdout)
        try:
            process.kill()
            process.wait(timeout=2)
        finally:
            process.stdout.close()
            process.stdin.close()
        self.pin_watch = None

    def shutdown(self):
        self.stopping = True
        errors = []
        for worktree in self.worktrees.values():
            try:
                if not worktree.stop():
                    errors.append('unsettled worktree watcher: ' + worktree.root)
            except Exception as exc:
                errors.append('worktree cleanup: ' + str(exc))
        try:
            self.stop_pin_watch()
        except Exception as exc:
            errors.append('pin watcher cleanup: ' + str(exc))
        for conn in list(self.subscribers):
            try:
                conn.close()  # Subscribers see the end and fall back to polling.
                del self.subscribers[conn]
            except Exception as exc:
                errors.append('subscriber cleanup: ' + str(exc))
        for path in (self.directory / 's', self.directory / 'token'):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                errors.append('service path cleanup: ' + str(exc))
        if errors:
            raise RuntimeError('service shutdown incomplete: ' + '; '.join(errors))
        log('stopped after serving ' + str(self.served) + ' commands')

    # ------------------------------------------------------------------ loop

    def run(self):
        wake_read, wake_write = os.pipe()
        os.set_blocking(wake_read, False)
        os.set_blocking(wake_write, False)
        signal.set_wakeup_fd(wake_write)
        signal.signal(signal.SIGCHLD, lambda *_: None)
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, 'stopping', True))
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        self.wake = (wake_read, wake_write)
        self.selector.register(wake_read, selectors.EVENT_READ, ('wake', None))
        log('serving ' + self.request['common_dir'] + ' with pin ' + self.pin['pin_identity'])
        while True:
            self.reap()
            self.audit_pin_if_due()
            if self.stopping and not self.children:
                break
            if self.stopping:
                self.close_listener()
            self.notify()
            if self.subscribers:
                self.last_active = time.monotonic()  # a subscribed supervisor keeps the service
            if not self.children and time.monotonic() - self.last_active > self.idle:
                log('idle timeout')
                break
            self.background()
            for key, _ in self.selector.select(timeout=SUBSCRIBED_TICK if self.subscribers else 0.5):
                kind, value = key.data
                if kind == 'accept':
                    self.accept()
                elif kind == 'wake':
                    try:
                        while os.read(wake_read, 4096):
                            pass
                    except BlockingIOError:
                        pass
                elif kind == 'watch':
                    if value.process is not None:
                        value.readable()
                elif kind == 'pin':
                    self.pin_event()
                elif kind == 'connection':
                    self.connection(key.fileobj, value)
                elif kind == 'helper':
                    self.helper_done(key.fileobj, value)
                elif kind == 'snapshot':
                    self.snapshot_request(key.fileobj, value)
                elif kind == 'subscriber':
                    self.unsubscribe(key.fileobj)
        self.shutdown()

    def forget(self, fileobj):
        try:
            self.selector.unregister(fileobj)
        except (KeyError, ValueError):
            pass

    def close_listener(self):
        if getattr(self, 'listener', None) is not None:
            self.forget(self.listener)
            self.listener.close()
            self.listener = None
            try:
                os.unlink(self.directory / 's')
            except OSError:
                pass

    def accept(self):
        try:
            conn, _ = self.listener.accept()
        except (BlockingIOError, OSError):
            return
        try:
            conn.setblocking(True)
            conn.settimeout(5)
            if WIRE['_al_service_peer_uid'](conn) != os.getuid():
                raise ValueError('foreign peer')
            message, fds = WIRE['_al_service_receive'](conn, fds=3)
        except (OSError, ValueError, EOFError):
            conn.close()
            return
        try:
            self.dispatch(conn, message, fds)
        except Exception as exc:  # a malformed request never ends the service
            log('request failed: ' + repr(exc))
            for descriptor in fds:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            try:
                WIRE['_al_service_send'](conn, {'event': 'fallback', 'reason': 'service error'})
            except OSError:
                pass
            conn.close()

    def reply(self, conn, value):
        try:
            WIRE['_al_service_send'](conn, value)
        except OSError:
            pass
        conn.close()

    def dispatch(self, conn, message, fds):
        if (not isinstance(message, dict) or message.get('protocol') != PROTOCOL
                or not secrets.compare_digest(str(message.get('token', '')), self.token)):
            for descriptor in fds:
                os.close(descriptor)
            return self.reply(conn, {'event': 'fallback', 'reason': 'unauthenticated request'})
        operation = message.get('op')
        if operation == 'status':
            return self.reply(conn, self.status())
        if operation == 'stop':
            self.stopping = True
            return self.reply(conn, {'event': 'stopping', 'pid': os.getpid()})
        if operation in ('snapshot', 'subscribe'):
            for descriptor in fds:
                os.close(descriptor)
            if operation == 'snapshot':
                return self.reply(conn, self.snapshot_answer(message))
            return self.subscribe(conn, message)
        if operation == 'run' and isinstance(message.get('env'), dict) and 'HARNESS_CHECK_CONTEXT' in message['env']:
            # A Harness-contained command keeps its own sandbox: it never runs in a child of
            # the (uncontained) service. It may still ask for snapshots and subscribe (#31).
            for descriptor in fds:
                os.close(descriptor)
            return self.reply(conn, {'event': 'fallback', 'reason': 'contained Harness command'})
        if operation != 'run' or len(fds) != 3:
            for descriptor in fds:
                os.close(descriptor)
            return self.reply(conn, {'event': 'fallback', 'reason': 'unsupported request'})
        if self.stopping or message.get('launcher') != self.request['launcher_identity']:
            for descriptor in fds:
                os.close(descriptor)
            return self.reply(conn, {'event': 'fallback', 'reason': 'service of another launcher'})
        if not self.pin_ready:
            for descriptor in fds:
                os.close(descriptor)
            return self.reply(conn, {'event': 'fallback', 'reason': 'pin watcher initializing'})
        if not self.pin_unchanged():
            for descriptor in fds:
                os.close(descriptor)
            return self.reply(conn, {'event': 'fallback', 'reason': 'pinned runtime changed'})
        self.last_active = time.monotonic()
        state, channel = self.memory(message)
        conn.settimeout(None)
        pid = os.fork()
        if pid == 0:
            if channel is not None:
                channel.close()
            self.child(conn, message, fds, state)
        for descriptor in fds:
            os.close(descriptor)
        if channel is not None:
            state['channel'].close()
            self.selector.register(channel, selectors.EVENT_READ, ('snapshot', state['worktree']))
            self.channels[pid] = channel
        self.served += 1
        self.children[pid] = conn
        self.selector.register(conn, selectors.EVENT_READ, ('connection', pid))

    def pin_unchanged(self):
        if not self.pin_ready or self.stopping or self.pin_watch is None:
            return False
        # Consume already-delivered changes/loss before authorizing another command.
        self.pin_event()
        if not self.pin_ready or self.stopping or self.pin_buffer:
            return False
        try:
            current = self.pin_manifest_identity()
        except OSError:
            return self.verify_pin('its manifest disappeared')
        if current != self.manifest_identity:
            return self.verify_pin('its manifest changed')
        return self.audit_pin_if_due()

    def connection(self, conn, pid):
        """A forwarded signal from the client, or its disconnection."""
        try:
            message = WIRE['_al_service_receive'](conn)
        except (OSError, ValueError, EOFError):
            message = None
        if pid not in self.children:
            return
        if message is None:
            self.forget(conn)
            self.kill(pid, signal.SIGHUP)
            return
        if message.get('op') == 'signal' and message.get('signal') in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            self.kill(pid, message['signal'])

    @staticmethod
    def kill(pid, number):
        try:
            os.kill(pid, number)
        except ProcessLookupError:
            pass

    def reap(self):
        while self.children:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                return
            channel = self.channels.pop(pid, None)
            if channel is not None:
                self.forget(channel)
                self.pending.pop(channel, None)
                channel.close()
            conn = self.children.pop(pid, None)
            if conn is None:
                continue
            self.forget(conn)
            self.reply(conn, {'event': 'exit', 'status': os.waitstatus_to_exitcode(status)})
            self.last_active = time.monotonic()
            self.refresh_packs()

    def status(self):
        return {
            'event': 'status', 'pid': os.getpid(), 'protocol': PROTOCOL,
            'common_dir': self.request['common_dir'], 'platform': self.platform,
            'pin_identity': self.pin['pin_identity'], 'launcher_identity': self.request['launcher_identity'],
            'started': self.started, 'uptime_seconds': round(time.time() - self.started, 3),
            'idle_seconds': round(time.monotonic() - self.last_active, 3), 'idle_timeout_seconds': self.idle,
            'pin_ready': self.pin_ready and not self.stopping,
            'commands_served': self.served, 'running_commands': len(self.children), 'stopping': self.stopping,
            'worktrees': [{'root': w.root, 'watcher': w.backend if w.process is not None else 'none',
                           'snapshot': w.tree if w.tree is not None and w.tree_generation == w.generation else None}
                          for w in self.worktrees.values()],
            'fact_store_packs': len(self.packs),
        }

    # ------------------------------------------------------------------ memory

    def location(self, repository):
        """(worktree root, git dir, common dir) of a repository path, cached."""
        repository = os.path.realpath(repository)
        if repository not in self.locations:
            try:
                top, git_dir, common = _git(repository, 'rev-parse', '--path-format=absolute', '--show-toplevel',
                                            '--absolute-git-dir', '--git-common-dir')
                self.locations[repository] = tuple(os.path.realpath(p) for p in (top, git_dir, common))
            except (OSError, ValueError, subprocess.SubprocessError):
                self.locations[repository] = None
        return self.locations[repository]

    def worktree_available(self, worktree):
        if getattr(self, 'stopping', False):
            return False
        if worktree.process is not None and not worktree.watch_failed:
            return True
        if (worktree.helper is not None or time.monotonic() < worktree.retry_after
                or not self.pin_ready or self.pin_watch is None
                or self.worktrees.get(worktree.root) is not worktree
                or worktree.common_dir != os.path.realpath(self.request['common_dir'])):
            return False
        if worktree.stop():
            worktree.start(self.captured['bytes']['installer/runtime_service_watch.py'].decode())
        return False

    def memory(self, message):
        """In-memory data handed to the command child (see al_service_cache), and the parent's
        end of the child's snapshot channel. The child asks over the channel only when it takes
        a snapshot, so commands that never snapshot pay nothing for the watcher."""
        import al_service_cache
        state = {'environment': al_service_cache.environment_key(self.environment), 'packs': self.packs}
        try:
            located = self.location(message['repository'])
        except (KeyError, TypeError):
            located = None
        if located is None or located[2] != os.path.realpath(self.request['common_dir']):
            return state, None
        root = located[0]
        worktree = self.worktrees.get(root)
        if worktree is None and len(self.worktrees) < MAX_WORKTREES:
            worktree = self.worktrees[root] = Worktree(self, *located)
        if worktree is not None:
            self.worktree_available(worktree)
        if not self.packs:
            self.refresh_packs(root)
        if worktree is None:
            return state, None
        parent, child = socket.socketpair()
        current, changes = _asker(child, root)
        state.update(root=root, worktree=worktree, channel=child, current=current, changes=changes)
        return state, parent

    # ------------------------------------------------------------------ subscriptions

    @staticmethod
    def _identity(path):
        try:
            state = os.lstat(path)
        except OSError:
            return None
        if not stat.S_ISREG(state.st_mode) or state.st_uid != os.getuid():
            return None
        return [state.st_ino, state.st_size, state.st_mtime_ns]

    def subscribe(self, conn, message):
        """`op: subscribe`: push `{'event': 'changed', 'path': P}` whenever one of the named files
        (owned regular files, such as an AL run's state) is replaced or rewritten. The service
        records and interprets nothing: the supervisor (Harness, #31) reads the file it named."""
        paths = message.get('paths')
        if (not isinstance(paths, list) or not 0 < len(paths) <= MAX_SUBSCRIBED_PATHS
                or len(self.subscribers) >= MAX_SUBSCRIBERS
                or any(not isinstance(p, str) or not os.path.isabs(p) or os.path.islink(p) for p in paths)):
            return self.reply(conn, {'event': 'fallback', 'reason': 'unsupported subscription'})
        watched = {p: self._identity(p) for p in paths}
        try:
            WIRE['_al_service_send'](conn, {'event': 'subscribed', 'paths': sorted(watched)})
        except OSError:
            conn.close()
            return None
        conn.setblocking(False)
        self.subscribers[conn] = watched
        self.selector.register(conn, selectors.EVENT_READ, ('subscriber', None))
        return None

    def unsubscribe(self, conn):
        try:
            if conn.recv(4096):
                return  # subscribers send nothing; stray bytes are ignored until they close
        except BlockingIOError:
            return
        except OSError:
            pass
        self.forget(conn)
        self.subscribers.pop(conn, None)
        conn.close()

    def notify(self):
        for conn, watched in list(self.subscribers.items()):
            for path, before in list(watched.items()):
                current = self._identity(path)
                if current == before:
                    continue
                watched[path] = current
                try:
                    conn.setblocking(True)
                    conn.settimeout(2)
                    WIRE['_al_service_send'](conn, {'event': 'changed', 'path': path, 'present': current is not None})
                    conn.setblocking(False)
                except OSError:
                    self.forget(conn)
                    self.subscribers.pop(conn, None)
                    conn.close()
                    break

    def snapshot_answer(self, message):
        """`op: snapshot`: the proven-current snapshot tree of a worktree of this repository, or
        None. A worktree seen for the first time starts its watcher and answers None."""
        try:
            located = self.location(str(message['repository']))
        except (KeyError, TypeError):
            located = None
        if located is None or located[2] != os.path.realpath(self.request['common_dir']):
            return {'event': 'snapshot', 'tree': None}
        self.last_active = time.monotonic()
        worktree = self.worktrees.get(located[0])
        if worktree is None and len(self.worktrees) < MAX_WORKTREES:
            worktree = self.worktrees[located[0]] = Worktree(self, *located)
        available = worktree is not None and self.worktree_available(worktree)
        return {'event': 'snapshot', 'tree': worktree.current() if available else None}

    def snapshot_request(self, channel, worktree):
        """A command child asks for the current snapshot tree of its worktree (`?`) or for its
        change set since a token (`c<token>\\n`, #35). Requests are answered in order, one line each."""
        try:
            data = channel.recv(4096)
        except OSError:
            data = b''
        if not data:
            self.forget(channel)
            self.pending.pop(channel, None)
            return
        buffer = self.pending.pop(channel, b'') + data
        while buffer:
            if buffer[:1] == b'?':
                buffer = buffer[1:]
                tree = worktree.current() if self.worktree_available(worktree) else None
                answer = (tree or '-').encode()
            elif buffer[:1] == b'c':
                if b'\n' not in buffer:
                    if len(buffer) > 256:
                        self.forget(channel)
                        return
                    break
                line, buffer = buffer.split(b'\n', 1)
                value = worktree.changes(line[1:].decode(errors='replace')) if self.worktree_available(worktree) else None
                answer = json.dumps(value, sort_keys=True, separators=(',', ':')).encode() if value is not None else b'-'
            else:
                self.forget(channel)
                return
            try:
                channel.sendall(answer + b'\n')
            except OSError:
                return
        if buffer:
            self.pending[channel] = buffer

    def refresh_packs(self, root=None):
        try:
            import al_analysis_index
            import al_service_cache
            if root is not None and al_analysis_index.enabled():
                common = os.path.realpath(self.request['common_dir'])
                self.pack_directories.add(str(al_analysis_index.index_root()
                                              / hashlib.sha256(common.encode()).hexdigest()[:32]))
            packs = {}
            for directory in self.pack_directories:
                packs.update(al_service_cache.pack_headers(al_analysis_index, directory, self.packs))
            self.packs = packs
        except (OSError, ValueError, ImportError):
            self.packs = {}

    def background(self):
        """After changes settle, recompute a worktree's snapshot in a forked helper."""
        if self.stopping:
            return
        now = time.monotonic()
        for root, worktree in list(self.worktrees.items()):
            if not os.path.isdir(root):
                if worktree.stop() and worktree.helper is None:
                    del self.worktrees[root]
                continue
            if (worktree.process is None or worktree.watch_failed or not worktree.ready or worktree.helper is not None
                    or (worktree.tree is not None and worktree.tree_generation == worktree.generation)
                    or now - worktree.last_event < SETTLE):
                continue
            generation = worktree.generation
            read, write = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(read)
                self.helper(worktree.root, write)
            os.close(write)
            worktree.helper = (pid, generation)
            reader = os.fdopen(read, 'rb')
            self.selector.register(reader, selectors.EVENT_READ, ('helper', worktree))

    def helper_done(self, reader, worktree):
        data = reader.read()
        self.forget(reader)
        reader.close()
        pid, generation = worktree.helper
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        worktree.helper = None
        tree = data.decode(errors='replace').strip()
        if (len(tree) not in (40, 64) or worktree.process is None
                or worktree.watch_failed):
            worktree.last_event = time.monotonic() + 30  # retry later, not in a tight loop
            return
        if generation != worktree.generation:
            return  # An ordinary edit raced this helper; recompute after normal settling.
        if worktree.sync() and worktree.generation == generation:
            worktree.tree, worktree.tree_generation = tree, generation

    def helper(self, root, write):
        code = 1
        try:
            self.isolate()
            os.chdir(root)
            self.fresh_modules()
            self.bind_resources()
            import al_snapshot
            os.write(write, al_snapshot.snapshot(root).encode())
            code = 0
        except BaseException:
            pass
        os._exit(code)

    # ------------------------------------------------------------------ command child

    def isolate(self):
        """In a forked child: drop the service's descriptors and handlers."""
        signal.set_wakeup_fd(-1)
        for number in (signal.SIGCHLD, signal.SIGTERM, signal.SIGHUP):
            signal.signal(number, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        for key in list(self.selector.get_map().values()):
            try:
                if isinstance(key.fileobj, int):
                    os.close(key.fileobj)
                else:
                    key.fileobj.close()
            except OSError:
                pass
        os.close(self.wake[1])
        os.close(self.lock)
        if self.pin_watch is not None:
            self.pin_watch.stdin.close()
        for worktree in self.worktrees.values():
            if worktree.process is not None:
                worktree.process.stdin.close()

    def fresh_modules(self, state=None):
        for name in list(sys.modules):
            if name.startswith('al_') or name == 'installer' or name.startswith('installer.') or name == '_al_run_pin_context':
                del sys.modules[name]
        self.loader.loaded.clear()
        if state is not None:
            from_cache = self.captured['bytes'].get(self.prefix + 'al_service_cache.py')
            cache = types.ModuleType('_al_service_cache')
            exec(compile(from_cache, str(self.payload / self.prefix / 'al_service_cache.py'), 'exec'), cache.__dict__)
            self.loader.service_hook = lambda module: cache.install(module, state)
        else:
            self.loader.service_hook = None

    def bind_resources(self):
        import al_runtime_resources
        resources, identities = {}, {}
        for logical, name in self.manifest['resource_map'].items():
            key = logical[len('scripts/'):] if logical.startswith('scripts/') else logical
            resources[key] = self.captured['bytes'][name]
            identities[key] = str(self.payload / name)
        al_runtime_resources._bind_verified_context({
            'identities': identities, 'bytes': resources, 'platform': self.platform,
            'closure_digest': self.manifest['closure_digest'], 'origin_digest': self.manifest['pin_identity']})

    def child(self, conn, message, fds, state):
        log_fd = None
        try:
            self.isolate()
            log_fd = os.dup(2)
            conn.settimeout(None)
            prepared = self.prepare(message, fds, state)
        except BaseException as exc:  # never run what could not be prepared
            prepared = 'service could not prepare the command: ' + repr(exc)
        if isinstance(prepared, str):
            if log_fd is not None:
                try:
                    os.write(log_fd, (time.strftime('%Y-%m-%dT%H:%M:%SZ ', time.gmtime()) + 'fallback for '
                                      + repr(message.get('argv'))[:200] + ': ' + prepared + '\n').encode())
                except OSError:
                    pass
            try:
                WIRE['_al_service_send'](conn, {'event': 'fallback', 'reason': prepared})
            except OSError:
                pass
            os._exit(0)
        try:
            WIRE['_al_service_send'](conn, {'event': 'started'})
        except OSError:
            os._exit(0)
        conn.close()
        os.close(log_fd)
        os._exit(self.execute(prepared))

    def prepare(self, message, fds, state):
        """Re-check what `alctl run` checks before delegating; a reason string means fall back."""
        argv, session, platform = message.get('argv'), message.get('session'), message.get('platform')
        repository, env, cwd = message.get('repository'), message.get('env'), message.get('cwd')
        if (not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv)
                or not isinstance(env, dict) or not isinstance(cwd, str) or not isinstance(repository, str)
                or not (session is None or isinstance(session, str)) or platform != self.platform):
            return 'malformed request'
        if argv[0].startswith('-') or argv[0] in {'start', 'clear'}:
            return 'operation not served'
        for token in argv[: argv.index('--') if '--' in argv else len(argv)]:
            if token.startswith('--') and '--session'.startswith(token.split('=')[0]):
                return 'session option in operation'
        if 'AL_RUNTIME_DIR' in env:
            return 'alternate runtime directory'
        if any(env.get(k) != self.environment.get(k) for k in ('LANG', 'LC_ALL', 'LC_CTYPE')):
            return 'locale differs from the service'
        for target, descriptor in enumerate(fds):
            os.dup2(descriptor, target, inheritable=True)
            if descriptor > 2:
                os.close(descriptor)
        os.umask(int(message.get('umask', 0o022)) & 0o777)
        os.chdir(cwd)
        os.environ.clear()
        os.environ.update(env)
        self.stdio()
        self.fresh_modules(state)
        run_context = types.ModuleType('_al_run_pin_context')
        run_context.transaction = None
        run_context.storage = str(self.base)
        run_context.readonly = False
        sys.modules['_al_run_pin_context'] = run_context
        from installer.runtime_pin_transaction import coordinated, read, read_state, subject, validate
        from installer.runtime_successor_store import require_ordinary_authority
        implicit = session is None
        if implicit:
            from installer.runtime_run import native_session
            session = native_session(repository, platform, argv)
        value = subject(repository, platform, session)
        require_ordinary_authority(value)
        native_root = os.environ.get('CLAUDE_PROJECT_DIR')
        if native_root and Path(native_root).resolve() != Path(value['repository_identity']['root']):
            return 'native project directory differs'
        if value['repository_identity']['common_dir'] != self.request['common_dir']:
            return 'repository of another service'
        with coordinated(value) as path:
            transaction = read(path) if path.exists() else None
        if transaction is None:
            return 'no protected run binding'
        validate(transaction, value)
        if transaction['status'] != 'ACTIVE':
            return 'run binding is not active'
        if transaction['pin'] != self.pin or transaction['platform'] != self.platform:
            # One service per pinned runtime: a run pinned elsewhere never executes here.
            return 'pinned runtime differs'
        run_context.transaction = transaction
        if read_state(transaction) is None:
            return 'run state missing'
        request = {'transaction': transaction, 'argv': list(argv), 'storage': str(self.base),
                   'existing_run': False, 'native_implicit': implicit}
        return {'request': request, 'transaction': transaction, 'value': value, 'run_context': run_context,
                'functions': {'coordinated': coordinated, 'read': read, 'read_state': read_state}}

    def stdio(self):
        """Standard streams as the interpreter would have created them on these descriptors."""
        encoding = sys.__stdout__.encoding if sys.__stdout__ is not None else 'utf-8'
        streams = []
        for fd, mode, name in ((0, 'rb', '<stdin>'), (1, 'wb', '<stdout>'), (2, 'wb', '<stderr>')):
            raw = io.FileIO(fd, mode, closefd=False)
            raw.name = name
            buffered = io.BufferedReader(raw) if fd == 0 else io.BufferedWriter(raw)
            interactive = os.isatty(fd)
            streams.append(io.TextIOWrapper(buffered, encoding=encoding,
                                            errors='backslashreplace' if fd == 2 else 'strict',
                                            line_buffering=fd == 2 or (fd == 1 and interactive)))
        sys.stdin, sys.stdout, sys.stderr = streams
        sys.__stdin__, sys.__stdout__, sys.__stderr__ = streams

    def execute(self, prepared):
        """Run the one-shot program's command tail; exit status as the interpreter computes it."""
        import atexit
        import linecache
        import threading
        from installer.runtime_pin_transaction import commit_run_binding
        from installer.runtime_retry_control import complete
        request = prepared['request']
        program = self.child_text.replace('request=REQUEST', 'request=' + repr(request), 1)
        if hasattr(linecache, '_register_code'):
            linecache._register_code(self.tail, program, '<string>')
        atexit._clear()
        namespace = {
            '__name__': '__main__', '__builtins__': __builtins__, 'sys': sys, 'os': os, 'json': json,
            'types': types, 'Path': Path, 'request': request, 'transaction': prepared['transaction'],
            'base': self.base, 'run_context': prepared['run_context'], 'manifest': self.manifest,
            'captured': self.captured['bytes'], 'payload': self.payload, 'platform': self.platform,
            'prefix': self.prefix, 'value': prepared['value'], 'commit_run_binding': commit_run_binding,
            'complete': complete,
            **prepared['functions'],
        }
        code = 0
        try:
            exec(self.tail, namespace)
        except SystemExit as exc:
            code = self.exit_code(exc)
        except BaseException as exc:
            sys.excepthook(type(exc), exc, exc.__traceback__.tb_next if exc.__traceback__ else None)
            code = 1
            if isinstance(exc, PermissionError):
                try:
                    sys.stderr.write(
                        'AL resident service permission failure: the service retains its launch access context; '
                        'elevating the launcher does not elevate an existing service. '
                        'Inspect possible partial effects and settle owned work before retrying. '
                        'AL_SERVICE=0 runs the same protected command in the invoking context, subject to normal '
                        'host permission review; alternatively stop and restart the owned service through '
                        'alctl service. No command was automatically replayed.\n')
                except (OSError, ValueError):
                    pass
            if isinstance(exc, KeyboardInterrupt):
                code = -signal.SIGINT
        try:
            threading._shutdown()
        except Exception:
            pass
        atexit._run_exitfuncs()
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except (OSError, ValueError):
                code = 120 if code == 0 else code
        if code < 0:
            signal.signal(-code, signal.SIG_DFL)
            os.kill(os.getpid(), -code)
        return code

    @staticmethod
    def exit_code(exc):
        value = exc.code
        if value is None:
            return 0
        if isinstance(value, int):
            return value & 0xFF
        try:
            sys.stderr.write(str(value) + '\n')
        except (OSError, ValueError):
            pass
        return 1


def serve(request, manifest, captured, loader, child_text):
    """Entry point from the verified service bootstrap; returns when the service ends."""
    service = Service(request, manifest, captured, loader, child_text)
    if not service.acquire():
        return 0
    try:
        service.precompile()
        service.listen()
        service.watch_pin()
    except BaseException:
        log('could not start: ' + traceback.format_exc())
        try:
            service.shutdown()
        except Exception:
            log('cleanup failed: ' + traceback.format_exc())
        raise
    try:
        service.run()
    except BaseException:
        log('failed: ' + traceback.format_exc())
        try:
            service.shutdown()
        except Exception:
            log('cleanup failed: ' + traceback.format_exc())
        raise
    return 0
