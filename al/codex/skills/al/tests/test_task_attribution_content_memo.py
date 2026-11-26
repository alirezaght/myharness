"""Real retained bytes, local reuse and the pre-return uncached attribution fence."""
import copy
import os
from pathlib import Path

import pytest
import al_artifacts as A
import al_store
import al_task_attribution as T
from al_task_content_memo import ContentMemo
from test_task_attribution import contribution


def artifact(tmp_path, name, raw):
    path = tmp_path / name
    path.write_bytes(raw)
    return {'stored_path': str(path), 'sha256': A.digest(raw), 'kind': 'task-source-content-v1'}


@pytest.fixture
def history(run_state, cli, monkeypatch):
    run_state('implemented')
    st = cli.state()
    import al_attribution_tree
    # Exercise the real retained-artifact fallback; no snapshot authority is fabricated.
    monkeypatch.setattr(al_attribution_tree, 'open_observation', lambda base: None)
    contribution(st, ['svc'], lambda: (al_store.REPO_ROOT / 'svc/app.py').write_text('x = 2\n'))
    record = A.read_json(st['task_attribution']['contributions'][0])
    capture = A.read_json(record['before_capture_ref'])
    return st, record, capture['contents']['web/app.js']


def counted_reads(monkeypatch):
    counts = {}
    original = A.read
    def read(reference):
        key = (reference['stored_path'], reference['sha256'])
        counts[key] = counts.get(key, 0) + 1
        return original(reference)
    monkeypatch.setattr(A, 'read', read)
    return counts


def key(reference):
    return reference['stored_path'], reference['sha256']


def corrupt_same_size(reference):
    path = Path(reference['stored_path'])
    before = path.stat()
    raw = path.read_bytes()
    path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size and path.stat().st_mtime_ns == before.st_mtime_ns


def test_digest_reuse_frozen_reference_and_full_uncached_fence(tmp_path, monkeypatch):
    reference = artifact(tmp_path, 'content', b'original')
    original = dict(reference)
    counts = counted_reads(monkeypatch)
    memo = ContentMemo()
    assert memo.digest(reference) == memo.digest(reference) == original['sha256']
    assert counts[key(original)] == 1
    reference.update(artifact(tmp_path, 'other', b'other'))
    corrupt_same_size(original)
    with pytest.raises(SystemExit, match='immutable artifact was altered'):
        memo.validate()
    assert counts[key(original)] == 2


def test_same_path_different_expected_sha_is_not_a_hit(tmp_path, monkeypatch):
    reference = artifact(tmp_path, 'content', b'original')
    counts = counted_reads(monkeypatch)
    memo = ContentMemo()
    memo.digest(reference)
    different = dict(reference, sha256='0' * 64)
    with pytest.raises(SystemExit, match='immutable artifact was altered'):
        memo.digest(different)
    assert counts[key(different)] == 1


@pytest.mark.parametrize('limit', ['entries', 'bytes'])
def test_capacity_keeps_enrolled_fence_and_reads_overflow_every_time(tmp_path, monkeypatch, limit):
    first = artifact(tmp_path, 'first', b'first')
    second = artifact(tmp_path, 'second', b'second')
    cap = sum(len(os.fsencode(first[k])) for k in ('stored_path', 'sha256', 'kind'))
    memo = ContentMemo(max_entries=1) if limit == 'entries' else ContentMemo(max_reference_bytes=cap)
    counts = counted_reads(monkeypatch)
    memo.digest(first)
    memo.digest(second)
    memo.digest(second)
    assert counts[key(second)] == 2 and len(memo.entries) == 1
    corrupt_same_size(first)
    with pytest.raises(SystemExit, match='immutable artifact was altered'):
        memo.validate()
    assert counts[key(first)] == 2


def test_disabled_cache_never_reuses_or_enrolls(tmp_path, monkeypatch):
    reference = artifact(tmp_path, 'content', b'original')
    monkeypatch.setenv('AL_ANALYSIS_CACHE', '0')
    counts = counted_reads(monkeypatch)
    memo = ContentMemo()
    memo.digest(reference)
    memo.digest(reference)
    memo.validate()
    assert counts[key(reference)] == 2 and not memo.entries
    corrupt_same_size(reference)
    with pytest.raises(SystemExit, match='immutable artifact was altered'):
        memo.digest(reference)


