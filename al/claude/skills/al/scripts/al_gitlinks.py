"""Read-only identity and worktree validation for opaque Git submodules."""
import os
import hashlib
import re
from pathlib import Path
import subprocess


class GitlinkError(ValueError):
    pass


def git(root, *args):
    try:
        result = subprocess.run(['git', '--no-optional-locks', '--literal-pathspecs', *args], cwd=root, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitlinkError('submodule inspection failed: ' + str(exc)) from exc
    if result.returncode:
        raise GitlinkError('submodule inspection failed: ' + result.stderr.decode(errors='replace').strip())
    return result.stdout


def entries(raw, index=False):
    out = {}
    try:
        for record in raw.split(b'\0'):
            if not record:
                continue
            meta, path = record.split(b'\t', 1)
            mode, middle, last = meta.split()
            if index and last != b'0':
                raise GitlinkError('unmerged index is unsupported')
            out[path] = {'mode': mode.decode(), 'oid': (middle if index else last).decode()}
    except (ValueError, UnicodeError) as exc:
        raise GitlinkError('invalid Git entry metadata: ' + str(exc)) from exc
    return out


_INDEXES = {}


def _index_key(root):
    metadata = plain_metadata(root)
    if metadata is None:
        return None
    try:
        return str(metadata), hashlib.sha256((metadata / 'index').read_bytes()).hexdigest()
    except OSError:
        return None


def index_entries(root):
    """Index entries, reused only for byte-identical index content of a plain repository."""
    key = _index_key(root)
    if key is None or key not in _INDEXES:
        value = entries(git(root, 'ls-files', '--stage', '-z'), index=True)
        # Only a listing whose index bytes did not change around the read may be reused.
        if key is None or _index_key(root) != key:
            return value
        if len(_INDEXES) >= 8:
            _INDEXES.clear()
        _INDEXES[key] = value
    return {path: dict(entry) for path, entry in _INDEXES[key].items()}


_TREES = {}


HEX = re.compile(rb'[0-9a-f]{40}|[0-9a-f]{64}')


def _head(metadata):
    """HEAD's object id from plain files-backend metadata, or None to let Git resolve it."""
    try:
        if (metadata / 'reftable').exists() or (metadata / 'commondir').exists():
            return None
        head = (metadata / 'HEAD').read_bytes().strip()
        if HEX.fullmatch(head):
            return head.decode()
        if not head.startswith(b'ref: refs/heads/'):
            return None
        name = head[5:]
        if b'..' in name.split(b'/'):
            return None
        reference = metadata / os.fsdecode(name)
        if reference.is_file() and not reference.is_symlink():
            value = reference.read_bytes().strip()
            return value.decode() if HEX.fullmatch(value) else None
        if reference.exists() or reference.is_symlink():
            return None
        packed = metadata / 'packed-refs'
        if not packed.is_file():
            return None
        for line in packed.read_bytes().splitlines():
            parts = line.split(b' ')
            if len(parts) == 2 and parts[1] == name and HEX.fullmatch(parts[0]):
                return parts[0].decode()
    except OSError:
        return None
    return None


def _tree_key(root, revision):
    """Listings of an exact object id are immutable unless replacement refs change."""
    metadata = plain_metadata(root)
    if metadata is None:
        return None
    if revision == 'HEAD':
        revision = _head(metadata)
    if revision is None or not HEX.fullmatch(revision.encode()):
        return None
    return str(metadata), revision, replacement_state(metadata)


def replacement_state(metadata):
    """File identity of everything that can redirect object history in a plain repository:
    replacement refs (and `core.useReplaceRefs` in config), shallow boundaries and grafts."""
    state = []
    for path in (metadata / 'packed-refs', metadata / 'refs' / 'replace', metadata / 'config',
                 metadata / 'shallow', metadata / 'info' / 'grafts'):
        try:
            value = path.lstat()
        except FileNotFoundError:
            state.append(None)
        else:
            state.append((value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns))
    return tuple(state)


REPOSITORY_ENV = ('GIT_DIR', 'GIT_COMMON_DIR', 'GIT_WORK_TREE', 'GIT_NO_REPLACE_OBJECTS', 'GIT_REPLACE_REF_BASE',
                  'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_GRAFT_FILE')


def plain_metadata(root):
    """The `.git` directory of a plain (non-linked, non-reftable) repository, else None.

    Repository-selecting Git environment disables reuse: Git would not read this metadata."""
    if any(name in os.environ for name in REPOSITORY_ENV):
        return None
    metadata = Path(root).resolve() / '.git'
    try:
        if metadata.is_symlink() or not metadata.is_dir() or (metadata / 'commondir').exists() or (metadata / 'reftable').exists():
            return None
    except OSError:
        return None
    return metadata


_LISTINGS = {}


def long_tree(root, revision):
    """Raw `ls-tree -rlz --full-tree` bytes, reused for an unchanged exact object id."""
    key = _tree_key(root, revision)
    if key is None or key not in _LISTINGS:
        value = subprocess.run(['git', 'ls-tree', '-rlz', '--full-tree', key[1] if key else revision], cwd=root,
                               capture_output=True, check=True).stdout
        if key is None:
            return value
        if len(_LISTINGS) >= 8:
            _LISTINGS.clear()
        _LISTINGS[key] = value
    return _LISTINGS[key]


def tree_entries(root, revision):
    key = _tree_key(root, revision)
    if key is None or key not in _TREES:
        # Read the exact object the key names, never a moving symbolic revision.
        value = entries(git(root, 'ls-tree', '-r', '-z', key[1] if key else revision))
        if key is None or _tree_key(root, revision) != key:
            return value
        if len(_TREES) >= 8:
            _TREES.clear()
        _TREES[key] = value
    return {path: dict(entry) for path, entry in _TREES[key].items()}


def paths(*maps):
    return {path for items in maps for path, entry in items.items() if entry['mode'] == '160000'}


def observe(root, raw, base_entry, head_entry, index_entry):
    rel = Path(os.fsdecode(raw))
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise GitlinkError('unsafe submodule path')
    root = Path(root).resolve()
    if any((root / Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts))):
        raise GitlinkError('submodule path traverses symlink: ' + str(rel))
    indexed = bool(index_entry and index_entry['mode'] == '160000')
    child = {'state': 'uninitialized' if indexed else 'replaced', 'head': None,
             'dirty': False, 'status_sha256': None, 'error': None, 'contents_captured': False}
    effective = {'type': 'gitlink', **index_entry} if indexed else None
    result = {'base': base_entry, 'head': head_entry, 'index': index_entry,
              'effective': effective, 'child': child}
    path = root / rel
    try:
        if path.is_symlink() or path.is_file():
            child['state'] = 'replaced'
            result['effective'] = None
            return result
        if not path.exists() or (path.is_dir() and not any(path.iterdir())):
            if not indexed:
                child['state'] = 'removed'
                result['effective'] = {'type': 'deleted'}
            return result
        if not path.is_dir():
            raise GitlinkError('unsupported submodule file type: ' + str(rel))
        # Without child metadata a normal replacement directory belongs to the parent.
        if not indexed and not (path / '.git').exists():
            return result
        top = Path(os.fsdecode(git(path, 'rev-parse', '--show-toplevel')).rstrip('\n')).resolve()
        if top != path:
            raise GitlinkError('nonempty uninitialized submodule: ' + str(rel))
        child['head'] = git(path, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
        status = git(path, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignore-submodules=none')
        child.update(state='initialized' if indexed else 'removed', dirty=bool(status),
                     status_sha256=hashlib.sha256(status).hexdigest())
        result['effective'] = {'type': 'gitlink', 'mode': '160000', 'oid': child['head']} if indexed else {'type': 'deleted'}
    except (GitlinkError, OSError) as exc:
        child.update(state='inspection-failed', dirty=None, error=str(exc))
        result['effective'] = {'type': 'unknown'}
    return result


def opaque(row):
    child = row.get('child', {})
    return child.get('state') in {'initialized', 'uninitialized', 'inspection-failed'} or (child.get('state') == 'removed' and child.get('head') is not None)
