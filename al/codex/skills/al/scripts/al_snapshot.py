"""The working copy as one Git tree, through a private per-worktree index (#25).

`snapshot()` runs `git add` (tracked files and AL's untracked members) and `git write-tree` against an index kept in the
worktree's own Git directory (`git rev-parse --git-path al-snapshot`, file `index`), never
the user's index. It is incremental through Git's stat cache, racy-clean handling and
`core.fsmonitor` where configured. The private index is seeded from the real index,
with assume-unchanged and skip-worktree flags cleared so those files are checked
against the disk like any other. The resulting tree's blob ids are content hashes of
the working copy; AL's own membership rules, symlink, nested-repository and torn
capture checks are unchanged and decide what the tree may vouch for.

All state here is per worktree and mutable; the shared fact store holds only facts.
Concurrent snapshots in one worktree never wait: a busy private index is a fallback to
direct reads. Snapshot trees used by a run are pinned under `refs/al/snapshots/<run>/`
so `git gc` keeps them; `unpin` drops a run's refs and `sweep` drops orphaned ones.

Under a sandbox that admits only AL's private paths (the Harness operation sandbox, #31),
`AL_SNAPSHOT_OBJECTS=private` keeps the objects a snapshot writes in the private directory
(`al-snapshot/objects`, the repository's object store read as an alternate): tree ids are
content hashes, so they are the same ids a snapshot with the shared object store computes."""
import contextlib
import contextvars
import os
import re
import subprocess
from pathlib import Path

import al_store

PRIVATE = 'al-snapshot'
INDEX = 'index'
REF_PREFIX = 'refs/al/snapshots/'
EXCLUDED = ('.git', '.codex', '.claude', '.venv', 'node_modules', '__pycache__', '.pytest_cache', '.ruff_cache',
            'dist', 'build', '.next', '.turbo')
# Attributes under which Git's blob bytes may differ from the working-tree bytes.
CONVERSIONS = ('text', 'eol', 'crlf', 'filter', 'ident', 'working-tree-encoding')
_GIT = ['git', '--no-optional-locks', '-c', 'core.excludesFile=/dev/null', '-c', 'advice.addEmbeddedRepo=false',
        '-c', 'core.safecrlf=false']


# A tree an enclosing capture took and will confirm unchanged when it ends (attribution, #25).
_REUSE = contextvars.ContextVar('al_snapshot_reuse', default=None)


@contextlib.contextmanager
def reusing(root, tree):
    """Inside, `vouched` answers from `tree` instead of a new snapshot of `root`. Only for a
    caller that compares the working copy with `tree` again afterwards and refuses movement."""
    token = _REUSE.set((str(_root(root)), tree))
    try:
        yield
    finally:
        _REUSE.reset(token)


class Unavailable(Exception):
    """The snapshot cannot be taken now (busy private index, no repository): read directly."""


_OBJECTS = {}


def _objects(root):
    """GIT_OBJECT_DIRECTORY/alternates for a private object store, or {} (see module doc)."""
    if os.environ.get('AL_SNAPSHOT_OBJECTS') != 'private':
        return {}
    key = str(root)
    if key not in _OBJECTS:
        def path(name):
            result = subprocess.run(_GIT + ['rev-parse', '--path-format=absolute', '--git-path', name], cwd=key,
                                    capture_output=True, timeout=30,
                                    env={k: v for k, v in os.environ.items()
                                         if k not in ('GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES')})
            if result.returncode:
                raise Unavailable('git rev-parse failed')
            return Path(result.stdout.decode().strip())
        private = path(PRIVATE) / 'objects'
        if private.is_symlink() or private.parent.is_symlink():
            raise Unavailable('private snapshot object store is a symlink')
        private.mkdir(mode=0o700, parents=True, exist_ok=True)
        _OBJECTS[key] = {'GIT_OBJECT_DIRECTORY': str(private), 'GIT_ALTERNATE_OBJECT_DIRECTORIES': str(path('objects'))}
    return _OBJECTS[key]


def _run(root, args, *, env=None, data=None, timeout=120):
    result = subprocess.run(_GIT + args, cwd=str(root), input=data, capture_output=True, timeout=timeout,
                            env={**os.environ, **_objects(root), **(env or {})})
    if result.returncode:
        raise Unavailable(result.stderr.decode(errors='replace').strip() or 'git ' + args[0] + ' failed')
    return result.stdout


def _root(root):
    return Path(root or al_store.REPO_ROOT)


