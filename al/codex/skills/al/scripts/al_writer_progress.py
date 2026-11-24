"""Value-free, context-local diagnostics for guarded operation preparation."""
from contextlib import contextmanager
from contextvars import ContextVar
import re
import sys
import threading
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
    'inventory-imports': 'analyzing source imports',
    'inventory-references': 'finding literal references to changed artifacts',
    'inventory-provenance': 'binding scanned inputs to units',
    'inventory-boundaries': 'finding source consumers of repository boundaries',
    'inventory-coverage': 'assessing affected services and coverage',
    'inventory-ci': 'evaluating CI jobs, includes and rules',
    'inventory-relevance': 'projecting task-relevant checks',
    'inventory-bindings': 'binding check inputs and sources',
    'inventory-validate': 'revalidating the manifest against a fresh inventory',
    'inventory-verify': 'verifying the recorded capture of the validated manifest',
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
# Silent phases longer than this still print a still-working line with the elapsed time.
_HEARTBEAT = 15.0
_WRITE = threading.Lock()
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
    'conflict-resolved': 'conflict resolution', 'retarget': 'retarget',
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


_COMMAND = ContextVar('al_command_label', default=None)
_WORD = re.compile(r'[a-z][a-z0-9-]{0,31}')


def _command_label(command, action):
    """A parser-defined command/action pair, rendered only when both are plain words."""
    words = [w for w in (command, action) if w is not None]
    if not words or not all(isinstance(w, str) and _WORD.fullmatch(w) for w in words):
        return None
    return ' '.join(words)


def current_command():
    return _COMMAND.get()


@contextmanager
def command_progress(command, action=None):
    """Select only fixed labels; read-only and unknown command names stay silent."""
    operation = 'writer-'+action if command=='writer' and action in {'start','finish','reconcile'} else command
    if (command,action) in _READ_ACTIONS:
        operation=None
    token = _COMMAND.set(_command_label(command, action))
    try:
        with operation_progress(operation):
            yield
    finally:
        _COMMAND.reset(token)


def _emit(state, description, counted='', now=None):
    now = time.monotonic() if now is None else now
    elapsed = max(0.0, now - state['started'])
    with _WRITE:
        sys.stderr.write(f"AL · {state['operation']} · {description}{counted} · {elapsed:.1f}s elapsed\n")
        sys.stderr.flush()


def _heartbeat(state, stop):
    """Long phases between stage reports still show the run is alive, not stuck."""
    while not stop.wait(_HEARTBEAT):
        try:
            now = time.monotonic()
            if state['last'] is not None and now - state['last'] < _HEARTBEAT:
                continue
            description = _STAGES.get(state['stage'], 'working')
            # Items processed out of the phase total (when the phase knows it) tell a
            # slow phase from a stuck one: the count must keep moving between beats.
            counted = _counted(state.get('count'), state.get('total'))
            counted = counted + ' so far' if counted else ''
            state['last'] = now
            _emit(state, description + ' · still working', counted, now)
        except Exception:
            # A broken diagnostic stream never changes the command outcome.
            return


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
        state = {'started': time.monotonic(), 'last': None, 'stage': None, 'operation': label, 'count': None, 'total': None}
        token = _ACTIVE.set(state)
    except Exception:
        # Diagnostics cannot change the outcome when even their clock is unavailable.
        yield
        return
    stop = threading.Event()
    try:
        beat = threading.Thread(target=_heartbeat, args=(state, stop), name='al-progress-heartbeat', daemon=True)
        beat.start()
    except Exception:
        beat = None
    try:
        yield
    finally:
        stop.set()
        if beat is not None:
            beat.join(timeout=1.0)
        _ACTIVE.reset(token)


def _counted(count, total):
    if type(count) is not int or not 0 <= count <= 2**63 - 1:
        return ''
    if type(total) is int and count <= total <= 2**63 - 1:
        return f' · {count}/{total} items processed'
    return f' · {count} items processed'


def report(stage, *, count=None, total=None, force=False):
    """Emit fixed descriptions and actual bounded counts; never render caller values."""
    state = _ACTIVE.get()
    if state is None:
        return
    try:
        description = _STAGES.get(stage)
        if description is None:
            return
        now = time.monotonic()
        changed = state['stage'] != stage
        state['count'] = count if type(count) is int else None
        state['total'] = total if type(total) is int else None
        if (not force and not changed and state['last'] is not None
                and now - state['last'] < _INTERVAL):
            return
        state['stage'] = stage
        state['last'] = now
        _emit(state, description, _counted(count, total), now)
    except Exception:
        # Closed/absent/broken diagnostic streams never substitute for admission checks.
        # BaseException is deliberately not caught: cancellation still propagates.
        return


def lock_wait(holder, waited):
    """A command blocked on the run lock names the holding command, never free text.

    `holder` is the lock's holder record ({'pid', 'command', 'acquired'}) or None."""
    try:
        detail = 'held by an unidentified command'
        if isinstance(holder, dict):
            command = holder.get('command')
            command = command if isinstance(command, str) and all(_WORD.fullmatch(w) for w in command.split(' ')) and 0 < len(command) <= 65 else 'another command'
            pid = holder.get('pid')
            acquired = holder.get('acquired')
            parts = [f'`{command}`']
            if type(pid) is int and 0 < pid < 2**31:
                parts.append(f'pid {pid}')
            if isinstance(acquired, (int, float)) and not isinstance(acquired, bool):
                held = time.time() - acquired
                if 0 <= held < 10**7:
                    parts.append(f'held {held:.0f}s')
            detail = 'held by ' + parts[0] + (' (' + ', '.join(parts[1:]) + ')' if len(parts) > 1 else '')
        own = current_command()
        prefix = f'AL · {own} · ' if own else 'AL · '
        with _WRITE:
            sys.stderr.write(f'{prefix}waiting for the run admission lock · {detail} · {max(0.0, waited):.1f}s waited\n')
            sys.stderr.flush()
    except Exception:
        return
