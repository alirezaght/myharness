"""Task attribution captures bracketed by working-copy snapshot trees (#25).

An attribution capture reads the candidate records once, between two observations of the
working copy. Each observation is the snapshot tree (`al_snapshot.snapshot`: every member's
content, mode and layout) plus the residual state a tree does not record: the index entries,
HEAD, the untracked listing the records use, physical modes, and the direct content of every
candidate path the tree cannot vouch for (outside AL's membership, or converted by Git
attributes). The capture is stable exactly when the two observations agree; differences are
found with `git diff-tree` between the trees, restricted to the paths a capture may ignore (an
ACTIVE sibling writer's ownership). The records' own snapshot reads reuse the opening tree.

Retained content of every record the tree holds exactly is the tree itself, pinned under
`refs/al/snapshots/<run>/` so `git gc` keeps it; only the remaining records are copied into
artifacts. Gitlinks (child working copies a tree cannot describe) and an unavailable snapshot
fall back to the double read of the records."""
import contextlib
import contextvars
import copy
import hashlib
import json
import os
import stat

import al_snapshot
import al_store
import al_gitlinks as G

_FILE_MODES = {'100644', '100755'}
_EXISTS = set()


def _owned(path, files):
    return any(path == p or path.startswith(p.rstrip('/') + '/') for p in files)


def _direct(root, path):
    """(kind, payload digest) of a path read without Git: the content the records would read."""
    try:
        value = os.lstat(os.path.join(root, path))
    except FileNotFoundError:
        return ('absent', None)
    except NotADirectoryError:
        return ('absent', None)
    if stat.S_ISLNK(value.st_mode):
        return ('symlink', hashlib.sha256(os.fsencode(os.readlink(os.path.join(root, path)))).hexdigest())
    if stat.S_ISREG(value.st_mode):
        with open(os.path.join(root, path), 'rb') as handle:
            return ('file', value.st_mode & 0o111, hashlib.sha256(handle.read()).hexdigest())
    return ('other', stat.S_IFMT(value.st_mode))


class Observation:
    """One observation of the working copy: tree, residual state and exact tree entries."""

    def __init__(self, root, base):
        self.root = str(root)
        self.base = base
        index = G.index_entries(root)
        head = G.tree_entries(root, 'HEAD')
        baseline = G.tree_entries(root, base)
        if G.paths(baseline, head, index):
            raise al_snapshot.Unavailable('gitlinks are observed by the records themselves')
        self.tree = al_snapshot.snapshot(root)
        self.head = al_snapshot._run(root, ['rev-parse', '--verify', 'HEAD^{commit}'], timeout=30).strip()
        from al_scope import git_bytes
        # The same untracked listing the records use (repository, info and global excludes).
        untracked = {os.fsdecode(raw.rstrip(b'/')) for raw in git_bytes(
            'ls-files', '--others', '--exclude-standard', '-z', root=root).split(b'\0') if raw}
        self.index = {os.fsdecode(raw): entry for raw, entry in index.items()}
        candidates = set(self.index) | untracked | {os.fsdecode(raw) for raw in (*head, *baseline)}
        self.untracked = untracked
        tree = al_snapshot.entries(root, self.tree)
        convertible = al_snapshot.convertible(root, sorted(p for p, row in tree.items() if row[0] in _FILE_MODES))
        self.exact = {p: row for p, row in tree.items()
                      if (row[0] in _FILE_MODES and p not in convertible) or row[0] == '120000'}
        self.modes, self.direct = {}, {}
        for path in sorted(candidates):
            try:
                self.modes[path] = stat.S_IMODE(os.lstat(os.path.join(self.root, path)).st_mode)
            except (FileNotFoundError, NotADirectoryError):
                self.modes[path] = None
            if path not in self.exact:
                self.direct[path] = _direct(self.root, path)

    def value(self):
        """Everything `changed` compares, as JSON data."""
        return {'root': self.root, 'base': self.base, 'tree': self.tree, 'head': self.head.decode(),
                'index': self.index, 'untracked': sorted(self.untracked), 'modes': self.modes,
                'direct': {p: list(v) for p, v in self.direct.items()}}

    def key(self):
        """Digest of everything this observation saw: equal keys, equal working-copy state."""
        if not hasattr(self, '_key'):
            self._key = _digest(self.value())
        return self._key

    def changed(self, other):
        """Paths whose observed state differs from `other` (None: a global change)."""
        if self.head != other.head:
            return None
        paths = set()
        if self.tree != other.tree:
            raw = al_snapshot._run(self.root, ['diff-tree', '-r', '--no-renames', '--name-only', '-z',
                                              self.tree, other.tree], timeout=120)
            paths.update(os.fsdecode(p) for p in raw.split(b'\0') if p)
        for mine, theirs in ((self.index, other.index), (self.modes, other.modes), (self.direct, other.direct)):
            paths.update(p for p in mine.keys() | theirs.keys() if mine.get(p) != theirs.get(p))
        paths.update(self.untracked ^ other.untracked)
        return paths


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Recorded:
    """An Observation read back from its `value()` (compares like the original)."""

    changed = Observation.changed

    def __init__(self, value):
        self.root, self.base, self.tree = value['root'], value['base'], value['tree']
        self.head = value['head'].encode()
        self.index, self.modes = value['index'], value['modes']
        self.untracked = set(value['untracked'])
        self.direct = {p: tuple(v) for p, v in value['direct'].items()}


