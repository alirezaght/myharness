"""What the resident AL service (#27) keeps in memory between commands.

The service forks a fresh child per command and every AL module is imported fresh in it,
so a command sees exactly the state a one-shot run would. Only these kinds of
data cross from the service into that child, all through hooks installed when
the module is first imported:

* working-copy snapshots: when the command takes a snapshot of its worktree, it asks the
  service (`state['current']`) for the tree id the service computed in the background. The
  service answers only while its file watcher proves nothing that can change the tree has
  happened since it was computed (a cookie file written into the Git directory and seen
  back through the watcher orders every earlier event first). Any doubt (an event, a
  dropped-event notice, a dead watcher, a different Git environment) means no answer, and
  the command runs `snapshot()` itself;
* the worktree's change set (#35): the paths the watcher saw change since a token the
  command took earlier, with the same cookie proof and the same doubts (an unknown token, a
  dropped-event notice, forgotten events, a dead watcher answer nothing). A command uses it
  only to show that a membership listing it took cannot have changed (see
  `al_repository_facts.served_listing`); it never stands for a file's content;
* fact-store pack headers: `al_analysis_index` packs are immutable, content-addressed
  files, so a parsed header is reused only for the same file (path, inode, size, owner).
  Entry bytes are still read and checked against their digests.

None is a result of a check and none records anything; evidence is only recorded
by a command an agent runs."""
import os
import stat

# Directory names `al_snapshot.snapshot()` excludes at any depth (al_snapshot.EXCLUDED).
EXCLUDED = ('.git', '.codex', '.claude', '.venv', 'node_modules', '__pycache__', '.pytest_cache', '.ruff_cache',
            'dist', 'build', '.next', '.turbo')
# Files in a Git directory that change what `git add -A` records (ignore rules, config).
GIT_INPUTS = ('config', 'config.worktree', 'info', 'commondir', 'gitdir')
# Environment that changes which Git runs or how it reads the working copy.
ENVIRONMENT = ('PATH', 'HOME', 'XDG_CONFIG_HOME', 'LANG', 'LC_ALL', 'LC_CTYPE')
COOKIE = 'al-service-cookie.'


def _under(path, root):
    return path == root or path.startswith(root.rstrip('/') + '/')


def relevant(path, worktree, git_dirs):
    """Whether a change at `path` can change the snapshot tree of `worktree`."""
    for directory in git_dirs:
        if _under(path, directory):
            relative = path[len(directory):].lstrip('/')
            return bool(relative) and relative.split('/', 1)[0] in GIT_INPUTS
    if not _under(path, worktree):
        return False
    relative = path[len(worktree):].lstrip('/')
    if not relative:
        return True
    return not any(part in EXCLUDED for part in relative.split('/'))


def environment_key(environ):
    """The parts of an environment a snapshot depends on."""
    return sorted((k, v) for k, v in environ.items() if k.startswith('GIT_') or k in ENVIRONMENT)


def install(module, state):
    """Hook a freshly imported module with the service's in-memory data (see module doc)."""
    if module.__name__ == 'al_snapshot':
        current = state.get('current')
        if current is None or state.get('environment') != environment_key(os.environ):
            return
        original = module.snapshot

        def snapshot(root=None):
            tree = current(str(module._root(root)))
            return tree if tree is not None else original(root)

        snapshot.__doc__ = original.__doc__
        module.snapshot = snapshot
        if state.get('changes') is not None:
            module._SERVICE_CHANGES = state['changes']
    elif module.__name__ == 'al_analysis_index':
        headers = dict(state.get('packs') or {})
        if not headers:
            return
        original = module.Pack.open.__func__

        def open_pack(cls, path):
            known = headers.get(path)
            if known is not None:
                try:
                    current = os.lstat(path)
                except OSError:
                    current = None
                if (current is not None and stat.S_ISREG(current.st_mode) and current.st_uid == os.getuid()
                        and (current.st_ino, current.st_size) == known[0]):
                    return cls(path, known[1], known[2])
            return original(cls, path)

        module.Pack.open = classmethod(open_pack)


def pack_headers(module, directory, known):
    """Parsed headers of the packs in a fact-store directory, reusing `known` ones."""
    out = {}
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith('.pack'))
    except OSError:
        return out
    for name in names:
        path = os.path.join(directory, name)
        try:
            current = os.lstat(path)
        except OSError:
            continue
        identity = (current.st_ino, current.st_size)
        if path in known and known[path][0] == identity:
            out[path] = known[path]
            continue
        pack = module.Pack.open(path)
        if pack is not None:
            out[path] = (identity, pack.entries, pack.offset)
    return out
