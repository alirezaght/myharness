"""Attribution captures bracketed by snapshot trees equal the double-read captures (#25)."""
import json
import os
import subprocess

import pytest

import al_attribution_tree as tree
import al_snapshot
import al_task_attribution as T
from test_task_attribution import run, state


def _git(repo, *args):
    subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', *args], cwd=repo, check=True,
                   capture_output=True)


def _legacy(monkeypatch, st, **kwargs):
    """The capture as observed before #25: the records read twice (three times retained)."""
    with monkeypatch.context() as patch:
        patch.setattr(tree, 'open_observation', lambda base: None)
        return T.observe(st, **kwargs)


def _content(repo, capture, path):
    """Retained bytes of `path`: its artifact, or its blob in the capture's snapshot tree."""
    import al_artifacts as A
    if path in capture['contents']:
        return A.read(capture['contents'][path])
    return subprocess.run(['git', 'cat-file', 'blob', capture['snapshot_tree'] + ':' + path], cwd=repo,
                          capture_output=True, check=True).stdout


def _states(repo):
    """Working-copy changes of every kind the records distinguish."""
    (repo / 'svc/app.py').write_text('x = 2\n')                       # modified tracked
    (repo / 'svc/new.py').write_text('new = 1\n')                     # untracked member
    os.unlink(repo / 'web/app.js')                                     # deleted tracked
    (repo / 'docs/readme.md').chmod(0o600)                             # physical (non-executable) mode
    (repo / 'svc/run.sh').write_text('#!/bin/sh\n')
    (repo / 'svc/run.sh').chmod(0o755)                                 # executable untracked
    os.symlink('app.py', repo / 'svc/link.py')                         # symlink
    (repo / 'svc/__pycache__').mkdir()
    (repo / 'svc/__pycache__/x.pyc').write_bytes(b'\0')               # ignored
    (repo / 'node_modules/pkg').mkdir(parents=True)
    (repo / 'node_modules/pkg/index.js').write_text('1\n')            # untracked outside AL membership
    (repo / 'svc/tests/fixtures/rows.json').write_text('[1]\n')
    _git(repo, 'add', 'svc/tests/fixtures/rows.json')                  # staged change
    (repo / 'svc/tests/fixtures/rows.json').write_text('[2]\n')       # and a worktree change on top
    (repo / '.gitattributes').write_text('*.txt text eol=crlf\n')
    (repo / 'docs/notes.txt').write_bytes(b'a\nb\n')                  # converted by attributes


@pytest.mark.parametrize('retain', [False, True])
def test_tree_bracketed_capture_equals_double_read_capture(cli, repo, state_file, monkeypatch, retain):
    run(cli, 'start', '--spec', 'Compare attribution captures taken through snapshot trees and direct reads.')
    st = state(state_file)
    _states(repo)
    legacy = _legacy(monkeypatch, st, retain_content=retain)
    current = T.observe(st, retain_content=retain)
    assert current['files'] == legacy['files']
    assert json.dumps(current['files'], sort_keys=True) == json.dumps(legacy['files'], sort_keys=True)
    if retain:
        retained = [r['path'] for r in legacy['files'] if r['worktree']['type'] in {'file', 'symlink'}]
        assert set(legacy['contents']) == set(retained)
        # Tree-backed content is exactly the retained artifact content of the double read.
        assert current['snapshot_tree'] and set(current['contents']) < set(retained)
        for path in retained:
            assert _content(repo, current, path) == _content(repo, legacy, path), path
        # Converted files and files outside AL's membership stay artifacts.
        assert {'docs/notes.txt', 'node_modules/pkg/index.js'} <= set(current['contents'])
        T._capture(st, T._save(st, current, current['kind']))


