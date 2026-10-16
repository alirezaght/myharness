"""Read-only identity and worktree validation for opaque Git submodules."""
import os
import hashlib
from pathlib import Path
import subprocess


class GitlinkError(ValueError):
    pass


def git(root, *args):
    try:
        result = subprocess.run(['git', '--no-optional-locks', *args], cwd=root, capture_output=True, timeout=30)
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


def index_entries(root):
    return entries(git(root, 'ls-files', '--stage', '-z'), index=True)


def tree_entries(root, revision):
    return entries(git(root, 'ls-tree', '-r', '-z', revision))


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
