"""#55 stage 2: one readiness assessment replays an unchanged run state's history once.

Uses the real retained-artifact fallback (no attribution-tree observation window, as with a
gitlink), where every classification would otherwise replay the whole contribution history.
"""
import copy
import os

import pytest
import al_artifacts as A
import al_store
import al_task_attribution as T
import al_task_history_memo as H
from test_task_attribution_content_memo import corrupt_same_size, history  # noqa: F401


@pytest.fixture
def replays(monkeypatch):
    calls = []
    original = T._replay

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(T, '_replay', counted)
    return calls


def uncached(st):
    os.environ['AL_ANALYSIS_CACHE'] = '0'
    try:
        return T.classify_changes(st)
    finally:
        del os.environ['AL_ANALYSIS_CACHE']


def test_one_assessment_replays_history_once_with_identical_results(history, replays):
    st, _, _ = history
    expected = uncached(st)
    replays.clear()
    with H.assessment() as owner:
        results = [T.classify_changes(st) for _ in range(3)]
    assert len(replays) == 1 and owner.stores == 1 and owner.hits == 2
    assert results == [expected] * 3
    # Every call returns independent collections: mutating one cannot leak into the next.
    results[0]['task_owned'].append('mutated')
    assert results[1]['task_owned'] == expected['task_owned']


def test_current_source_is_observed_on_every_reuse(history, replays):
    st, _, _ = history
    with H.assessment() as owner:
        before = T.classify_changes(st)
        (al_store.REPO_ROOT / 'svc/app.py').write_text('x = 3\n')
        after = T.classify_changes(st)
    assert owner.hits == 1 and len(replays) == 1
    assert after == uncached(st) and after != before


@pytest.mark.parametrize('target', ['retained-content', 'contribution-record'])
def test_reuse_refuses_an_enrolled_artifact_altered_after_the_first_classification(
        history, target):
    st, record, content = history
    reference = content if target == 'retained-content' else st['task_attribution']['contributions'][0]
    with H.assessment():
        T.classify_changes(st)
        corrupt_same_size(reference)
        with pytest.raises(SystemExit):
            T.classify_changes(st)


def test_reuse_refuses_a_deleted_enrolled_artifact(history):
    st, _, content = history
    with H.assessment():
        T.classify_changes(st)
        os.unlink(content['stored_path'])
        with pytest.raises(SystemExit, match='unreadable historical attribution dependency'):
            T.classify_changes(st)


def test_a_changed_state_recomputes_and_never_hits(history, replays):
    st, _, _ = history
    changed = copy.deepcopy(st)
    changed['task_attribution']['reconciliations'] = list(changed['task_attribution'].get('reconciliations', []))
    changed['writers'] = copy.deepcopy(st['writers'])
    changed['writers'][next(iter(changed['writers']))]['note'] = 'state changed'
    with H.assessment() as owner:
        T.classify_changes(st)
        T.classify_changes(changed)
        T.classify_changes(st)
    assert len(replays) == 3 and owner.hits == 0


def test_no_reuse_when_disabled_outside_or_across_assessments(history, replays, monkeypatch):
    st, _, _ = history
    T.classify_changes(st)
    T.classify_changes(st)
    assert len(replays) == 2  # outside an assessment
    with H.assessment():
        T.classify_changes(st)
    with H.assessment() as second:
        T.classify_changes(st)
    assert len(replays) == 4 and second.hits == 0  # consecutive assessments share nothing
    monkeypatch.setenv('AL_ANALYSIS_CACHE', '0')
    with H.assessment() as disabled:
        T.classify_changes(st)
        T.classify_changes(st)
    assert len(replays) == 6 and disabled.hits == disabled.stores == 0


def test_nested_assessments_share_one_owner(history, replays):
    st, _, _ = history
    with H.assessment() as outer:
        T.classify_changes(st)
        with H.assessment() as inner:
            assert inner is outer
            T.classify_changes(st)
    assert len(replays) == 1 and outer.hits == 1


def test_an_incomplete_dependency_recording_is_never_stored(history, replays, monkeypatch):
    st, _, _ = history
    expected = uncached(st)
    replays.clear()
    monkeypatch.setattr(H, 'MAX_DEPENDENCIES', 1)
    with H.assessment() as owner:
        assert [T.classify_changes(st) for _ in range(2)] == [expected] * 2
    assert len(replays) == 2 and owner.stores == owner.hits == 0


def test_explain_ambiguity_reuses_the_history_with_identical_causes(history, replays):
    from test_task_attribution import contribution
    st, _, _ = history
    # An unregistered edit between two writers of the same file: the replay itself records why
    # the path is ambiguous, so the explanation depends on the history's causes.
    (al_store.REPO_ROOT / 'svc/app.py').write_text('x = 7\n')
    contribution(st, ['svc'], lambda: (al_store.REPO_ROOT / 'svc/app.py').write_text('x = 8\n'), wid='second')
    os.environ['AL_ANALYSIS_CACHE'] = '0'
    try:
        expected = T.explain_ambiguity(st)
    finally:
        del os.environ['AL_ANALYSIS_CACHE']
    assert expected['overlaps'], 'the fixture must produce a replay-recorded ambiguity'
    replays.clear()
    with H.assessment() as owner:
        T.classify_changes(st)
        first = T.explain_ambiguity(st)
        second = T.explain_ambiguity(st)
    assert len(replays) == 1 and owner.hits == 2
    assert first == second == expected


