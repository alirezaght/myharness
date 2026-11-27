"""Per-repository content-addressed fact store shared by every worktree and task (#25).

A fact is the output of a pure function of content-addressed inputs. Its identity is
`sha256([namespace, code digest of the producing function's module closure, key])`,
where the key holds the content hashes of every input the function read. The same
identity therefore always names the same bytes, so a reused fact equals its
recomputation.

Storage is immutable segment files ("packs") under one directory per repository,
keyed by the realpath of the git common dir, so all worktrees and tasks share it.
A command writes the facts it computed as one new pack (temp file + rename): there
are no locks, no in-place rewrites and no compaction, and concurrent writers of the
same fact race harmlessly. Eviction deletes whole least-recently-used packs; a fact
evicted while another command needs it is a miss and is recomputed. Every entry
carries the sha256 of its identity and bytes: a corrupt, foreign, symlinked or
unreadable pack or entry is a miss. Directories are 0700, files 0600.

Only immutable facts live here. Mutable state (run state, evidence, change-set
state) stays in each worktree's runtime directory. `AL_ANALYSIS_CACHE=0` disables
the store; `AL_ANALYSIS_INDEX_DIR` relocates it (tests)."""
import re
import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

SCHEMA = 1
MAGIC = b'ALFACTS1'
MAX_BYTES = 1024 * 1024 * 1024
MAX_PACKS = 96
_STORES = {}
_DIGESTS = {}


def enabled():
    import al_retry_control
    if al_retry_control.current() is not None:
        return False
    return os.environ.get('AL_ANALYSIS_CACHE', '1') != '0'


def index_root():
    from al_snapshot import operator_source
    source = operator_source()
    if source is not None:
        return source.cache_root
    configured = os.environ.get('AL_ANALYSIS_INDEX_DIR')
    if configured:
        return Path(configured)
    return Path.home() / '.local' / 'state' / 'al-distribution' / 'analysis-index'


def _limit(name, default):
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        return default
    return value if value > 0 else default


# ---------------------------------------------------------------- code digests

_SOURCES = {}
_MENTION = re.compile(rb"\bal_[A-Za-z0-9_]+")


def _modules():
    """{module name: source bytes} of the runtime's al_* modules (verified bytes when pinned)."""
    from al_runtime_resources import _CONTEXT, _ordinary
    if _CONTEXT:
        return {n[:-3]: v for n, v in _CONTEXT['bytes'].items() if n.startswith('al_') and n.endswith('.py') and '/' not in n}
    return {n[:-3]: p for n, p in _ordinary().items() if n.startswith('al_') and n.endswith('.py')}


def _module_bytes(name):
    if name not in _SOURCES:
        value = _modules()[name]
        _SOURCES[name] = value if isinstance(value, bytes) else value.read_bytes()
    return _SOURCES[name]


def _referenced(name, known):
    """al_* modules a module may load: every al_* name its source mentions (imports, string
    constants, even comments), a superset of what it can import."""
    return {m.decode() for m in _MENTION.findall(_module_bytes(name))} & known


def code_digest(*modules):
    """Digest of the producing code: the named modules, every al_* module they can load
    (transitively), the interpreter version and this store's schema."""
    key = tuple(sorted(modules))
    if key in _DIGESTS:
        return _DIGESTS[key]
    modules = _modules()
    for name, value in modules.items():
        if name not in _SOURCES:
            _SOURCES[name] = value if isinstance(value, bytes) else value.read_bytes()
    known = set(modules)
    closure, pending = set(), list(key)
    while pending:
        name = pending.pop()
        if name in closure:
            continue
        if name not in known:
            raise ValueError('unknown analyser module: ' + name)
        closure.add(name)
        pending.extend(_referenced(name, known) - closure)
    digest = hashlib.sha256()
    digest.update(json.dumps([SCHEMA, sys.implementation.name, list(sys.version_info[:3])]).encode())
    for name in sorted(closure):
        data = _module_bytes(name)
        digest.update(name.encode() + b'\0' + str(len(data)).encode() + b'\0' + data)
    _DIGESTS[key] = digest.hexdigest()
    return _DIGESTS[key]


# ---------------------------------------------------------------- encoding