def open_observation(base):
    """The opening Observation, or None when the snapshot is unavailable (read twice instead)."""
    try:
        return Observation(al_store.REPO_ROOT, base)
    except (al_snapshot.Unavailable, OSError, ValueError):
        return None


# Candidate records read inside a bracket whose closing observation equalled its opening one,
# by (root, base, observation key). The records are a function of what an Observation sees
# (content, modes, index, HEAD, untracked membership; gitlinks are never observed this way), so
# a later bracket opening on an equal observation reuses them instead of reading 17k files, and
# one opening on a different observation re-reads only the paths whose observed state differs
# (`Observation.changed`). The latest confirmed records of a worktree and base are also kept
# in the run's runtime directory, so the next command starts from them.
_RECORDS = {}
_BRACKET = contextvars.ContextVar('al_attribution_bracket', default=None)


@contextlib.contextmanager
def bracket(opening):
    """Inside, the working copy is taken to be `opening` (its tree vouches for content and
    `records` may answer from an equal earlier observation). The caller must call `confirm`
    with the yielded state only after a closing observation equalled `opening`."""
    state = {'root': opening.root, 'base': opening.base, 'key': opening.key(), 'pending': None,
             'opening': opening, 'stored': None}
    token = _BRACKET.set(state)
    try:
        with al_snapshot.reusing(opening.root, opening.tree):
            yield state
    finally:
        _BRACKET.reset(token)


def active(root, base):
    """The enclosing bracket's state when it observes `root` against `base`, else None."""
    state = _BRACKET.get()
    if state is None or state['root'] != str(root) or state['base'] != base:
        return None
    return state


def _enabled():
    import al_analysis_index as index
    return index.enabled()


def _stored_path(root, base):
    return al_store.RUNTIME_DIR / 'observed-records' / (_digest([str(root), base]) + '.json')


def _code():
    import al_analysis_index as index
    return index.code_digest('al_scope', 'al_attribution_tree')


def _load(root, base):
    """(Recorded observation, its key, records) last confirmed for this worktree and base, or None."""
    path = _stored_path(root, base)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid():
            return None
        value = json.loads(path.read_bytes())
        observation = value['observation']
        if (value.get('code') != _code() or observation['root'] != str(root) or observation['base'] != base
                or _digest(observation) != value['key'] or not isinstance(value['records'], list)):
            return None
        return Recorded(observation), value['key'], value['records']
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _readonly():
    """A read-only inspection writes nothing, not even this cache."""
    from al_analysis_cache import CURRENT
    context = CURRENT.get()
    return context is not None and context.readonly_git


def _store(state, rows):
    import tempfile
    if _readonly():
        return
    path = _stored_path(state['root'], state['base'])
    try:
        al_store._ensure_runtime_dir()
        path.parent.mkdir(mode=0o700, exist_ok=True)
        if path.parent.is_symlink():
            return
        data = json.dumps({'code': _code(), 'key': state['key'], 'observation': state['opening'].value(),
                           'records': rows}, sort_keys=True, separators=(',', ':')).encode()
        fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.tmp.', suffix='.json')
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except (OSError, ValueError, TypeError):
        return


