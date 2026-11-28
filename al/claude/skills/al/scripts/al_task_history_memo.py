"""Reuse one run state's replayed contribution history within a single readiness assessment.

An implementation-readiness assessment classifies the same run state several times (gate
currency, service coverage and the direct attribution check). When no attribution-tree
observation window is available (for example with a gitlink), each call would replay the whole
recorded contribution history. The history part of a classification depends only on the run
state and on content-addressed artifacts it references, so within one assessment it is computed
once and reused - but only after a full fence: every enrolled artifact is re-read in full
(content-addressed), every enrolled snapshot tree is checked directly in git (bypassing the
process-wide existence cache), the task revision hashes are recomputed, and the state key is
recomputed. Current-source observation is never reused. The memo lives only inside one guarded
command (al_state's dispatch opens the owner; a readiness assessment nested in it reuses that
owner): it is never global, persistent or shared across commands, and it never stores an
incomplete dependency recording. Any state change gives a new key and a fresh replay.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import subprocess
import sys

import al_artifacts as A
import al_store
from al_schema import refuse

MAX_DEPENDENCIES = 65536
MAX_BYTES = 64 * 1024 * 1024
_OWNER = ContextVar('task-readiness-history-owner', default=None)
_RECORDER = ContextVar('task-history-dependencies', default=None)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _frozen(reference):
    if not isinstance(reference, dict):
        return None
    values = tuple(reference.get(k) for k in ('stored_path', 'sha256', 'kind'))
    return values if all(type(v) is str for v in values) else None


class Dependencies:
    """Artifacts and trees a history computation actually verified, in first-use order."""

    def __init__(self):
        self.references = {}
        self.trees = {}
        self.size = 0
        self.complete = True

    def _add(self, table, key):
        if not self.complete or key in table:
            return
        size = len(encoded(list(key) if isinstance(key, tuple) else key))
        if len(self.references) + len(self.trees) >= MAX_DEPENDENCIES or self.size + size > MAX_BYTES:
            self.complete = False
            return
        table[key] = None
        self.size += size

    def reference(self, reference):
        frozen = _frozen(reference)
        if frozen is None:
            self.complete = False
        else:
            self._add(self.references, frozen)

    def tree(self, tree):
        if isinstance(tree, str):
            self._add(self.trees, tree)
        else:
            self.complete = False


def enroll_reference(reference):
    """Called by attribution readers after A.read of `reference` succeeded."""
    recorder = _RECORDER.get()
    if recorder is not None:
        recorder.reference(reference)


def exclude_recording():
    """A history input the reuse fence does not re-verify was read: never store this recording.

    Retained unmerged index stages are checked against git blob content (al_scope._blob_hashes);
    the fence re-reads artifacts and trees only, so such a history is always recomputed.
    """
    recorder = _RECORDER.get()
    if recorder is not None:
        recorder.complete = False


def enroll_tree(tree):
    """Called by attribution readers after the snapshot tree was required successfully."""
    recorder = _RECORDER.get()
    if recorder is not None:
        recorder.tree(tree)


class HistoryMemo:
    def __init__(self):
        self.origin = sys.modules.get('_al_run_pin_context')
        self.disabled = False
        self.key = self.payload = self.dependencies = None
        self.stores = self.hits = 0
        try:
            self.transaction = encoded(self.origin.transaction)
            self.root = str(al_store.REPO_ROOT.resolve())
        except (AttributeError, TypeError, ValueError, OSError):
            self.disabled = True

    def state_key(self, st):
        if self.disabled or os.environ.get('AL_ANALYSIS_CACHE', '1') == '0':
            return None
        try:
            origin = sys.modules.get('_al_run_pin_context')
            if (origin is not self.origin or encoded(origin.transaction) != self.transaction
                    or str(al_store.REPO_ROOT.resolve()) != self.root):
                self.close()
                return None
            return encoded({'state': st, 'repository': self.root, 'transaction': self.transaction.decode()})
        except (AttributeError, KeyError, TypeError, ValueError, OSError):
            self.close()
            return None

    def lookup(self, st):
        """The stored history for exactly this state, after a full fence; None when absent."""
        key = self.state_key(st)
        if key is None or key != self.key or self.payload is None:
            return None
        import al_task
        from al_writer_progress import report
        # The task package and its revision inputs are read afresh, never supplied by the memo.
        al_task.revision_hashes(st)
        references, trees = json.loads(self.dependencies)
        report('attribution-history-fence', count=0)
        for count, frozen in enumerate(references, 1):
            reference = dict(zip(('stored_path', 'sha256', 'kind'), frozen))
            try:
                A.read(reference)  # Full bytes; refuses unless they hash to the reference.
            except (OSError, ValueError, KeyError, TypeError):
                refuse('unreadable historical attribution dependency')
            report('attribution-history-fence', count=count)
        import al_snapshot
        from pathlib import Path
        for tree in trees:
            # AL's own git entry point: honours the private snapshot object store and sealed
            # control, and bypasses al_attribution_tree's process-wide existence cache.
            try:
                al_snapshot._run(Path(self.root), ['cat-file', '-e', tree + '^{tree}'], timeout=30)
            except (al_snapshot.Unavailable, OSError, subprocess.TimeoutExpired):
                refuse('historical attribution snapshot tree is unavailable')
        report('attribution-history-fence', count=len(references), force=True)
        if self.state_key(st) != key:
            return None
        self.hits += 1
        return json.loads(self.payload)

    def store(self, st, key, payload, dependencies):
        """Keep `payload` (history serialized before the caller mutated it) for this state."""
        if (key is None or not dependencies.complete or self.disabled
                or self.state_key(st) != key):
            return
        refs = encoded([[list(r) for r in dependencies.references], list(dependencies.trees)])
        if len(key) + len(payload) + len(refs) > MAX_BYTES:
            return
        self.key, self.payload, self.dependencies = key, payload, refs
        self.stores += 1

    def close(self):
        self.disabled = True
        self.key = self.payload = self.dependencies = None


@contextmanager
def assessment():
    """One readiness assessment's owner; a nested assessment reuses the outer one."""
    outer = _OWNER.get()
    if outer is not None:
        yield outer
        return
    owner = HistoryMemo()
    token = _OWNER.set(owner)
    try:
        yield owner
    finally:
        owner.close()
        _OWNER.reset(token)


def current():
    return _OWNER.get()


@contextmanager
def recording():
    recorder = Dependencies()
    token = _RECORDER.set(recorder)
    try:
        yield recorder
    finally:
        _RECORDER.reset(token)