def encode(value):
    """Canonical JSON bytes; raises for values JSON cannot carry exactly."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _exact(value):
    """Only values whose JSON round trip is type-exact are stored."""
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return value == value and value not in (float('inf'), float('-inf'))
    if isinstance(value, list):
        return all(_exact(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _exact(v) for k, v in value.items())
    return False


def identity(namespace, code, key):
    return hashlib.sha256(encode([namespace, code, key])).hexdigest()


# ---------------------------------------------------------------- store

def _common_dir(root):
    from al_snapshot import operator_source
    source = operator_source(root)
    if source is not None:
        return str(source.common_dir)
    result = subprocess.run(['git', '--no-optional-locks', 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                            cwd=str(root), capture_output=True, timeout=10)
    if result.returncode:
        return None
    value = result.stdout.decode().strip()
    return os.path.realpath(value) if value else None


def _private_directory(path, create):
    """An owned, private, non-symlink directory (created 0700 when asked)."""
    try:
        state = os.lstat(path)
    except FileNotFoundError:
        if not create:
            return False
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        state = os.lstat(path)
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode) or state.st_uid != os.getuid():
        return False
    if stat.S_IMODE(state.st_mode) != 0o700:
        return False
    return True


def _prepare(directory):
    """Create the store directory: ancestors of the index root ordinary, the root and below private."""
    root = index_root()
    parent = Path(os.path.realpath(root.parent))
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    current = parent / root.name
    if not _private_directory(current, True):
        return False
    for part in directory.relative_to(root).parts:
        current = current / part
        if not _private_directory(current, True):
            return False
    return True


class Pack:
    """One immutable segment: MAGIC, header length, header JSON, entry bytes."""

    def __init__(self, path, entries, offset):
        self.path, self.entries, self.offset = path, entries, offset

    @classmethod
    def open(cls, path):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return None
        try:
            state = os.fstat(fd)
            if not stat.S_ISREG(state.st_mode) or state.st_uid != os.getuid():
                return None
            with os.fdopen(fd, 'rb', closefd=False) as handle:
                head = handle.read(len(MAGIC) + 8)
                if len(head) != len(MAGIC) + 8 or head[:len(MAGIC)] != MAGIC:
                    return None
                size = struct.unpack('>Q', head[len(MAGIC):])[0]
                if size > state.st_size:
                    return None
                header = json.loads(handle.read(size))
            entries = header['entries']
            if not isinstance(entries, dict):
                return None
            return cls(path, entries, len(MAGIC) + 8 + size)
        except (OSError, ValueError, KeyError, TypeError, struct.error):
            return None
        finally:
            os.close(fd)

    def read(self, wanted):
        """{identity: bytes} for the wanted identities this pack holds and verifies."""
        rows = sorted((self.entries[i][0], i) for i in wanted if i in self.entries)
        out = {}
        if not rows:
            return out
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return out
        try:
            with os.fdopen(fd, 'rb', closefd=False) as handle:
                for start, ident in rows:
                    try:
                        _, length, digest = self.entries[ident]
                        handle.seek(self.offset + int(start))
                        data = handle.read(int(length))
                    except (OSError, ValueError, TypeError):
                        continue
                    if hashlib.sha256(ident.encode() + data).hexdigest() == digest:
                        out[ident] = data
        finally:
            os.close(fd)
        return out


class Store:
    """The fact store of one repository, as seen by one command."""

    def __init__(self, directory):
        self.directory = directory
        self.packs = {}
        self.pending = {}
        self.hits = self.misses = self.writes = 0
        self._scanned = 0.0

    def _refresh(self):
        try:
            names = sorted(n for n in os.listdir(self.directory) if n.endswith('.pack'))
        except OSError:
            names = []
        current = set(names)
        for name in list(self.packs):
            if name not in current:
                del self.packs[name]
        for name in names:
            if name not in self.packs:
                pack = Pack.open(os.path.join(self.directory, name))
                if pack is not None:
                    self.packs[name] = pack

    def get_many(self, identities):
        """{identity: bytes} found among the store's packs and this command's pending facts."""
        wanted = set(identities)
        out = {i: self.pending[i] for i in wanted if i in self.pending}
        wanted -= set(out)
        if wanted:
            self._refresh()
            used = []
            for name, pack in sorted(self.packs.items(), reverse=True):
                found = pack.read(wanted)
                if found:
                    out.update(found)
                    wanted -= set(found)
                    used.append(pack.path)
                if not wanted:
                    break
            now = time.time()
            for path in used:
                try:
                    os.utime(path, (now, now), follow_symlinks=False)
                except OSError:
                    pass
        self.hits += len(out)
        self.misses += len(set(identities)) - len(out)
        return out

    def put_many(self, items):
        for ident, data in items.items():
            self.pending.setdefault(ident, data)
        if sum(len(v) for v in self.pending.values()) > 64 * 1024 * 1024:
            self.flush()

    def flush(self):
        """Publish this command's new facts as one immutable pack, then evict."""
        if not self.pending:
            return
        rows, header, offset = [], {}, 0
        for ident in sorted(self.pending):
            data = self.pending[ident]
            header[ident] = [offset, len(data), hashlib.sha256(ident.encode() + data).hexdigest()]
            rows.append(data)
            offset += len(data)
        head = json.dumps({'schema': SCHEMA, 'entries': header}, sort_keys=True, separators=(',', ':')).encode()
        blob = MAGIC + struct.pack('>Q', len(head)) + head + b''.join(rows)
        name = hashlib.sha256(blob).hexdigest()[:40] + '.pack'
        try:
            fd, temporary = tempfile.mkstemp(dir=self.directory, prefix='.tmp.', suffix='.pack')
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(blob)
                os.replace(temporary, os.path.join(self.directory, name))
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            self.writes += len(self.pending)
        except OSError:
            pass
        self.pending = {}
        self.evict()

    def evict(self):
        """Delete least-recently-used whole packs beyond the size/count bounds (lock-free)."""
        try:
            rows = []
            for name in os.listdir(self.directory):
                path = os.path.join(self.directory, name)
                state = os.lstat(path)
                if name.startswith('.tmp.') and time.time() - state.st_mtime > 3600:
                    os.unlink(path)
                elif name.endswith('.pack') and stat.S_ISREG(state.st_mode):
                    rows.append((state.st_mtime, state.st_size, path))
        except OSError:
            return
        rows.sort(reverse=True)
        limit, count = _limit('AL_ANALYSIS_INDEX_BYTES', MAX_BYTES), _limit('AL_ANALYSIS_INDEX_PACKS', MAX_PACKS)
        total = 0
        for index, (_, size, path) in enumerate(rows):
            total += size
            if index >= count or total > limit:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def store(root=None):
    """The repository's fact store, or None when disabled or unavailable."""
    if not enabled():
        return None
    if root is None:
        import al_store
        root = al_store.REPO_ROOT
    try:
        common = _common_dir(root)
    except (OSError, subprocess.SubprocessError):
        return None
    if not common:
        return None
    directory = index_root() / hashlib.sha256(common.encode()).hexdigest()[:32]
    key = (str(index_root()), str(directory))
    if key in _STORES:
        return _STORES[key]
    try:
        ready = _prepare(directory)
    except (OSError, ValueError):
        ready = False
    value = Store(str(directory)) if ready else None
    _STORES[key] = value
    if value is not None:
        from al_snapshot import operator_source
        if operator_source(root) is None:
            import atexit
            atexit.register(value.flush)
    return value


