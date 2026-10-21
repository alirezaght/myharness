"""Fresh repository identity uses bounded batched metadata reads."""
import subprocess
from pathlib import Path

import pytest
import al_scope as S
import al_store


def command(repo, *args):
    return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True).stdout.decode().strip()


def test_identity_batches_fresh_metadata_and_preserves_result(repo, monkeypatch):
    expected = {key: command(repo, 'rev-parse', flag) for key, flag in [('common_dir', '--git-common-dir'), ('git_dir', '--git-dir')]}
    original = S.git_bytes
    calls = []
    def observe(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(S, 'git_bytes', observe)
    value = S.identity()
    assert len(calls) == 1
    assert calls[0] == ('rev-parse', '--git-common-dir', '--git-dir', '--end-of-options', 'HEAD^{commit}', 'HEAD^{commit}')
    assert value['root'] == str(repo.resolve()) and value['cwd'] == str(Path.cwd().resolve())
    for key, path in expected.items():
        assert value[key] == str((repo / path).resolve())
    assert value['target_oid'] == command(repo, 'rev-parse', 'HEAD')
    assert value['merge_base'] == value['target_oid']


def test_identity_ref_movement_and_missing_target_are_not_cached(repo):
    before = S.identity()
    command(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '--allow-empty', '-qm', 'new head')
    after = S.identity()
    assert after['target_oid'] != before['target_oid']
    assert S.identity(before['target_oid'])['merge_base'] == before['target_oid']
    with pytest.raises(SystemExit, match='git failed'):
        S.identity('refs/heads/absent-for-identity-test')


def test_linked_worktree_retains_distinct_git_directory_and_cwd_guard(repo, tmp_path, monkeypatch):
    linked = tmp_path / 'linked'
    command(repo, 'worktree', 'add', '--detach', str(linked), 'HEAD')
    original = S.identity()
    monkeypatch.chdir(linked)
    monkeypatch.setattr(al_store, 'REPO_ROOT', linked)
    current = S.identity()
    assert current['common_dir'] == original['common_dir']
    assert current['git_dir'] != original['git_dir']
    assert current['root'] != original['root']
    with pytest.raises(SystemExit, match='pinned repository/cwd mismatch'):
        S.validate_identity({'identity': original})


@pytest.mark.parametrize('response', [b'.git\ninvalid\n', b'.git\n.git\nwrong-marker\n' + b'a' * 40 + b'\n' + b'a' * 40 + b'\n', b'.git\n.git\n--end-of-options\nnot-an-oid\nnot-an-oid\n', b'.git\n.git\n--end-of-options\n' + b'a' * 40 + b'\n' + b'a' * 64 + b'\n'])
def test_malformed_batch_metadata_refuses_without_merge_query(repo, monkeypatch, response):
    calls = []
    def broken(*args, **kwargs):
        calls.append(args)
        return response
    monkeypatch.setattr(S, 'git_bytes', broken)
    with pytest.raises(SystemExit, match='invalid repository identity response'):
        S.identity()
    assert len(calls) == 1


def test_head_source_rows_read_tree_once_but_reobserve_each_boundary(repo, monkeypatch):
    original = S.G.tree_entries
    calls = []
    def observe(root, revision):
        calls.append(revision)
        return original(root, revision)
    monkeypatch.setattr(S.G, 'tree_entries', observe)
    assert S.source_rows() == []
    assert calls == ['HEAD']
    calls.clear()
    command(repo, 'update-index', '--add', '--cacheinfo', '160000', command(repo, 'rev-parse', 'HEAD'), 'child')
    rows = S.source_rows()
    assert calls == ['HEAD']
    assert len(rows) == 1 and rows[0]['path'] == 'child'
    assert rows[0]['index']['mode'] == '160000'
    assert rows[0]['base'] is None and rows[0]['head'] is None
    command(repo, 'update-index', '--force-remove', 'child')
    assert S.source_rows() == []
    assert calls == ['HEAD', 'HEAD']


def test_explicit_base_tree_is_independently_observed(repo, monkeypatch):
    base = command(repo, 'rev-parse', 'HEAD')
    original = S.G.tree_entries
    calls = []
    def observe(root, revision):
        calls.append(revision)
        return original(root, revision)
    monkeypatch.setattr(S.G, 'tree_entries', observe)
    assert S.source_rows(base=base) == []
    assert calls == ['HEAD', base]


def test_multi_revision_grammar_resolves_fresh_commits_and_rejects_invalid_targets(repo, monkeypatch, tmp_path):
    def resolve(target):
        return S.git_bytes('rev-parse', '--git-common-dir', '--git-dir', '--end-of-options', target + '^{commit}', 'HEAD^{commit}').decode().splitlines()
    before = command(repo, 'rev-parse', 'HEAD')
    value = resolve('HEAD')
    assert len(value) == 5 and value[2] == '--end-of-options' and value[3:] == [before, before]
    command(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '--allow-empty', '-qm', 'advance')
    after = command(repo, 'rev-parse', 'HEAD')
    assert resolve(before)[3:] == [before, after]
    for target in ('refs/heads/absent-for-grammar', '--git-dir', '--end-of-options', '', 'HEAD^{tree}'):
        with pytest.raises(SystemExit, match='git failed'):
            resolve(target)
    linked = tmp_path / 'grammar-linked'
    command(repo, 'worktree', 'add', '--detach', str(linked), before)
    monkeypatch.setattr(al_store, 'REPO_ROOT', linked)
    actual = resolve('HEAD')
    assert actual[2] == '--end-of-options' and actual[3:] == [before, before]
    assert (linked / actual[0]).resolve() == (repo / value[0]).resolve()
    assert (linked / actual[1]).resolve() != (repo / value[1]).resolve()


def test_different_fresh_oids_still_execute_real_merge_base_after_ref_movement(repo, monkeypatch):
    before = command(repo, 'rev-parse', 'HEAD');original = S.git_bytes;calls = []
    def observe(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            command(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '--allow-empty', '-qm', 'concurrent-before-observation')
        return original(*args, **kwargs)
    monkeypatch.setattr(S, 'git_bytes', observe)
    value = S.identity(before)
    assert len(calls) == 2 and calls[1] == ('merge-base', 'HEAD', before)
    assert value['target_oid'] == value['merge_base'] == before
    assert command(repo, 'rev-parse', 'HEAD') != before


def test_unrelated_head_retains_real_merge_base_refusal(repo, monkeypatch):
    before = command(repo, 'rev-parse', 'HEAD')
    command(repo, 'checkout', '--orphan', 'unrelated-identity-head')
    command(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'unrelated history')
    original = S.git_bytes;calls = []
    def observe(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(S, 'git_bytes', observe)
    with pytest.raises(SystemExit, match='git failed'):
        S.identity(before)
    assert calls[-1] == ('merge-base', 'HEAD', before)


def test_identity_accepts_real_sha256_repository_without_assuming_sha1(repo, tmp_path, monkeypatch):
    root = tmp_path / 'sha256';root.mkdir()
    command(root, 'init', '-q', '--object-format=sha256')
    command(root, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '--allow-empty', '-qm', 'sha256 identity')
    monkeypatch.chdir(root);monkeypatch.setattr(al_store, 'REPO_ROOT', root)
    value = S.identity()
    assert len(value['target_oid']) == 64 and value['merge_base'] == value['target_oid']


@pytest.mark.parametrize('target', ['--git-dir', '--end-of-options', '', 'HEAD^{tree}'])
def test_identity_invalid_or_option_like_target_cannot_reach_merge_base(repo, monkeypatch, target):
    original = S.git_bytes;calls = []
    before = (repo / '.git/index').read_bytes()
    def observe(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(S, 'git_bytes', observe)
    with pytest.raises(SystemExit, match='git failed'):
        S.identity(target)
    assert len(calls) == 1 and calls[0][:4] == ('rev-parse', '--git-common-dir', '--git-dir', '--end-of-options')
    assert (repo / '.git/index').read_bytes() == before
