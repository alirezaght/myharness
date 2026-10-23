"""Declared, candidate-bound review-hook state under the disposable checkout's `.git`.

A gate may declare one isolated `.git/<name>` directory that its repository review hook writes.
The directory must be absent before execution (stale or foreign state is refused); whatever the
hook leaves there (or its absence) is captured as an immutable audit artifact bound to the run,
reservation, manifest, gate, stage, invocation, execution identity and candidate, and is removed
afterwards so every other metadata write still fails scratch integrity."""
import base64
import hashlib
import json
import re
import shutil
import stat
from pathlib import Path

import al_artifacts as A
from al_checkout_paths import MaterializationError

SEGMENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')
# Git's own metadata is never a review-state location.
RESERVED = frozenset({'objects', 'refs', 'hooks', 'index', 'HEAD', 'config', 'info', 'logs', 'modules',
                      'worktrees', 'packed-refs', 'lfs', 'description', 'FETCH_HEAD', 'ORIG_HEAD', 'MERGE_HEAD',
                      'COMMIT_EDITMSG', 'shallow', 'commondir', 'gitdir', 'sequencer', 'rebase-merge', 'rebase-apply'})
MAX_FILES = 4096
MAX_BYTES = 16 * 1024 * 1024
KIND = 'review-hook-state-v1'


def validate_declaration(value):
    if not isinstance(value, dict) or set(value) != {'path'} or not isinstance(value['path'], str):
        raise ValueError('review state declaration must be {"path": ".git/<name>"}')
    parts = value['path'].split('/')
    if not 2 <= len(parts) <= 4 or parts[0] != '.git' or parts[1] in RESERVED or not all(SEGMENT.match(p) for p in parts[1:]):
        raise ValueError('review state must be an isolated .git/<name> location outside Git metadata')
    return {'path': value['path']}


def prepare(root, declaration):
    """The declared location must not exist yet; any existing state is stale or foreign."""
    top = Path(root) / '/'.join(declaration['path'].split('/')[:2])
    if top.exists() or top.is_symlink():
        raise MaterializationError('stale or foreign review state present before execution: ' + declaration['path'])
    return top


def capture(root, declaration):
    """Files (bytes, mode) and directories (mode) of the declared location; None when absent."""
    target = Path(root) / declaration['path']
    top = Path(root) / '/'.join(declaration['path'].split('/')[:2])
    for path in (top, target):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise MaterializationError('declared review state location is not a directory')
    if not target.exists():
        return None
    files, directories, total = {}, {'.': stat.S_IMODE(target.lstat().st_mode)}, 0

    def visit(directory):
        nonlocal total
        for path in sorted(directory.iterdir()):
            info = path.lstat()
            relative = path.relative_to(target).as_posix()
            if stat.S_ISDIR(info.st_mode):
                directories[relative] = stat.S_IMODE(info.st_mode)
                visit(path)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                raw = path.read_bytes()
                total += len(raw)
                if len(files) + len(directories) > MAX_FILES or total > MAX_BYTES:
                    raise MaterializationError('review state exceeds bounded capture')
                if path.lstat() != info:
                    raise MaterializationError('review state changed during capture: ' + relative)
                files[relative] = (raw, stat.S_IMODE(info.st_mode))
            else:
                raise MaterializationError('unsupported review state member (link or special file): ' + relative)
    visit(target)
    return {'files': files, 'directories': directories}


def remove(top):
    if top.is_symlink():
        raise MaterializationError('declared review state directory was replaced')
    if top.exists():
        shutil.rmtree(top)


def seal(st, sid, gate, stage, fingerprint, invocation, captured, entry, manifest_sha):
    files = captured['files'] if captured else {}
    metadata = {name: {'sha256': hashlib.sha256(raw).hexdigest(), 'mode': mode, 'size': len(raw)}
                for name, (raw, mode) in sorted(files.items())}
    key = entry.get('execution_key')
    bundle = {'schema_version': 1, 'kind': KIND, 'run': st['run_id'], 'gate': gate['id'], 'stage': stage,
              'path': gate['review_state']['path'], 'candidate': fingerprint, 'invocation': invocation,
              'reservation': entry.get('reservation'), 'manifest': manifest_sha,
              'execution_sha256': hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest() if isinstance(key, dict) else None,
              'present': captured is not None, 'directories': captured['directories'] if captured else {}, 'files': metadata,
              'contents': {name: base64.b64encode(raw).decode() for name, (raw, _) in sorted(files.items())}}
    raw = json.dumps(bundle, sort_keys=True, separators=(',', ':')).encode()
    reference = A.snapshot_bytes(sid, raw, KIND, 'gate ' + gate['id'] + ' review hook state')
    return {'path': bundle['path'], 'candidate': fingerprint, 'files': metadata, 'artifact': reference}


def gaps(st, gate, entry, fingerprint):
    """Evidence for a declared gate must carry its own candidate-bound review state."""
    declaration = gate.get('review_state')
    state = entry.get('review_state')
    if declaration is None:
        return ['undeclared review state recorded for ' + gate['id']] if state is not None else []
    if not isinstance(state, dict) or set(state) != {'path', 'candidate', 'files', 'artifact'}:
        return ['missing review state for ' + gate['id']]
    try:
        bundle = json.loads(A.read(state['artifact']))
    except (OSError, ValueError, KeyError, TypeError, SystemExit):
        return ['unreadable review state artifact for ' + gate['id']]
    try:
        contents = {name: base64.b64decode(value, validate=True) for name, value in bundle['contents'].items()}
        expected = {name: {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)} for name, raw in contents.items()}
        actual = {name: {k: row[k] for k in ('sha256', 'size')} for name, row in bundle['files'].items()}
    except (KeyError, TypeError, ValueError, AttributeError):
        return ['malformed review state artifact for ' + gate['id']]
    if (bundle.get('kind') != KIND or bundle.get('run') != st['run_id'] or bundle.get('gate') != gate['id']
            or bundle.get('path') != declaration['path'] or state['path'] != declaration['path']
            or bundle.get('stage') != entry.get('gate_stage') or bundle.get('invocation') != entry.get('invocation')
            or bundle.get('reservation') != entry.get('reservation') or bundle.get('manifest') != entry.get('gate_manifest')
            or bundle.get('execution_sha256') != (hashlib.sha256(json.dumps(entry['execution_key'], sort_keys=True).encode()).hexdigest()
                                                  if isinstance(entry.get('execution_key'), dict) else None)
            or bundle.get('files') != state['files'] or expected != actual):
        return ['foreign or altered review state for ' + gate['id']]
    if bundle.get('candidate') != fingerprint or state['candidate'] != fingerprint:
        return ['stale review state for ' + gate['id']]
    return []
