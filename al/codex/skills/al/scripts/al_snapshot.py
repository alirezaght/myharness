"""The working copy as one Git tree, through a private per-worktree index (#25).

`snapshot()` runs `git add -A` and `git write-tree` against an index file kept in the
worktree's own Git directory (`git rev-parse --git-path al-snapshot.index`), never the
user's index. It is incremental through Git's stat cache, racy-clean handling and
`core.fsmonitor` where configured. The private index is seeded from the real index,
with assume-unchanged and skip-worktree flags cleared so those files are checked
against the disk like any other. The resulting tree's blob ids are content hashes of
the working copy; AL's own membership rules, symlink, nested-repository and torn
capture checks are unchanged and decide what the tree may vouch for.

All state here is per worktree and mutable; the shared fact store holds only facts.
Concurrent snapshots in one worktree never wait: a busy private index is a fallback to
direct reads. Snapshot trees used by a run are pinned under `refs/al/snapshots/<run>/`
so `git gc` keeps them; `unpin` drops a run's refs and `sweep` drops orphaned ones."""
import os
import re
import subprocess
from pathlib import Path

import al_store

INDEX = 'al-snapshot.index'
REF_PREFIX = 'refs/al/snapshots/'
EXCLUDED = ('.git', '.codex', '.claude', '.venv', 'node_modules', '__pycache__', '.pytest_cache', '.ruff_cache',
            'dist', 'build', '.next', '.turbo')
# Attributes under which Git's blob bytes may differ from the working-tree bytes.
CONVERSIONS = ('text', 'eol', 'crlf', 'filter', 'ident', 'working-tree-encoding')
_GIT = ['git', '--no-optional-locks', '-c', 'core.excludesFile=/dev/null', '-c', 'advice.addEmbeddedRepo=false',
        '-c', 'core.safecrlf=false']


class Unavailable(Exception):
    """The snapshot cannot be taken now (busy private index, no repository): read directly."""


def _run(root, args, *, env=None, data=None, timeout=120):
    result = subprocess.run(_GIT + args, cwd=str(root), input=data, capture_output=True, timeout=timeout,
                            env={**os.environ, **(env or {})})
    if result.returncode:
        raise Unavailable(result.stderr.decode(errors='replace').strip() or 'git ' + args[0] + ' failed')
    return result.stdout


def _root(root):
    return Path(root or al_store.REPO_ROOT)


def index_path(root=None):
    root = _root(root)
    value = _run(root, ['rev-parse', '--path-format=absolute', '--git-path', INDEX]).decode().strip()
    return Path(value)


def _seed(root, target):
    """Start from the real index (its stat cache makes the first snapshot incremental)."""
    real = Path(_run(root, ['rev-parse', '--path-format=absolute', '--git-path', 'index']).decode().strip())
    fd = os.open(str(target) + '.seed', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        state = None
        with os.fdopen(fd, 'wb') as handle:
            try:
                handle.write(real.read_bytes())
                state = real.stat()
            except FileNotFoundError:
                pass
        if state is not None:
            # Keep the index's own timestamp: Git's racy-clean check compares entries with it,
            # and a newer copy would let racily-clean entries pass unchecked.
            os.utime(str(target) + '.seed', ns=(state.st_atime_ns, state.st_mtime_ns))
        os.replace(str(target) + '.seed', target)
    finally:
        if os.path.exists(str(target) + '.seed'):
            os.unlink(str(target) + '.seed')


def snapshot(root=None):
    """The tree id of the working copy (AL's generated/cache directories excluded)."""
    root = _root(root)
    target = index_path(root)
    if target.is_symlink():
        raise Unavailable('private snapshot index is a symlink')
    if not target.exists():
        _seed(root, target)
    env = {'GIT_INDEX_FILE': str(target)}
    flagged = [row[2:] for row in _run(root, ['ls-files', '-v', '-z'], env=env).split(b'\0')
               if row[:1].islower() or row[:1] == b'S']
    if flagged:
        # Git trusts flagged entries without looking at the disk; AL checks them like any file.
        for flag in ('--no-assume-unchanged', '--no-skip-worktree'):
            _run(root, ['update-index', flag, '-z', '--stdin'], env=env, data=b'\0'.join(flagged) + b'\0')
    excludes = [":(exclude,glob)**/" + name + "/**" for name in EXCLUDED]
    _run(root, ['add', '-A', '--ignore-errors', '--', '.', *excludes], env=env, timeout=600)
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