def index_path(root=None):
    """The private index, in AL's own directory of the worktree's Git directory (created 0700)."""
    root = _root(root)
    directory = Path(_run(root, ['rev-parse', '--path-format=absolute', '--git-path', PRIVATE]).decode().strip())
    if directory.is_symlink():
        raise Unavailable('private snapshot directory is a symlink')
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise Unavailable('private snapshot directory unavailable: ' + str(exc)) from exc
    return directory / INDEX


def _seed(root, target):
    """Start from the real index: its entries are the tracked set, its stat cache makes the
    snapshot incremental. The copy keeps the index's own timestamp: Git's racy-clean check
    compares entries with it, and a newer copy would let racily-clean entries pass unchecked."""
    real = Path(_run(root, ['rev-parse', '--path-format=absolute', '--git-path', 'index']).decode().strip())
    temporary = str(target) + '.seed.' + str(os.getpid())
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        state = None
        with os.fdopen(fd, 'wb') as handle:
            try:
                handle.write(real.read_bytes())
                state = real.stat()
            except FileNotFoundError:
                pass
        if state is not None:
            os.utime(temporary, ns=(state.st_atime_ns, state.st_mtime_ns))
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def untracked(root=None):
    """Untracked members by AL's capture rule: repository .gitignore files only (no global or
    info/exclude rules), AL's generated/cache directory names excluded."""
    excluded = [v for name in EXCLUDED for v in ('--exclude=' + name + '/', '--exclude=**/' + name + '/')]
    raw = _run(_root(root), ['ls-files', '--others', '--exclude-per-directory=.gitignore', *excluded, '-z'])
    return [row for row in raw.split(b'\0') if row]


_RELAY = {}


def _relay_answer(root, value):
    """The service's snapshot answer through the supervisor's relay (#34, Linux sandbox).

    `AL_SERVICE_FD` is `<fd>:<inode>`: an inherited, already connected socket whose other end
    the Harness supervisor holds outside the sandbox; the supervisor adds the service token and
    relays `snapshot` requests only. The inode proves the descriptor is still that socket (a
    child that did not inherit it may have reused the number). Requests on it are sequential."""
    import json
    import socket
    import stat
    import struct
    import threading
    lock = _RELAY.setdefault('lock', threading.Lock())
    try:
        fd, inode = (int(part) for part in value.split(':'))
        state = os.fstat(fd)
        if not stat.S_ISSOCK(state.st_mode) or state.st_ino != inode:
            return None
        sock = socket.socket(fileno=os.dup(fd))
    except (ValueError, OSError):
        return None
    with lock, sock:
        try:
            sock.settimeout(10)
            data = json.dumps({'op': 'snapshot', 'repository': str(root)}, sort_keys=True,
                              separators=(',', ':')).encode()
            sock.sendall(struct.pack('>I', len(data)) + data)

            def exact(size):
                raw = b''
                while len(raw) < size:
                    chunk = sock.recv(size - len(raw))
                    if not chunk:
                        raise ValueError('relay closed')
                    raw += chunk
                return raw
            size = struct.unpack('>I', exact(4))[0]
            if size > 65536:
                return None
            return json.loads(exact(size))
        except (OSError, ValueError, UnicodeError):
            return None


def _service_tree(root):
    """The resident AL service's proven-current tree of `root` (`AL_SERVICE_SNAPSHOT` names the
    service directory a supervisor admitted, #31; `AL_SERVICE_FD` its relay, #34), or None.
    Any doubt is None."""
    import json
    import socket
    import stat
    import struct
    relay = os.environ.get('AL_SERVICE_FD')
    if relay:
        return _checked_tree(root, _relay_answer(root, relay))
    directory = os.environ.get('AL_SERVICE_SNAPSHOT')
    if not directory or not os.path.isabs(directory):
        return None
    here = None
    try:
        for path, kind, mode in ((directory, stat.S_ISDIR, 0o700), (os.path.join(directory, 'token'), stat.S_ISREG, 0o600)):
            state = os.lstat(path)
            if not kind(state.st_mode) or state.st_uid != os.getuid() or stat.S_IMODE(state.st_mode) != mode:
                return None
        with open(os.path.join(directory, 'token'), 'rb') as handle:
            token = handle.read(256).decode().strip()
        here = os.open('.', os.O_RDONLY)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            os.chdir(directory)  # a relative name keeps long paths under the socket limit
            sock.connect('s')
            os.fchdir(here)
            data = json.dumps({'protocol': 1, 'op': 'snapshot', 'token': token, 'repository': str(root)},
                              sort_keys=True, separators=(',', ':')).encode()
            sock.sendall(struct.pack('>I', len(data)) + data)
            raw = b''
            while len(raw) < 4 or len(raw) < 4 + struct.unpack('>I', raw[:4])[0]:
                chunk = sock.recv(4096)
                if not chunk or len(raw) > 65536:
                    return None
                raw += chunk
        answer = json.loads(raw[4:4 + struct.unpack('>I', raw[:4])[0]])
    except (OSError, ValueError, UnicodeError):
        return None
    finally:
        if here is not None:
            try:
                os.fchdir(here)
            finally:
                os.close(here)
    return _checked_tree(root, answer)