def flush():
    for value in list(_STORES.values()):
        if value is not None:
            value.flush()


def reset():
    """Forget opened stores (tests, or after relocating the index)."""
    flush()
    _STORES.clear()


# ---------------------------------------------------------------- facts

def facts(namespace, code, keys, compute, *, root=None, compress=False):
    """Values of `namespace` facts for every key, reusing stored ones.

    `compute(missing_keys)` returns {index: value} for the positions of the missing keys
    (positions into `keys`). Every computed value that can be stored exactly is round
    tripped through its encoding, so a stored and a recomputed fact are the same value."""
    values = [None] * len(keys)
    target = store(root)
    idents = [identity(namespace, code, k) for k in keys] if target is not None else None
    found = target.get_many(set(idents)) if target is not None else {}
    missing = []
    for index in range(len(keys)):
        data = found.get(idents[index]) if idents else None
        if data is not None:
            try:
                values[index] = json.loads(zlib.decompress(data) if compress else data)
                continue
            except (ValueError, zlib.error, RecursionError):
                pass
        missing.append(index)
    if missing:
        computed = compute(missing)
        new = {}
        for index in missing:
            value = computed[index]
            if _exact(value):
                try:
                    data = encode(value)
                except (ValueError, TypeError, RecursionError):
                    data = None
                if data is not None:
                    value = json.loads(data)
                    if target is not None:
                        new[idents[index]] = zlib.compress(data, 1) if compress else data
            values[index] = value
        if target is not None and new:
            target.put_many(new)
    return values


def fact(namespace, code, key, compute, *, root=None, compress=False):
    return facts(namespace, code, [key], lambda missing: {0: compute()}, root=root, compress=compress)[0]


def lookup(namespace, code, keys, *, root=None):
    """{position: value} of the stored facts among `keys` (no computation)."""
    target = store(root)
    if target is None:
        return {}
    idents = [identity(namespace, code, k) for k in keys]
    found = target.get_many(set(idents))
    out = {}
    for index, ident in enumerate(idents):
        if ident in found:
            try:
                out[index] = json.loads(found[ident])
            except (ValueError, RecursionError):
                pass
    return out


def record(namespace, code, items, *, root=None):
    """Store computed facts given as [(key, value)]."""
    target = store(root)
    if target is None:
        return
    new = {}
    for key, value in items:
        if _exact(value):
            try:
                new[identity(namespace, code, key)] = encode(value)
            except (ValueError, TypeError, RecursionError):
                pass
    if new:
        target.put_many(new)
