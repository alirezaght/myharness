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
    T.observe(st)   # the windows below open from this capture's closing observation
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
    # A later window observing the same working copy carries the answers over.
    for result in decide(st):
        _same(result, cold)
    assert len(classified) == 1 and len(bodies) == 2
    # State a records comparison cannot see (a non-executable mode) still re-runs the window,
    # and nothing is carried over from before the movement.
    del classified[:], bodies[:]
    moved = decide(st, move=lambda: os.chmod(repo / 'docs/readme.md', 0o600))
    assert len(bodies) == 2 and len(classified) >= 1
    with monkeypatch.context() as patch:
        patch.setenv('AL_ANALYSIS_CACHE', '0')
        patch.setattr(tree, 'open_observation', lambda base: None)
        cold = original(st)
    for result in moved:
        _same(result, cold)


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


def _observations(monkeypatch):
    taken = []
    real = tree.Observation

    def observation(root, base):
        taken.append(base)
        return real(root, base)
    monkeypatch.setattr(tree, 'Observation', observation)
    return taken


def test_a_bracket_opens_from_the_previous_closing_observation(cli, repo, state_file, monkeypatch):
    run(cli, 'start', '--spec', 'Open a bracket from the closing observation of the previous one.')
    st = state(state_file)
    T.observe(st)
    taken = _observations(monkeypatch)
    _same(T.observe(st, retain_content=True)['files'], _fresh(monkeypatch, st))
    assert len(taken) == 1, 'only the closing observation is taken'


def test_movement_between_brackets_retries_from_a_fresh_observation(cli, repo, state_file, monkeypatch):
    run(cli, 'start', '--spec', 'A chained bracket that finds movement starts again instead of refusing.')
    st = state(state_file)
    T.observe(st)
    (repo / 'svc/app.py').write_text('x = 77\n')              # moved after the previous bracket closed
    (repo / 'docs/readme.md').chmod(0o600)
    taken = _observations(monkeypatch)
    capture = T.observe(st, retain_content=True)
    _same(capture['files'], _fresh(monkeypatch, st))
    assert len(taken) == 3, 'chained attempt closing, then a fresh opening and closing'
    T._capture(st, T._save(st, capture, capture['kind']))
    # A decision window chained from that closing observation after more movement is
    # evaluated again from a fresh observation and equals a cold evaluation.
    (repo / 'svc/app.py').write_text('x = 78\n')

    @S.decision
    def decide(st):
        return T.classify_changes(st)
    result = decide(st)
    with monkeypatch.context() as patch:
        patch.setenv('AL_ANALYSIS_CACHE', '0')
        patch.setattr(tree, 'open_observation', lambda base: None)
        cold = T._classify(st)
    _same(result, cold)


def test_movement_during_a_retried_capture_still_refuses(cli, repo, state_file, monkeypatch):
    import pytest
    run(cli, 'start', '--spec', 'Movement during the capture refuses even after a chained retry.')
    st = state(state_file)
    T.observe(st)
    original, moves = T._rows, []

    def moving(st):
        moves.append(1)
        (repo / 'svc/app.py').write_text('x = %d\n' % (100 + len(moves)))
        return original(st)
    monkeypatch.setattr(T, '_rows', moving)
    with pytest.raises(SystemExit, match=T.UNSTABLE_OBSERVATION):
        T.observe(st, retain_content=True)
    assert len(moves) == 2, 'one chained attempt, one fresh attempt'


def test_path_helpers_equal_pathlib():
    from pathlib import Path
    import al_repository_facts as F
    for case in ['a', 'a/b', 'a/b/', 'a//b', '/a', '.', './a', 'a/./b', 'a/.', '', '/', 'a/..', '..', '.gitignore',
                 'dir/', 'x/.git/', 'a b/c.py', 'services/a/package.json']:
        assert F.path_parts(case) == Path(case).parts, case
        assert F.path_name(case) == Path(case).name, case
