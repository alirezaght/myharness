"""Bounded process-local derived data. Cache hits never establish authority."""
import ast
from collections import OrderedDict
from contextvars import ContextVar
import copy
import hashlib
import json
import os
import sys


def content_key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _encoding(value):
    if isinstance(value, ast.AST):
        return ast.dump(value, include_attributes=True).encode()
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
        if not self._enabled():
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


def parse_python(data):
    key = [hashlib.sha256(data).hexdigest(), list(sys.version_info[:3]), 'ast-parse-v1']
    return CACHE.get_or_compute('python-ast', key, lambda: ast.parse(data))