def _checked_tree(root, answer):
    """The tree id an answer names, when it is a well-formed snapshot answer naming a tree."""
    tree = answer.get('tree') if isinstance(answer, dict) and answer.get('event') == 'snapshot' else None
    if not isinstance(tree, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', tree):
        return None
    try:
        return tree if _run(root, ['cat-file', '-t', tree]).decode().strip() == 'tree' else None
    except (Unavailable, OSError, subprocess.SubprocessError):
        return None


def snapshot(root=None):
    """The tree id of the working copy with AL's membership: every tracked file, wherever it
    lives (generated-looking directories included), plus untracked files by AL's rule."""
    root = _root(root)
    served = _service_tree(root)
    if served is not None:
        return served
    target = index_path(root)
    if target.is_symlink():
        raise Unavailable('private snapshot index is a symlink')
    # Reseeded every time: the tracked set is exactly the real index's, and nothing an
    # earlier snapshot added can linger.
    _seed(root, target)
    env = {'GIT_INDEX_FILE': str(target)}
    flagged = [row[2:] for row in _run(root, ['ls-files', '-v', '-z'], env=env).split(b'\0')
               if row[:1].islower() or row[:1] == b'S']
    if flagged:
        # Git trusts flagged entries without looking at the disk; AL checks them like any file.
        for flag in ('--no-assume-unchanged', '--no-skip-worktree'):
            _run(root, ['update-index', flag, '-z', '--stdin'], env=env, data=b'\0'.join(flagged) + b'\0')
    _run(root, ['add', '-u', '--ignore-errors'], env=env, timeout=600)
    extra = untracked(root)
    if extra:
        _run(root, ['add', '-f', '--ignore-errors', '--pathspec-from-file=-', '--pathspec-file-nul'], env=env,
             data=b'\0'.join(extra) + b'\0', timeout=600)
    return _run(root, ['write-tree'], env=env).decode().strip()


def entries(root, tree, paths=None):
    """{path: (mode, kind, oid, size or None)} of a tree, recursively (optionally under `paths`)."""
    out = {}
    raw = _run(_root(root), ['ls-tree', '-r', '-l', '-z', '--full-tree', tree, *(['--', *paths] if paths else [])])
    for row in raw.split(b'\0'):
        if not row:
            continue
        meta, path = row.split(b'\t', 1)
        mode, kind, oid, size = meta.split()
        out[path.decode()] = (mode.decode(), kind.decode(), oid.decode(), int(size) if size.isdigit() else None)
    return out


def convertible(root, paths):
    """Paths whose working-tree bytes Git may convert (so their blob is not their content)."""
    root = _root(root)
    config = _run(root, ['config', '--get', 'core.autocrlf'], timeout=10) if _has_config(root, 'core.autocrlf') else b''
    if config.strip().lower() in {b'true', b'input'}:
        return set(paths)
    if not paths:
        return set()
    raw = _run(root, ['check-attr', '-z', '--stdin', *CONVERSIONS], data=b'\0'.join(p.encode() for p in paths) + b'\0')
    fields = raw.split(b'\0')
    out = set()
    for index in range(0, len(fields) - 2, 3):
        if fields[index + 2] != b'unspecified':
            out.add(fields[index].decode())
    return out


BLOB_SHA256 = 'sha256-of-git-blob-v1'
FILE_BYTES = 2 * 1024 * 1024


def blob_sha256(root, oids, *, batch_bytes=4 * 1024 * 1024, sizes=None):
    """{oid: sha256} of Git blobs: stored facts first, then verified object reads (recorded).

    Blobs larger than the per-file limit or unreadable are left out."""
    import hashlib
    import al_analysis_index as index
    from al_git_cache import blobs
    oids = list(dict.fromkeys(oids))
    known = index.lookup('git-blob-sha256', BLOB_SHA256, oids, root=root)
    out = {oids[i]: v for i, v in known.items() if isinstance(v, str) and len(v) == 64}
    missing = [o for o in oids if o not in out]
    batch, size = [], 0

    def read(batch):
        try:
            values = blobs(root, batch, max_bytes=max(batch_bytes, FILE_BYTES), file_bytes=FILE_BYTES)
        except (OSError, ValueError, subprocess.SubprocessError):
            return
        found = [(oid, hashlib.sha256(values[oid]).hexdigest()) for oid in batch]
        out.update(found)
        index.record('git-blob-sha256', BLOB_SHA256, found, root=root)
    for oid in missing:
        weight = (sizes or {}).get(oid) or 0
        if batch and size + weight > batch_bytes:
            read(batch)
            batch, size = [], 0
        batch.append(oid)
        size += weight
    if batch:
        read(batch)
    return out


def vouched(root, wanted):
    """{path: (sha256, size)} for working-copy files whose snapshot blob is their content.

    `wanted(path)` selects candidate paths. Only regular blobs within the per-file limit and
    without Git conversions qualify; the caller still checks each file's identity (regular,
    blob size, older than the racy window) before relying on it. Empty when the store is
    disabled or the snapshot is unavailable."""
    import al_analysis_index as index
    from al_analysis_cache import CURRENT
    observation = CURRENT.get()
    # Read-only inspections never write Git objects, not even to a private index.
    if not index.enabled() or (observation is not None and observation.readonly_git):
        return {}
    root = _root(root)
    try:
        reused = _REUSE.get()
        tree = reused[1] if reused is not None and reused[0] == str(root) else snapshot(root)
        rows = {p: row for p, row in entries(root, tree).items()
                if row[0] in ('100644', '100755') and row[1] == 'blob' and row[3] is not None
                and row[3] <= FILE_BYTES and wanted(p)}
        for p in convertible(root, sorted(rows)):
            rows.pop(p, None)
    except (Unavailable, OSError, ValueError, subprocess.SubprocessError):
        return {}
    # Only blobs whose sha256 is already a stored fact (recorded from verified object reads of
    # the index and the base); anything else is read from the working copy by the caller.
    oids = sorted({r[2] for r in rows.values()})
    known = index.lookup('git-blob-sha256', BLOB_SHA256, oids, root=root)
    digests = {oids[i]: v for i, v in known.items() if isinstance(v, str) and len(v) == 64}
    return {p: (digests[r[2]], r[3]) for p, r in rows.items() if r[2] in digests}


def _has_config(root, key):
    result = subprocess.run(['git', 'config', '--get', key], cwd=str(root), capture_output=True, timeout=10)
    return result.returncode == 0


def input_tree(st, paths):
    """The Git tree hash of the working copy restricted to `paths` (files or directories).

    Equal hashes mean byte-identical content, modes and layout under those paths; it is
    the freshness identity of a check's inputs."""
    root = al_store.REPO_ROOT
    tree = snapshot(root)
    paths = sorted({p.rstrip('/') for p in paths if p not in {'', '.'}}) if '.' not in paths else []
    if not paths:
        return tree
    rows = _run(root, ['ls-tree', '-r', '-z', '--full-tree', tree, '--', *paths])
    target = index_path(root).with_name(INDEX + '.input.' + str(os.getpid()))
    try:
        env = {'GIT_INDEX_FILE': str(target)}
        _run(root, ['read-tree', '--empty'], env=env)
        info = b''.join(meta + b'\t' + path + b'\0' for meta, path in (r.split(b'\t', 1) for r in rows.split(b'\0') if r))
        _run(root, ['update-index', '-z', '--index-info'], env=env, data=info)
        return _run(root, ['write-tree'], env=env).decode().strip()
    finally:
        if target.exists():
            target.unlink()


def _safe_name(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', value) or '..' in value:
        raise ValueError('unsafe snapshot ref name')
    return value


def pin(run, name, tree, root=None):
    """Keep a run's snapshot tree reachable for `git gc`."""
    _run(_root(root), ['update-ref', REF_PREFIX + _safe_name(run) + '/' + _safe_name(name), tree])


def pinned(root=None):
    raw = _run(_root(root), ['for-each-ref', '--format=%(refname)', REF_PREFIX]).decode()
    return [line for line in raw.splitlines() if line]


def unpin(run, root=None):
    prefix = REF_PREFIX + _safe_name(run) + '/'
    for ref in pinned(root):
        if ref.startswith(prefix):
            _run(_root(root), ['update-ref', '-d', ref])


def sweep(active_runs, root=None):
    """Drop snapshot refs of runs that are no longer active."""
    keep = {_safe_name(r) for r in active_runs}
    for ref in pinned(root):
        run = ref[len(REF_PREFIX):].split('/', 1)[0]
        if run not in keep:
            _run(_root(root), ['update-ref', '-d', ref])
