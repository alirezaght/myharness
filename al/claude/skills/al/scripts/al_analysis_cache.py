"""Bounded process-local derived data. Cache hits never establish authority."""
import ast
from collections import OrderedDict
from contextvars import ContextVar
import copy
import hashlib
import json
import os


def content_key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _encoding(value):
    if isinstance(value, bytes):
        return value
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class AnalysisCache:
    def __init__(self, max_entries=512, max_bytes=32 * 1024 * 1024, enabled=True):
        self.max_entries, self.max_bytes, self.enabled = max_entries, max_bytes, enabled
        self.entries = OrderedDict()
        self.bytes = 0
        self.hits = self.misses = self.evictions = 0

    def clear(self):
        self.entries.clear()
        self.bytes = 0

    def _enabled(self):
        return self.enabled and os.environ.get('AL_ANALYSIS_CACHE', '1') != '0'

    def get(self, namespace, key):
        identity = (namespace, content_key(key))
        row = self.entries.get(identity) if self._enabled() else None
        if row is not None:
            try:
                data = _encoding(row['value'])
                if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != row['digest']:
                    raise ValueError('corrupt derived entry')
                value = copy.deepcopy(row['value'])
            except (ValueError, TypeError, KeyError, RecursionError):
                self._evict(identity)
            else:
                self.entries.move_to_end(identity)
                self.hits += 1
                return True, value
        self.misses += 1
        return False, None

    def _evict(self, identity):
        row = self.entries.pop(identity, None)
        if row is not None:
            if not isinstance(row, dict) or not isinstance(row.get('size'), int) or row['size'] < 0:
                self.clear()
            else:
                self.bytes = max(0, self.bytes - row['size'])
            self.evictions += 1

    def put(self, namespace, key, value):
        # Syntax trees are never shared: copying and re-encoding them costs more than parsing.
        if not self._enabled() or isinstance(value, ast.AST):
            return
        try:
            data = _encoding(value)
            if len(data) > self.max_bytes or self.max_entries < 1:
                return
            stored = copy.deepcopy(value)
        except (ValueError, TypeError, RecursionError):
            return
        identity = (namespace, content_key(key))
        self._evict(identity)
        while self.entries and (len(self.entries) >= self.max_entries or self.bytes + len(data) > self.max_bytes):
            self._evict(next(iter(self.entries)))
        self.entries[identity] = {'value': stored, 'size': len(data), 'digest': hashlib.sha256(data).hexdigest()}
        self.bytes += len(data)

    def get_or_compute(self, namespace, key, compute):
        hit, value = self.get(namespace, key)
        if hit:
            return value
        value = compute()
        self.put(namespace, key, value)
        return copy.deepcopy(value)


CACHE = AnalysisCache()
CURRENT = ContextVar('al_observation', default=None)


class ObservationContext:
    """Memoization is valid only inside one independently stability-checked read."""
    def __init__(self, binding, cache=None, *, readonly_git=False):
        self.readonly_git = readonly_git
        self.binding = copy.deepcopy(binding)
        self.cache = cache or CACHE
        self.local = {}

    def __enter__(self):
        self.token = CURRENT.set(self)
        return self

    def __exit__(self, *_):
        CURRENT.reset(self.token)

    def get_or_compute(self, namespace, key, compute):
        identity = (namespace, content_key([self.binding, key]))
        if identity not in self.local:
            self.local[identity] = copy.deepcopy(compute())
        return copy.deepcopy(self.local[identity])


def parse_python(data, memo=None):
    """Parse once per caller-owned memo; callers must treat a memoized tree as read-only."""
    if memo is None:
        return ast.parse(data)
    key = hashlib.sha256(data).hexdigest()
    if key not in memo:
        memo[key] = ast.parse(data)
    return memo[key]


class DerivedStore:
    """Small content-addressed records shared by later commands of the same repository.

    A record is the output of a deterministic analysis keyed by every input it read
    (captured content hashes plus the runtime implementation digest), so a later
    command that captured the same inputs reuses it instead of recomputing it; any
    changed input is a different key and therefore a miss. Records carry their own
    key and payload digest; an unreadable, foreign or corrupt record is a miss.
    Writes are best-effort and atomic. AL_ANALYSIS_CACHE=0 disables reads and writes."""
    KEEP = 8

    def __init__(self, namespace):
        self.namespace = namespace

    def _directory(self):
        import al_store
        return al_store.RUNTIME_DIR / 'derived' / self.namespace

    def _enabled(self):
        return os.environ.get('AL_ANALYSIS_CACHE', '1') != '0'

    def load(self, key):
        if not self._enabled():
            return None
        identity = content_key(key)
        try:
            path = self._directory() / (identity + '.json')
            if path.is_symlink() or not path.is_file():
                return None
            row = json.loads(path.read_bytes())
            if not isinstance(row, dict) or row.get('key') != identity:
                return None
            if hashlib.sha256(_encoding(row['value'])).hexdigest() != row.get('digest'):
                return None
            return row['value']
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return None

    def store(self, key, value):
        if not self._enabled():
            return
        import tempfile
        identity = content_key(key)
        try:
            directory = self._directory()
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            data = json.dumps({'key': identity, 'digest': hashlib.sha256(_encoding(value)).hexdigest(), 'value': value},
                              sort_keys=True, separators=(',', ':')).encode()
            fd, temporary = tempfile.mkstemp(dir=str(directory), prefix='.' + identity[:16] + '.', suffix='.tmp')
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(data)
                os.replace(temporary, directory / (identity + '.json'))
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            records = sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
            for stale in records[self.KEEP:]:
                stale.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError, RecursionError):
            return
