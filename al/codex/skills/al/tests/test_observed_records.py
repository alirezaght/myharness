"""Candidate records reused across observations and decision windows bracketed by them (#25).

Every reuse is checked against the records read afresh with the stores disabled."""
import json
import os

import al_attribution_tree as tree
import al_scope as S
import al_store
import al_task_attribution as T
from test_attribution_tree import _git, _states
from test_task_attribution import run, state


_COUNTING = []


def _fresh(monkeypatch, st):
    """The capture as a cold, cache-disabled run reads it."""
    with monkeypatch.context() as patch:
        patch.setenv('AL_ANALYSIS_CACHE', '0')
        patch.setattr(tree, 'open_observation', lambda base: None)
        _COUNTING.append(False)
        try:
            return T.observe(st)['files']
        finally:
            _COUNTING.pop()


def _reads(monkeypatch):
    """Paths whose record was read rather than taken over, per `_records` call."""
    calls = []
    original = S._records

    def counted(base, root=None, reuse=None):
        rows = original(base, root, reuse)
        taken = reuse[0] if reuse else {}
        if not _COUNTING:
            calls.append(sorted(r['path'] for r in rows if taken.get(r['path']) is not r))
        return rows
    monkeypatch.setattr(S, '_records', counted)
    return calls


def _same(a, b):
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_records_are_reused_exactly_across_observations(cli, repo, state_file, monkeypatch):
    run(cli, 'start', '--spec', 'Reuse candidate records only for unchanged observed state.')
    st = state(state_file)
    reads = _reads(monkeypatch)
    first = T.observe(st)['files']
    _same(first, _fresh(monkeypatch, st))
    # The same process and an equal observation: no read at all.
    _same(T.observe(st)['files'], first)
    # A new process (memory cleared) starts from the records stored in the runtime directory.
    tree._RECORDS.clear()
    _same(T.observe(st)['files'], first)
    # `start` stored the records of its own capture: none was read since.
    assert reads == []
    # Every kind of change re-reads exactly the changed paths and equals a cold read.
    _states(repo)
    changed = T.observe(st)['files']
    _same(changed, _fresh(monkeypatch, st))
    assert len(reads) == 1 and 0 < len(reads[-1]) < len(changed), reads[-1]
    assert {'svc/app.py', 'svc/new.py', 'web/app.js', 'docs/notes.txt'} <= set(reads[-1])
    tree._RECORDS.clear()
    (repo / 'docs/readme.md').chmod(0o644)
    _same(T.observe(st)['files'], _fresh(monkeypatch, st))


def test_a_new_head_or_a_corrupt_store_reads_every_record(cli, repo, state_file, monkeypatch):
    run(cli, 'start', '--spec', 'Read every candidate record when HEAD moved or the store is unusable.')
    st = state(state_file)
    reads = _reads(monkeypatch)
    T.observe(st)
    (repo / 'svc/app.py').write_text('x = 3\n')
    _git(repo, 'commit', '-qam', 'move head')
    tree._RECORDS.clear()
    rows = T.observe(st)['files']
    assert len(reads[-1]) == len(rows)
    _same(rows, _fresh(monkeypatch, st))
    for stored in (al_store.RUNTIME_DIR / 'observed-records').glob('*.json'):
        value = json.loads(stored.read_text())
        value['records'][0]['worktree'] = {'type': 'deleted'}
        value['observation']['tree'] = '0' * 40   # forged: the key no longer matches
        stored.write_text(json.dumps(value))
    tree._RECORDS.clear()
    _same(T.observe(st)['files'], _fresh(monkeypatch, st))
    assert len(reads[-1]) == len(rows)


def test_decision_window_classifies_once_and_reruns_on_movement(cli, repo, state_file, monkeypatch):
    run(cli, 'start', '--spec', 'Classify once per decision window and re-run it when anything moved.')
    st = state(state_file)
    (repo / 'svc/app.py').write_text('x = 9\n')
    classified, bodies = [], []
    original = T._classify
    monkeypatch.setattr(T, '_classify', lambda *a, **k: classified.append(1) or original(*a, **k))

    @S.decision
    def decide(st, move=None):
        bodies.append(1)
        results = [T.classify_changes(st) for _ in range(3)]
        if move and len(bodies) == 1:
            move()
        return results
    results = decide(st)
    assert len(classified) == 1 and len(bodies) == 1
    with monkeypatch.context() as patch:
        patch.setenv('AL_ANALYSIS_CACHE', '0')
        patch.setattr(tree, 'open_observation', lambda base: None)
        cold = original(st)
    for result in results:
        _same(result, cold)
    # State a records comparison cannot see (a non-executable mode) still re-runs the window.
    del classified[:], bodies[:]
    decide(st, move=lambda: os.chmod(repo / 'docs/readme.md', 0o600))
    assert len(bodies) == 2


def test_membership_matching_equals_pattern_matches():
    from al_ci_rules import pattern_matches
    from al_repository_capture import CaptureMixin
    members = ['AGENTS.md', 'ci/', 'ci/a.yml', 'ci/x/b.yml', 'ci/x/b.yaml', 'services/', 'services/a/',
               'services/a/AGENTS.md', 'services/a/package.json', '.github/workflows/w.yml', 'a.yml', 'ci.yml',
               '.husky/pre-commit', 'Makefile', 'x/Makefile', 'weird[1].yml', 'docs/$x.md']
    patterns = ['AGENTS.md', 'ci/**/*.yml', '**/*.yml', '*.yml', '.github/workflows/*.yml', 'services/*/AGENTS.md',
                '**', 'ci/*', 'Make?ile', 'weird[1].yml', 'docs/$x.md', '.woodpecker/*.yml', '']
    for pattern in patterns:
        expected = tuple(sorted(p.rstrip('/') for p in members if pattern_matches(pattern, p.rstrip('/')) is True))
        assert CaptureMixin.matched(pattern, members) == expected, pattern
