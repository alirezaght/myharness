"""Value-free, context-local diagnostics for guarded operation preparation."""
from contextlib import contextmanager
from contextvars import ContextVar
import sys
import time


_STAGES = {
    'command-preflight': 'checking guarded command',
    'state-load': 'loading guarded run state',
    'state-save': 'validating and saving guarded run state',
    'state-saved': 'guarded run state saved',
    'state-lock': 'acquiring run admission lock',
    'state-locked': 'run admission lock acquired',
    'candidate-fingerprint': 'hashing current candidate identity',
    'candidate-materialization': 'materializing complete candidate evidence',
    'inventory-rules': 'discovering repository instruction rules',
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
_OPERATIONS = {
    'writer-start': 'writer start', 'writer-finish': 'writer finish',
    'writer-reconcile': 'writer reconciliation', 'start': 'start',
    'spec-gate': 'specification', 'classify': 'classification', 'phase': 'phase transition',
    'fanout': 'agent fanout', 'ownership': 'ownership', 'snapshot': 'snapshot',
    'retry': 'retry', 'agent-failed': 'agent failure', 'model-unavailable': 'model availability',
    'approved': 'approval', 'stopped': 'stop', 'clear': 'clear', 'preflight': 'preflight',
    'isolate': 'isolated check', 'rework': 'rework', 'rework-resolve': 'rework resolution',
    'rework-recovery': 'rework recovery', 'task': 'task update', 'planning': 'planning',
    'gates': 'gates', 'evidence': 'evidence', 'integration': 'execution update',
    'artifact': 'artifact', 'recovery': 'recovery', 'checkpoint': 'checkpoint',
    'resume': 'resume', 'waiting': 'wait checkpoint', 'interrupt': 'interrupt',
    'transfer': 'transfer', 'check-reconcile': 'check reconciliation',
    'agent-peer-reconcile': 'peer reconciliation', 'publication': 'publication',
    'acceptance': 'acceptance', 'review-start': 'review preparation',
    'review-packet': 'review packet', 'review-compare-start': 'review comparison',
    'review-result': 'review result', 'review-compare-result': 'review comparison result',
    'correction': 'correction', 'notes-triage': 'review notes', 'oscillation': 'correction review',
    'conflict-resolved': 'conflict resolution',
}
_READ_ACTIONS = {
    ('integration', 'capabilities'), ('integration', 'events'), ('artifact', 'show'),
    ('writer', 'handoff'), ('gates', 'status'), ('evidence', 'status'),
    ('evidence', 'require'), ('evidence', 'validate'),
    ('publication', 'packet'), ('publication', 'status'),
    ('acceptance', 'packet'), ('acceptance', 'status'),
}


@contextmanager
def writer_progress():
    """Backward-compatible writer-start scope; nested calls retain the owner clock."""
    with operation_progress('writer-start'):
        yield


@contextmanager
def command_progress(command, action=None):
    """Select only fixed labels; read-only and unknown command names stay silent."""
    operation = 'writer-'+action if command=='writer' and action in {'start','finish','reconcile'} else command
    if (command,action) in _READ_ACTIONS:
        operation=None
    with operation_progress(operation):
        yield


@contextmanager
def operation_progress(operation):
    label = _OPERATIONS.get(operation)
    if label is None:
        yield
        return
    if _ACTIVE.get() is not None:
        yield
        return
    try:
        state = {'started': time.monotonic(), 'last': None, 'stage': None, 'operation': label}
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
        sys.stderr.write(f"AL · {state['operation']} · {description}{counted} · {elapsed:.1f}s elapsed\n")
        sys.stderr.flush()
    except Exception:
        # Closed/absent/broken diagnostic streams never substitute for admission checks.
        # BaseException is deliberately not caught: cancellation still propagates.
        return