def test_a_missing_enrolled_snapshot_tree_refuses_on_reuse(history):
    st, _, _ = history
    with H.assessment() as owner:
        key = owner.state_key(st)
        dependencies = H.Dependencies()
        dependencies.tree('f' * 40)  # an object id absent from the repository
        owner.store(st, key, H.encoded({'unused': True}), dependencies)
        with pytest.raises(SystemExit, match='snapshot tree is unavailable'):
            owner.lookup(st)


def test_public_readiness_reuses_history_and_a_refused_reuse_saves_nothing(history, replays, monkeypatch):
    import al_implementation
    st, _, content = history
    al_implementation.require_ready(copy.deepcopy(st))
    assert len(replays) == 1  # three classifications inside one assessment
    # Tamper with retained evidence between the first classification and its reuse: the
    # readiness assessment refuses before returning, so no caller can save a result.
    original = T.classify_changes
    calls = []

    def tamper_after_first(state, *args, **kwargs):
        calls.append(1)
        result = original(state, *args, **kwargs)
        if len(calls) == 1:
            corrupt_same_size(content)
        return result

    monkeypatch.setattr(T, 'classify_changes', tamper_after_first)
    state = copy.deepcopy(st)
    snapshot = copy.deepcopy(state)
    with pytest.raises(SystemExit):
        al_implementation.require_ready(state)
    assert state == snapshot


def test_reuse_reflects_a_current_change_that_is_later_reverted(history, replays):
    # The current part adds an external edit of a writer-owned path to `ambiguous`; the stored
    # history must be the state before that, so reverting the edit is seen on reuse.
    st, _, _ = history
    path = al_store.REPO_ROOT / 'svc/app.py'
    written = path.read_text()
    with H.assessment() as owner:
        path.write_text('x = 99\n')
        edited = T.classify_changes(st)
        path.write_text(written)
        reverted = T.classify_changes(st)
    assert owner.hits == 1 and len(replays) == 1
    assert 'svc/app.py' in edited['ambiguous'] + edited['externally_changed']
    assert reverted == uncached(st)
    assert 'svc/app.py' not in reverted['ambiguous'] + reverted['externally_changed']


def test_reuse_refuses_an_altered_task_package(history):
    st, _, _ = history
    with H.assessment():
        T.classify_changes(st)
        corrupt_same_size(st['task_package'])
        with pytest.raises(SystemExit, match='immutable artifact was altered'):
            T.classify_changes(st)


def test_tree_backed_history_is_reused_with_a_private_snapshot_object_store(
        run_state, cli, monkeypatch, replays):
    # Real attribution-tree captures (no patched observation), created and reused with the
    # private snapshot object store that Harness sets for AL commands.
    from test_task_attribution import contribution
    monkeypatch.setenv('AL_SNAPSHOT_OBJECTS', 'private')
    run_state('implemented')
    st = cli.state()
    contribution(st, ['svc'], lambda: (al_store.REPO_ROOT / 'svc/app.py').write_text('x = 2\n'))
    expected = uncached(st)
    replays.clear()
    with H.assessment() as owner:
        first = T._classify(st)
        trees = [tree for tree in __import__('json').loads(owner.dependencies)[1]] if owner.dependencies else []
        second = T._classify(st)
    assert trees, 'the captures must be tree-backed for this test to mean anything'
    assert owner.hits == 1 and len(replays) == 1
    assert first == second == expected


def test_history_with_retained_unmerged_stages_is_never_reused(cli, repo, tmp_path, replays):
    # 55-HISTORY-DEPENDENCY-01: retained unmerged index stages are validated against git blob
    # content, which the reuse fence does not re-verify; such a history is always recomputed.
    from evidence_helpers import git, start_implementation
    from test_merge_conflicts import stages
    stages(repo)
    stages(repo, 'web/app.js')
    start_implementation(cli, tmp_path)
    assert cli('writer', 'start', '--id', 'repair', '--role', 'al-engineer', '--files', 'svc/app.py')[0] == 0
    (repo / 'svc/app.py').write_text('resolved\n')
    git(repo, 'add', '--', 'svc/app.py')
    assert cli('writer', 'finish', '--id', 'repair', '--outcome', 'success')[0] == 0
    st = cli.state()
    contribution = A.read_json(st['task_attribution']['contributions'][-1])
    before = A.read_json(contribution['before_capture_ref'])
    assert any('stages' in (row.get('index') or {}) for row in before['files'])
    expected = uncached(st)
    replays.clear()
    with H.assessment() as owner:
        first = T._classify(st)
        second = T._classify(st)
    assert first == second == expected
    assert owner.stores == owner.hits == 0 and len(replays) == 2


def test_one_public_command_replays_an_unchanged_history_once(history, replays, cli):
    """#55: every classification inside one guarded command shares one fenced replay."""
    st, _, _ = history
    al_store.save(st['session_id'], st)
    for command in (('gates', 'status'), ('recovery', 'next')):
        replays.clear()
        cli(*command)
        assert len(replays) == 1, command
