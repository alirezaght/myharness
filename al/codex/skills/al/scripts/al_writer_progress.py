"""Value-free, in-process diagnostics enabled only for writer-start preparation."""
from contextlib import contextmanager
from contextvars import ContextVar
import sys
import time


_STAGES = {
    'preflight': 'checking writer request',
    'readiness': 'assessing current scoped implementation readiness',
    'gate-currency': 'checking current verification bindings',
    'service-coverage': 'discovering current service and check coverage',
    'attribution-classification': 'validating task contribution provenance',
    'prospective-coverage': 'checking prospective writer coverage',
    'writer-publication': 'publishing admitted writer',
    'inventory-membership': 'enumerating repository membership',
    'inventory-current': 'reading current repository inputs',
    'inventory-base': 'reading immutable baseline inputs',
    'inventory-analysis': 'analyzing repository relationships and checks',
    'inventory-stability': 'checking repository snapshot stability',
    'attribution-first': 'observing source for attribution',
    'attribution-second': 'confirming source attribution stability',
    'attribution-retain': 'retaining immutable source evidence',
    'attribution-final': 'checking retained source stability',
    'attribution-history': 'validating retained contribution evidence',
    'attribution-replay': 'replaying recorded task contributions',
    'scope-index': 'reading indexed source metadata',
    'scope-worktree': 'hashing current worktree members',
}
_ACTIVE = ContextVar('al_writer_progress', default=None)
_INTERVAL = 5.0


@contextmanager
def writer_progress():
    """Nested calls share one clock; threads and ordinary commands stay silent."""
    if _ACTIVE.get() is not None:
        yield
        return
    try:
        state = {'started': time.monotonic(), 'last': None, 'stage': None}
        token = _ACTIVE.set(state)
    except Exception:
        # Diagnostics cannot change the outcome when even their clock is unavailable.
        yield
        return
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def report(stage, *, count=None, force=False):
    """Emit fixed descriptions and actual bounded counts; never render caller values."""
    state = _ACTIVE.get()
    if state is None:
        return
    try:
        description = _STAGES.get(stage)
        if description is None:
            return
        now = time.monotonic()
        if (not force and state['stage'] == stage and state['last'] is not None
                and now - state['last'] < _INTERVAL):
            return
        state['stage'] = stage
        state['last'] = now
        counted = (f' · {count} items processed'
                   if type(count) is int and 0 <= count <= 2**63 - 1 else '')
        elapsed = max(0.0, now - state['started'])
        sys.stderr.write(f'AL · writer start · {description}{counted} · {elapsed:.1f}s elapsed\n')
        sys.stderr.flush()
    except Exception:
        # Closed/absent/broken diagnostic streams never substitute for admission checks.
        # BaseException is deliberately not caught: cancellation still propagates.
        return