def test_disabling_later_cannot_cancel_an_enrolled_obligation(tmp_path, monkeypatch):
    reference = artifact(tmp_path, 'content', b'original')
    memo = ContentMemo()
    memo.digest(reference)
    monkeypatch.setenv('AL_ANALYSIS_CACHE', '0')
    corrupt_same_size(reference)
    with pytest.raises(SystemExit, match='immutable artifact was altered'):
        memo.validate()


@pytest.mark.parametrize('damage', ['delete', 'replace', 'unreadable'])
def test_fence_rejects_missing_replaced_or_unreadable_dependency(tmp_path, monkeypatch, damage):
    reference = artifact(tmp_path, 'content', b'original')
    memo = ContentMemo()
    memo.digest(reference)
    target = Path(reference['stored_path'])
    if damage == 'delete':
        target.unlink()
    elif damage == 'replace':
        replacement = tmp_path / 'replacement'
        replacement.write_bytes(b'replaced')
        replacement.replace(target)
    else:
        original = A.read
        def denied(ref):
            if ref['stored_path'] == str(target):
                raise PermissionError('test-owned read denial')
            return original(ref)
        monkeypatch.setattr(A, 'read', denied)
    with pytest.raises(SystemExit, match='unreadable retained|immutable artifact was altered'):
        memo.validate()


def test_capture_reparses_manifests_and_default_remains_uncached(history, monkeypatch):
    st, record, reference = history
    counts = counted_reads(monkeypatch)
    memo = ContentMemo()
    first = T._capture(st, record['before_capture_ref'], content_memo=memo)
    second = T._capture(st, record['before_capture_ref'], content_memo=memo)
    assert first == second and first is not second
    assert counts[key(record['before_capture_ref'])] == 2 and counts[key(reference)] == 1
    memo.validate()
    assert counts[key(reference)] == 2
    T._capture(st, record['before_capture_ref'])
    T._capture(st, record['before_capture_ref'])
    assert counts[key(reference)] == 4


def test_reused_digest_still_checks_each_capture_worktree_digest(history):
    st, record, _ = history
    memo = ContentMemo()
    value = T._capture(st, record['before_capture_ref'], content_memo=memo)
    row = next(row for row in value['files'] if row['path'] == 'web/app.js')
    row['worktree']['sha256'] = '0' * 64
    inconsistent = T._save(st, value, value['kind'])
    with pytest.raises(SystemExit, match='retained attribution content differs'):
        T._capture(st, inconsistent, content_memo=memo)


@pytest.mark.parametrize('tamper', [False, True])
def test_reconciliation_only_content_joins_the_same_uncached_fence(history, monkeypatch, tamper):
    st, _, _ = history
    (al_store.REPO_ROOT / 'svc/app.py').write_text('x = 3\n')
    (al_store.REPO_ROOT / 'docs/memo-only.txt').write_text('reconciliation-only retained source\n')
    capture = T.observe(st, retain_content=True)
    reference = capture['contents']['docs/memo-only.txt']
    registry = st['task_attribution']
    data = {'schema_version': 1, 'kind': 'task-attribution-reconciliation-v1',
            'binding': T._binding(st), 'task_hash': T.al_task.task_hash(st),
            'snapshot_sha': registry['snapshot']['sha256'],
            'contribution_shas': [ref['sha256'] for ref in registry['contributions']],
            'prior_reconciliation_shas': [], 'paths': ['svc/app.py'],
            'capture_ref': T._save(st, capture, capture['kind']),
            'source': 'Explicit in-memory reconciliation decision fixture.',
            'reason': 'Retain actual externally changed source without manufacturing writer ownership.'}
    registry['reconciliations'].append(T._save(st, data, data['kind']))
    counts = counted_reads(monkeypatch)
    if tamper:
        original = T.observe
        def observed(*args, **kwargs):
            current = original(*args, **kwargs)
            corrupt_same_size(reference)
            return current
        monkeypatch.setattr(T, 'observe', observed)
        with pytest.raises(SystemExit, match='immutable artifact was altered'):
            T._classify(st)
    else:
        result = T._classify(st)
        assert not result['task_owned'] and not result['ambiguous']
        assert result['externally_changed'] == ['docs/memo-only.txt', 'svc/app.py']
    assert counts[key(reference)] == 2


