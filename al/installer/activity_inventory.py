"""Bounded stable runtime membership; installed target code is never imported."""
import hashlib
import os
from pathlib import Path
import stat

from .activity_profiles import classify_artifact, decode, ID
from .registry import canonical, digest
from .source import git

LIMITS = {'files': 200000, 'depth': 16, 'file_bytes': 1024 * 1024 * 1024,
          'total_bytes': 8 * 1024 * 1024 * 1024}


def repository_identity(path):
    root = Path(git(Path(path).resolve(), 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    rows = git(root, 'rev-parse', '--git-dir', '--git-common-dir').decode().splitlines()
    if len(rows) != 2 or not all(rows):raise ValueError('invalid repository identity observation')
    return {'root': str(root), 'git_dir': str((root / rows[0]).resolve()), 'common_dir': str((root / rows[1]).resolve())}


def under(runtime, path):
    """Allow an alias above the bound root, never a symlink within it."""
    runtime = Path(runtime).resolve()
    path = Path(path).absolute()
    boundary = next((p for p in (path, *path.parents) if p.resolve() == runtime), None)
    if boundary is None:raise ValueError('path is outside bound runtime')
    relative = path.relative_to(boundary)
    current = runtime
    for part in relative.parts:
        current = current / part
        if current.is_symlink():raise ValueError('runtime symlink boundary')
    return current


def membership(root):
    root = Path(root)
    if root.is_symlink():raise ValueError('runtime root symlink boundary')
    if not root.exists():return []
    if not root.is_dir():raise ValueError('runtime root is not a directory')
    result = []
    for directory, directories, files in os.walk(root, followlinks=False):
        if len(Path(directory).relative_to(root).parts) > LIMITS['depth']:raise ValueError('runtime depth limit')
        for name in sorted([*directories, *files]):
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError('unsafe runtime member: ' + str(path))
            result.append((path.relative_to(root).as_posix(), 'directory' if stat.S_ISDIR(mode) else 'file'))
            if len(result) > LIMITS['files']:raise ValueError('runtime file count limit')
    return sorted(result)


def snapshot(root):
    root = Path(root)
    before = membership(root)
    result, total = {}, 0
    for relative, kind in before:
        path = root / relative
        first = path.stat()
        row = {'relative_path': relative, 'type': kind, 'mode': first.st_mode & 0o777}
        if kind == 'file':
            total += first.st_size
            if first.st_size > LIMITS['file_bytes'] or total > LIMITS['total_bytes']:
                raise ValueError('runtime byte limit')
            checksum = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):checksum.update(block)
            row['sha256'] = checksum.hexdigest()
        else:row['sha256'] = None
        last = path.stat()
        if (first.st_ino, first.st_size, first.st_mtime_ns, first.st_mode) != (last.st_ino, last.st_size, last.st_mtime_ns, last.st_mode):
            raise ValueError('runtime member moved during hashing')
        result[relative] = row
    if membership(root) != before:raise ValueError('runtime membership moved')
    return result


def _pointer(path, root, members, *, conversation=False):
    relative = path.relative_to(root).as_posix()
    raw = path.read_bytes()
    row = members[relative]
    if hashlib.sha256(raw).hexdigest() != row['sha256']:raise ValueError('runtime pointer moved')
    if conversation:
        value = decode(raw)
        if not isinstance(value, dict) or set(value) != {'conversation_id', 'session_id'}:
            raise ValueError('unknown native conversation pointer')
        native, session = value['conversation_id'], value['session_id']
        if not isinstance(native, str) or not native or len(native) > 1024 or hashlib.sha256(native.encode()).hexdigest() != path.stem:
            raise ValueError('native conversation filename/identity mismatch')
    else:
        native, session = None, raw.decode().strip()
    if not ID.fullmatch(session):raise ValueError('invalid runtime pointer session')
    return {'path': str(path), 'sha256': row['sha256'], 'mode': row['mode'],
            'kind': 'claude-conversation' if conversation else 'current', 'session_id': session, 'conversation_id': native}


def inventory_activity(roots, *, invoking_run, nonstate=None, orphan_review=None):
    result = {'schema_version': 1, 'kind': 'workspace-activity-inventory', 'invoking_run': invoking_run,
              'roots': [], 'stable': True, 'limits': dict(LIMITS), 'orphan_review': orphan_review}
    from .activity_orphans import review, subjects
    reviewed = review(orphan_review)
    seen = set()
    for request in roots:
        row = {'repository': request.get('repository'), 'platform': request.get('platform'),
               'runtime_root': str(Path(request['runtime_root']).absolute()), 'existence': None,
               'members': [], 'pointers': [], 'records': [], 'orphan_locks': [], 'errors': []}
        result['roots'].append(row)
        try:
            repo = repository_identity(request['repository']['root'])
            if repo != request['repository']:raise ValueError('runtime repository/worktree identity changed')
            if row['platform'] not in {'codex', 'claude'}:raise ValueError('unknown runtime platform')
            root = Path(row['runtime_root'])
            if root.is_symlink():raise ValueError('runtime root symlink boundary')
            root = root.resolve()
            row['runtime_root'] = str(root)
            key = (str(root), canonical(repo), row['platform'])
            if key in seen:raise ValueError('duplicate runtime inventory root')
            seen.add(key)
            row['existence'] = root.exists()
            members = snapshot(root)
            row['members'] = list(members.values())
            if not row['existence']:continue
            if 'current' in members:row['pointers'].append(_pointer(root / 'current', root, members))
            for relative, item in members.items():
                path = root / relative
                if relative.startswith('conversations/') and path.suffix == '.json':
                    if row['platform'] != 'claude' or len(Path(relative).parts) != 2:
                        raise ValueError('unsupported native pointer layout')
                    row['pointers'].append(_pointer(path, root, members, conversation=True))
                if len(Path(relative).parts) != 1 or path.suffix != '.json':continue
                if item['type'] != 'file':raise ValueError('runtime session is not a regular file')
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != item['sha256']:raise ValueError('runtime state moved')
                provenance = {'repository': repo, 'platform': row['platform'], 'runtime_root': str(root),
                              'mode': item['mode'], 'members': members, 'nonstate': nonstate or {}}
                record = classify_artifact(path, raw, provenance=provenance)
                identity = {'repository': repo, 'platform': row['platform'], 'runtime_root': str(root),
                            'state_path': str(path), 'session_id': record.pop('session_id', path.stem), 'run_id': record.pop('run_id', None)}
                record.update(identity=identity, sha256=item['sha256'], mode=item['mode'])
                row['records'].append(record)
            from .activity_nonstate import references, recognize
            source_references = []
            for record in row['records']:
                if record['classification'] != 'current-session' or record['errors']:continue
                state = decode(Path(record['identity']['state_path']).read_bytes())
                source_references.extend(references(state, repository=repo, runtime_root=root, members=members))
            for record in row['records']:
                if record['classification'] != 'unknown':continue
                path = Path(record['identity']['state_path'])
                recognized = recognize(path, path.read_bytes(), source_references)
                if recognized:record.update(recognized)
            sessions = {r['identity']['session_id']: r for r in row['records']}
            for pointer in row['pointers']:
                record = sessions.get(pointer['session_id'])
                if not record or record['classification'] in {'unknown', 'nonstate-artifact'}:
                    raise ValueError('dangling or nonstate runtime pointer')
                if pointer['kind'] == 'claude-conversation' and record['native_conversation_id'] != pointer['conversation_id']:
                    raise ValueError('native pointer session attribution conflict')
                if pointer['kind'] == 'current' and row['platform'] == 'claude' and record['native_conversation_id'] is not None:
                    raise ValueError('fallback pointer session attribution conflict')
            row['orphan_locks'] = subjects(row, reviewed, invoking_run, orphan_review)
            if snapshot(root) != members or repository_identity(repo['root']) != repo:
                raise ValueError('runtime inventory moved during observation')
        except (OSError, ValueError, TypeError, KeyError) as exc:
            row['errors'].append(str(exc));result['stable'] = False
        if any(record['errors'] for record in row['records']):result['stable'] = False
    if {row['runtime_root'] for row in reviewed} - {root['runtime_root'] for root in result['roots']}:
        raise ValueError('reviewed orphan runtime coverage omitted')
    result['digest'] = digest(canonical(result))
    return result
