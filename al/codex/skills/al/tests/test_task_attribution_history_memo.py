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


def test_explain_ambiguity_never_uses_the_memo(history, replays):
    st, _, _ = history
    with H.assessment() as owner:
        T.classify_changes(st)
        T.explain_ambiguity(st)
    assert len(replays) == 2 and owner.hits == 0


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
    monkeypatch.setattr(al_implementation, 'classify_changes', tamper_after_first, raising=False)
    state = copy.deepcopy(st)
    snapshot = copy.deepcopy(state)
    with pytest.raises(SystemExit):
        al_implementation.require_ready(state)
    assert state == snapshot