@pytest.mark.parametrize('when', ['reuse', 'after-replay'])
def test_real_tamper_before_return_refuses_without_publication(history, state_file, monkeypatch, when):
    st, _, reference = history
    assert T._classify(st)['task_owned'] == ['svc/app.py']
    before_state = state_file().read_bytes()
    before = copy.deepcopy(st)
    events = []
    if when == 'reuse':
        original = ContentMemo.digest
        calls = []
        def reused(self, ref):
            result = original(self, ref)
            if key(ref) == key(reference):
                calls.append(None)
                if len(calls) == 2:
                    corrupt_same_size(reference)
                    events.append('tamper')
            return result
        monkeypatch.setattr(ContentMemo, 'digest', reused)
    else:
        original = T.observe
        def after_replay(*args, **kwargs):
            result = original(*args, **kwargs)
            corrupt_same_size(reference)
            events.append('tamper')
            return result
        monkeypatch.setattr(T, 'observe', after_replay)
    # This marker represents the caller's successful publication edge, after return.
    with pytest.raises(SystemExit, match='immutable artifact was altered'):
        result = T._classify(st)
        events.append(('published', result))
    assert events == ['tamper'] and st == before and state_file().read_bytes() == before_state


def test_nested_and_consecutive_classifications_do_not_share_digest_memos(history, monkeypatch):
    import al_task_content_memo
    st, _, reference = history
    instances = []
    class Tracked(ContentMemo):
        def __init__(self):
            super().__init__()
            instances.append(self)
    monkeypatch.setattr(al_task_content_memo, 'ContentMemo', Tracked)
    counts = counted_reads(monkeypatch)
    original = T.observe
    nested = []
    def observe(*args, **kwargs):
        current = original(*args, **kwargs)
        if not nested:
            nested.append(T._classify(st, current))
        return current
    monkeypatch.setattr(T, 'observe', observe)
    outer = T._classify(st)
    consecutive = T._classify(st)
    assert outer == consecutive == nested[0]
    assert len(instances) == 3 and len({id(m.entries) for m in instances}) == 3
    assert counts[key(reference)] == 6


@pytest.mark.parametrize('damage', ['writer', 'task', 'binding', 'reconciliation', 'source'])
def test_provenance_and_current_source_checks_still_run(history, monkeypatch, damage):
    st, _, _ = history
    assert T._classify(st)['task_owned'] == ['svc/app.py']
    if damage == 'writer':
        st['writers']['writer']['state'] = 'ACTIVE'
    elif damage == 'task':
        corrupt_same_size(st['task_package'])
    elif damage == 'binding':
        st['run_id'] = 'foreign'
    elif damage == 'reconciliation':
        st['task_attribution']['reconciliations'].append(st['task_package'])
    else:
        (al_store.REPO_ROOT / 'svc/app.py').write_text('x = 3\n')
        result = T._classify(st)
        assert result['ambiguous'] == ['svc/app.py'] and not result['task_owned']
        return
    with pytest.raises(SystemExit):
        T._classify(st)


def test_inherited_and_legacy_branches_do_not_create_a_memo(monkeypatch):
    import al_inherited_lineage
    import al_task_content_memo
    def forbidden():
        raise AssertionError('ordinary memo leaked into delegated branch')
    monkeypatch.setattr(al_task_content_memo, 'ContentMemo', forbidden)
    monkeypatch.setattr(al_inherited_lineage, 'classify', lambda st, current: {'delegated': current})
    assert T._classify({'inherited_lineage': {}}, {'fixture': True}) == {'delegated': {'fixture': True}}
    monkeypatch.setattr(T.S, 'changed_paths', lambda base: ['legacy.py'])
    assert T._classify({'identity': {'merge_base': 'base'}})['discovery_paths'] == ['legacy.py']