def records(root, base, compute):
    """Candidate records, from an equal confirmed observation when the enclosing bracket has one.

    `compute(reuse)` reads the records; `reuse` is None or (records by path, changed paths):
    a record of an unchanged path is taken over instead of read again."""
    state = active(root, base)
    if state is None or not _enabled():
        return compute(None)
    key = (state['root'], base, state['key'])
    from al_analysis_cache import data_copy
    if key in _RECORDS:
        return data_copy(_RECORDS[key])
    stored = _load(state['root'], base)
    reuse = None
    if stored is not None:
        recorded, recorded_key, rows = stored
        if recorded_key == state['key']:
            state['pending'] = (key, data_copy(rows), False)
            return rows
        try:
            changed = recorded.changed(state['opening'])
        except (al_snapshot.Unavailable, OSError, ValueError):
            changed = None
        if changed is not None:
            reuse = ({r['path']: r for r in rows}, changed)
    value = compute(reuse)
    state['pending'] = (key, data_copy(value), True)
    return value


def window(root):
    """The enclosing bracket's state when it observes `root` (any base), else None."""
    state = _BRACKET.get()
    return state if state is not None and state['root'] == str(root) else None


def physical_modes(root):
    """Permission bits of every candidate path as the enclosing bracket observed them, or None.

    Inside a bracket the working copy is its opening observation (the closing one proves it),
    so a record's physical mode is the observed one instead of another lstat of every path."""
    state = window(root)
    return state['opening'].modes if state is not None else None


def confirm(state):
    """The bracket's closing observation equalled its opening: its records are reusable."""
    if state is not None and state.get('pending') is not None:
        key, value, new = state['pending']
        if len(_RECORDS) >= 4:
            _RECORDS.clear()
        _RECORDS[key] = value
        if new:
            _store(state, value)


def stable(opening, base, ignored, state=None):
    """True when nothing outside `ignored` moved since `opening`; None when the closing
    observation is unavailable (the caller then compares a second read of the records).
    With the `bracket` state, records read inside are confirmed when nothing moved at all."""
    try:
        closing = Observation(al_store.REPO_ROOT, base)
    except (al_snapshot.Unavailable, OSError, ValueError):
        return None
    moved = opening.changed(closing)
    if moved == set():
        confirm(state)
    return moved is not None and all(_owned(p, ignored) for p in moved)


def tree_backed(opening, rows):
    """Paths of file/symlink records whose content and mode the opening tree holds exactly."""
    wanted = {r['path']: opening.exact[r['path']] for r in rows
              if r['worktree']['type'] in {'file', 'symlink'} and r['path'] in opening.exact}
    digests = al_snapshot.blob_sha256(opening.root, [e[2] for e in wanted.values()],
                                      sizes={e[2]: e[3] or 0 for e in wanted.values()})
    out = set()
    for row in rows:
        entry = wanted.get(row['path'])
        if entry is None or entry[2] not in digests:
            continue
        work = row['worktree']
        if work['type'] == 'file':
            same = entry[0] in _FILE_MODES and (entry[0] == '100755') == bool(work.get('executable')) \
                and digests[entry[2]] == work['sha256']
        else:
            same = entry[0] == '120000' and digests[entry[2]] == hashlib.sha256(os.fsencode(work['text'])).hexdigest()
        if same:
            out.add(row['path'])
    return out


def pin(st, tree):
    al_snapshot.pin(st['run_id'], tree, tree)


def require(tree):
    """A tree holding retained content must still be in the object store."""
    key = (str(al_store.REPO_ROOT), tree)
    if key in _EXISTS:
        return
    try:
        al_snapshot._run(al_store.REPO_ROOT, ['cat-file', '-e', tree + '^{tree}'], timeout=30)
    except (al_snapshot.Unavailable, OSError) as exc:
        from al_schema import refuse
        refuse('retained attribution snapshot tree is unavailable: ' + str(exc))
    _EXISTS.add(key)
