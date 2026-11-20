"""Thin client of the resident AL service (#27).

`CLIENT` is plain standard-library source. The managed launcher embeds it ahead of its own
self-verification, so a warm `alctl run` costs one interpreter start and one socket round
trip: it reads no runtime bytes and executes nothing but this text, which is part of the
launcher file itself. The service it reaches verified the pinned runtime when it started
and re-validates the run binding for every command; anything it cannot vouch for comes
back as "fallback" before the command starts, and the launcher then continues with its
ordinary in-process path, which produces the authoritative result or refusal.

The same text gives the service (through `namespace()`) its framing helpers, so client
and server share one implementation of the wire format.
"""

PROTOCOL = 1

CLIENT = r'''
def _al_service_frame(value):
    import json, struct
    data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return struct.pack('>I', len(data)) + data


def _al_service_send(sock, value, fds=()):
    import socket
    frame = _al_service_frame(value)
    sent = socket.send_fds(sock, [frame], list(fds)) if fds else 0
    sock.sendall(frame[sent:])


def _al_service_exact(sock, size):
    data = b''
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise EOFError('AL service connection closed')
        data += chunk
    return data


def _al_service_receive(sock, *, fds=0, limit=8 * 1024 * 1024):
    """One framed JSON message (and, when asked, the descriptors sent with its first byte)."""
    import json, socket, struct
    received = []
    if fds:
        head, received, _, _ = socket.recv_fds(sock, 4, fds)
        if not head:
            raise EOFError('AL service connection closed')
        head += _al_service_exact(sock, 4 - len(head))
    else:
        head = _al_service_exact(sock, 4)
    size = struct.unpack('>I', head)[0]
    if size > limit:
        for descriptor in received:
            import os
            os.close(descriptor)
        raise ValueError('AL service message too large')
    value = json.loads(_al_service_exact(sock, size))
    return (value, received) if fds else value


def _al_service_peer_uid(sock):
    import socket, struct, sys
    if sys.platform == 'darwin':
        raw = sock.getsockopt(0, getattr(socket, 'LOCAL_PEERCRED', 1), 76)
        return struct.unpack_from('=II', raw)[1]
    if hasattr(socket, 'SO_PEERCRED'):
        return struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))[1]
    return None


def _al_service_private(path, kind):
    """An owned, non-symlink directory (0700) or file (0600); anything else is not trusted."""
    import os, stat
    state = os.lstat(path)
    expected = (stat.S_ISDIR, 0o700) if kind == 'dir' else (stat.S_ISREG, 0o600)
    return expected[0](state.st_mode) and state.st_uid == os.getuid() and stat.S_IMODE(state.st_mode) == expected[1]


def _al_service_route(repository, platform, session, environ):
    """The route name of a (repository, platform, session) triple; None when no run is named."""
    import hashlib, json, os
    if session is None:
        native = environ.get('CLAUDE_SESSION_ID') or environ.get('CLAUDE_CODE_SESSION_ID')
        if platform != 'claude' or not native or len(native) > 1024:
            return None
        named = ['native', native]
    else:
        named = ['session', session]
    value = [os.path.realpath(repository), platform, *named]
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


def _al_service_environment(environ):
    """The environment `alctl run` hands its runtime (cli.main and installer.source.git_env)."""
    import os
    prefixes = ('GIT_', 'SSH_', 'DYLD_', 'LD_')
    env = dict(environ)
    for key in list(env):
        if key.startswith(prefixes):
            env.pop(key)
    clean = {k: v for k, v in env.items() if not k.startswith(prefixes)
             and k not in {'GIT_CONFIG', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT'}}
    clean.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS='1',
                 GIT_TERMINAL_PROMPT='0', GIT_PAGER='cat', GIT_OPTIONAL_LOCKS='0')
    env.update(clean)
    return env


def _al_service_parse(argv):
    """(repository, platform, session, arguments) of the plain `run` form, or None.

    Only exact option spellings are recognised; anything argparse would treat differently
    (abbreviations, repeats, other flags) is left to the full launcher."""
    options = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == '--':
            index += 1
            break
        if not token.startswith('-'):
            break
        name, equals, value = token.partition('=')
        if name not in {'--repository', '--platform', '--session'} or name in options:
            return None
        if not equals:
            index += 1
            if index >= len(argv):
                return None
            value = argv[index]
        options[name] = value
        index += 1
    arguments = argv[index:]
    if (not arguments or arguments[0].startswith('-') or '--repository' not in options
            or options.get('--platform') not in {'codex', 'claude'}):
        return None
    return options['--repository'], options['--platform'], options.get('--session'), arguments


def _al_service_connect(directory):
    """A connected socket to the service in `directory`, or None."""
    import os, socket
    try:
        if not _al_service_private(directory, 'dir') or not _al_service_private(os.path.join(directory, 'token'), 'file'):
            return None
        with open(os.path.join(directory, 'token'), 'rb') as handle:
            token = handle.read(256).decode().strip()
    except (OSError, UnicodeError):
        return None
    here = os.open('.', os.O_RDONLY)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        # A relative name keeps long state directories under the socket path limit.
        os.chdir(directory)
        sock.connect('s')
    except OSError:
        sock.close()
        return None
    finally:
        os.fchdir(here)
        os.close(here)
    if _al_service_peer_uid(sock) != os.getuid():
        sock.close()
        return None
    return sock, token


def _al_service_call(directory, operation):
    """A control request (status, stop) to one service; None when it is unreachable."""
    connected = _al_service_connect(directory)
    if connected is None:
        return None
    sock, token = connected
    try:
        sock.settimeout(10)
        _al_service_send(sock, {'protocol': PROTOCOL, 'op': operation, 'token': token})
        return _al_service_receive(sock)
    except (OSError, ValueError, EOFError):
        return None
    finally:
        sock.close()


def _al_service_run(services_root, launcher_identity, argv, environ):
    """Run `alctl run ARGV` in the resident service: its exit status, or None to fall back."""
    import os, signal, sys
    if environ.get('AL_SERVICE', '1') == '0':
        return None
    parsed = _al_service_parse(argv)
    if parsed is None:
        return None
    repository, platform, session, arguments = parsed
    if arguments[0] in {'start', 'clear'}:
        return None
    try:
        route = _al_service_route(repository, platform, session, environ)
        if route is None or not _al_service_private(services_root, 'dir'):
            return None
        path = os.path.join(services_root, 'routes', route)
        if not _al_service_private(path, 'file'):
            return None
        with open(path, 'rb') as handle:
            name = handle.read(128).decode().strip()
        if not name.isalnum():
            return None
        connected = _al_service_connect(os.path.join(services_root, name))
        if connected is None:
            return None
        sock, token = connected
    except (OSError, ValueError, UnicodeError):
        return None
    pending = []

    def forward(number, frame):
        pending.append(number)
        try:
            _al_service_send(sock, {'op': 'signal', 'signal': number})
        except OSError:
            pass

    started = False
    try:
        request = {'protocol': PROTOCOL, 'op': 'run', 'token': token, 'launcher': launcher_identity,
                   'repository': repository, 'platform': platform, 'session': session,
                   'argv': arguments, 'cwd': os.getcwd(), 'env': _al_service_environment(environ),
                   'umask': os.umask(0o022)}
        os.umask(request['umask'])
        _al_service_send(sock, request, [0, 1, 2])
        previous = {number: signal.signal(number, forward)
                    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            while True:
                message = _al_service_receive(sock)
                event = message.get('event')
                if event == 'fallback' and not started:
                    if pending:
                        break
                    return None
                if event == 'started':
                    started = True
                elif event == 'exit' and started:
                    status = message.get('status')
                    if isinstance(status, int) and status >= 0:
                        return status
                    if isinstance(status, int):
                        pending.append(-status)
                    break
                elif event != 'progress':
                    break
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
    except (OSError, ValueError, EOFError, TypeError, AttributeError):
        if not started and not pending:
            return None
    finally:
        sock.close()
    if pending:
        # The command ended by a signal (or was interrupted): end the same way.
        signal.signal(pending[-1], signal.SIG_DFL)
        os.kill(os.getpid(), pending[-1])
    if started:
        try:
            sys.stderr.write('alctl: the AL service connection ended before the command finished\n')
            sys.stderr.flush()
        except (OSError, ValueError):
            pass
    return 1
'''


def namespace():
    """The client helpers as callable functions (alctl service, the service itself, tests)."""
    values = {'PROTOCOL': PROTOCOL, '__name__': 'al_service_client'}
    exec(compile(CLIENT, '<al-service-client>', 'exec'), values)
    return values


def launcher_prelude(services_root, launcher_identity):
    """Launcher text run before self-verification: serve `run` warm or fall through."""
    return ('PROTOCOL=' + repr(PROTOCOL) + '\n' + CLIENT + '\n'
            "if len(sys.argv)>2 and sys.argv[1]=='run' and 'HARNESS_RETRY_CONTROL_FD' not in os.environ:\n"
            '    _al_status=_al_service_run(' + repr(str(services_root)) + ',' + repr(launcher_identity)
            + ',sys.argv[2:],os.environ)\n'
            '    if _al_status is not None:os._exit(_al_status)\n')
