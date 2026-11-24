"""Start, locate and control the resident AL service (#27) from the managed launcher.

The launcher's full `alctl run` path verifies the run binding and the pinned runtime as
before. For a run whose pin carries the service it then makes sure a service is running
for (repository common dir, pin identity, launcher identity), records a route from the
(repository, platform, session) triple to it, and still executes this command in process.
Later commands of the run reach the service through the launcher's thin client.

`SERVICE` is the service's bootstrap: the one-shot run child's own pin verification text
(`runtime_pin_boot.PIN_VERIFY`), the same live-interpreter check, then the pinned
`installer.runtime_service.serve`. A pin that fails verification never starts a service."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from .runtime_pin_boot import CHILD, PIN_VERIFY
from .runtime_service_client import PROTOCOL, namespace
from .validator_boot import BOOT

REQUIRED = ('installer/runtime_service.py', 'installer/runtime_service_client.py', 'installer/runtime_service_watch.py')
DEFAULT_IDLE_SECONDS = 1800

SERVICE = (
    BOOT
    + r"""
request=REQUEST
base=Path(request['storage'])
transaction={'pin':request['pin'],'platform':request['platform']}
"""
    + PIN_VERIFY
    + r"""from installer.runtime_pin import verify_pin
from installer.interpreter_execution import validate_live
from installer.execution_process import observe
verified=verify_pin(base/'pins',pin)
if verified['bytes']!=captured:raise ValueError('runtime pin moved before import')
validate_live(manifest['interpreter_execution_identity'],manifest['interpreter'],observe(os.getpid()))
from installer.runtime_service import serve
raise SystemExit(serve(request,manifest,context,loader,request['child']))
"""
)


def program(request):
    return SERVICE.replace("request=REQUEST", "request=" + repr(request), 1)


def services_root(storage):
    """The per-user service directory beside the run-pin store (mode 0700)."""
    return Path(storage).parent / 'al-services'


def _private(path):
    path.mkdir(mode=0o700, exist_ok=True)
    wire = namespace()
    if not wire['_al_service_private'](str(path), 'dir'):
        raise ValueError('AL service directory must be private and owned: ' + str(path))
    return path


def _write(path, data):
    temporary = path.with_name('.' + path.name + '.' + str(os.getpid()))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def launcher_identity():
    context = sys.modules.get('_al_validator_context')
    if context is None or context.request.get('mode') != 'snapshot':
        return None
    return context.request['expected']['snapshot_identity']


def service_name(common_dir, pin_identity, identity):
    value = json.dumps([common_dir, pin_identity, identity], separators=(',', ':')).encode()
    return hashlib.sha256(value).hexdigest()[:32]


def ensure(storage, repository, platform, session, transaction, manifest, argv, *, existing_run, private_session):
    """Route this run to its service and start the service when none is running.

    Never raises: the service is an optimisation and the caller always continues in process."""
    try:
        if (os.environ.get('AL_SERVICE', '1') == '0' or existing_run or private_session or not argv
                or argv[0] == 'clear' or transaction is None
                or not all(name in manifest['complete_file_manifest'] for name in REQUIRED)):
            return None
        identity = launcher_identity()
        common = transaction['repository_identity'].get('common_dir')
        if identity is None or not common:
            return None
        root = _private(services_root(storage))
        name = service_name(common, transaction['pin']['pin_identity'], identity)
        directory = _private(root / name)
        wire = namespace()
        route = wire['_al_service_route'](repository, platform, session, os.environ)
        if route is not None:
            path = _private(root / 'routes') / route
            if not path.exists() or path.read_bytes() != name.encode():
                _write(path, name.encode())
        lock = os.open(directory / 'lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return directory  # already running
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            os.close(lock)
        request = {
            'protocol': PROTOCOL, 'storage': str(storage), 'pin': transaction['pin'], 'platform': platform,
            'common_dir': common, 'launcher_identity': identity, 'service_directory': str(directory),
            'child': CHILD, 'idle_seconds': _idle(),
        }
        executable = manifest['interpreter']['executable']
        log = os.open(directory / 'log', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            subprocess.Popen([executable, '-I', '-S', '-B', '-c', program(request)], cwd='/',
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                             close_fds=True, start_new_session=True)
        finally:
            os.close(log)
        return directory
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return None


def _idle():
    try:
        value = float(os.environ.get('AL_SERVICE_IDLE_SECONDS', DEFAULT_IDLE_SECONDS))
    except ValueError:
        return DEFAULT_IDLE_SECONDS
    return value if value > 0 else DEFAULT_IDLE_SECONDS


def control(storage, action, repository=None):
    """`alctl service status|stop [--repository R]`: the services of this user (or one repository)."""
    root = services_root(storage)
    wire = namespace()
    common = None
    if repository is not None:
        from .validator_origin import repository_identity
        common = repository_identity(Path(repository).resolve())['common_dir']
    rows = []
    try:
        names = sorted(p.name for p in root.iterdir() if p.name != 'routes' and p.is_dir() and not p.is_symlink())
    except FileNotFoundError:
        names = []
    for name in names:
        status = wire['_al_service_call'](str(root / name), 'status')
        if status is None or status.get('event') != 'status':
            continue
        if common is not None and status.get('common_dir') != common:
            continue
        status = {k: v for k, v in status.items() if k != 'event'}
        status['directory'] = str(root / name)
        if action == 'stop':
            stopped = wire['_al_service_call'](str(root / name), 'stop')
            status['stopping'] = bool(stopped and stopped.get('event') == 'stopping')
        rows.append(status)
    return {'action': action, 'services': rows}
