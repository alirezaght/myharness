"""Opt-in bounded, value-free process profiling for owned verification reports."""
from collections import defaultdict
from contextlib import contextmanager
import os
import time

TOTALS = defaultdict(float)
COUNTS = defaultdict(int)


def enabled():
    return os.environ.get('AL_PROFILE') == '1'


@contextmanager
def span(name):
    if not enabled():
        yield
        return
    start = time.monotonic()
    try:
        yield
    finally:
        TOTALS[name] += time.monotonic() - start
        COUNTS[name] += 1


def measured(name):
    def decorate(fn):
        from functools import wraps
        @wraps(fn)
        def call(*args, **kwargs):
            with span(name):
                return fn(*args, **kwargs)
        return call
    return decorate


def report():
    return {'seconds': dict(sorted(TOTALS.items())), 'calls': dict(sorted(COUNTS.items()))}


def record(name, seconds):
    if enabled():
        TOTALS[name] += seconds
        COUNTS[name] += 1
