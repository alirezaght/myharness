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
import hashlib
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


def open_observation(base):
    """The opening Observation, or None when the snapshot is unavailable (read twice instead)."""
    try:
        return Observation(al_store.REPO_ROOT, base)
    except (al_snapshot.Unavailable, OSError, ValueError):
        return None


def stable(opening, base, ignored):
    """True when nothing outside `ignored` moved since `opening`; None when the closing
    observation is unavailable (the caller then compares a second read of the records)."""
    try:
        closing = Observation(al_store.REPO_ROOT, base)
    except (al_snapshot.Unavailable, OSError, ValueError):
        return None
    moved = opening.changed(closing)
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