def test_classification_is_unchanged_by_tree_backed_contributions(cli, repo, state_file, monkeypatch):
    run(cli, 'start', '--spec', 'Compare contribution attribution through snapshot trees and direct reads.')
    st = state(state_file)
    (repo / 'docs/readme.md').write_text('external\n')
    writer = {'id': 'w', 'files': ['svc'], 'state': 'ACTIVE'}
    before = T.begin_contribution(st, writer)
    st['writers']['w'] = writer
    (repo / 'svc/app.py').write_text('x = 5\n')
    (repo / 'web/app.js').write_text('let a = 5\n')
    writer.update(state='FINISHED', outcome='success')
    T.finish_contribution(st, writer, before)
    current = T.classify_changes(st)
    with monkeypatch.context() as patch:
        patch.setattr(tree, 'open_observation', lambda base: None)
        legacy = T.classify_changes(st)
    assert current == legacy
    assert current['task_owned'] == ['svc/app.py']
    assert current['externally_changed'] == ['docs/readme.md', 'web/app.js']


def _moving(monkeypatch, repo, path):
    original = T._rows
    def rows(st):
        (repo / path).write_text('moved\n')
        return original(st)
    monkeypatch.setattr(T, '_rows', rows)


@pytest.mark.parametrize('path', ['svc/app.py', 'docs/readme.md', 'svc/fresh.py'])
def test_movement_during_the_capture_is_torn(cli, repo, state_file, monkeypatch, path):
    run(cli, 'start', '--spec', 'Refuse attribution captures torn by working-copy movement.')
    st = state(state_file)
    _moving(monkeypatch, repo, path)
    tree._LAST.clear()   # a fresh opening observation: movement during the capture itself
    with pytest.raises(SystemExit, match=T.UNSTABLE_OBSERVATION):
        T.observe(st, retain_content=True)


def test_movement_outside_membership_and_in_the_index_is_torn(cli, repo, state_file, monkeypatch):
    run(cli, 'start', '--spec', 'Refuse attribution captures torn by state a snapshot tree does not hold.')
    st = state(state_file)
    (repo / 'node_modules').mkdir()
    original = T._rows
    def staging(st):
        rows = original(st)
        _git(repo, 'add', 'node_modules/x.js')
        return rows
    def chmod(st):
        rows = original(st)
        (repo / 'docs/readme.md').chmod(0o600)
        return rows
    def moving(st):
        (repo / 'node_modules/x.js').write_text('moved\n')
        return original(st)
    for rows in (moving, staging, chmod):
        tree._LAST.clear()   # a fresh opening observation: movement during the capture itself
        with monkeypatch.context() as patch:
            patch.setattr(T, '_rows', rows)
            with pytest.raises(SystemExit, match=T.UNSTABLE_OBSERVATION):
                T.observe(st)


def test_sibling_ownership_may_move_during_the_capture(cli, repo, state_file, monkeypatch):
    run(cli, 'start', '--spec', 'Let a parallel sibling writer edit its own paths during a capture.')
    st = state(state_file)
    _moving(monkeypatch, repo, 'web/app.js')
    capture = T.observe(st, retain_content=True, concurrent=['web'])
    assert not any(r['path'].startswith('web/') for r in capture['files'])
    st['writers']['sib'] = {'id': 'sib', 'files': ['web'], 'state': 'ACTIVE'}
    assert T.observe(st)['files']


def test_unavailable_closing_snapshot_falls_back_to_a_second_read(cli, repo, state_file, monkeypatch):
    run(cli, 'start', '--spec', 'Fall back to a second read when the closing snapshot is unavailable.')
    st = state(state_file)
    calls = []
    original = T._rows
    monkeypatch.setattr(T, '_rows', lambda st: calls.append(1) or original(st))
    opened = []
    real = tree.Observation
    def observation(root, base):
        if opened:
            raise al_snapshot.Unavailable('busy')
        opened.append(1)
        return real(root, base)
    monkeypatch.setattr(tree, 'Observation', observation)
    tree._LAST.clear()
    assert T.observe(st)['files'] == original(st)
    assert len(calls) == 2


def test_single_read_with_a_snapshot_tree(cli, repo, state_file, monkeypatch):
    run(cli, 'start', '--spec', 'Read the records once when snapshot trees bracket the capture.')
    st = state(state_file)
    calls = []
    original = T._rows
    monkeypatch.setattr(T, '_rows', lambda st: calls.append(1) or original(st))
    T.observe(st, retain_content=True)
    assert len(calls) == 1
