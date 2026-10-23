"""In-process reuse of exact Git metadata reads never survives a change of its inputs."""
import subprocess

import al_git_cache
import al_gitlinks as G
import al_runtime_resources as R


def _git(repo, *args):
    return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True).stdout


def _commit(repo, name, text):
    (repo / name).write_text(text)
    _git(repo, 'add', name)
    _git(repo, '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'commit', '-qm', name)


def test_head_tree_follows_new_commits_and_packed_refs(repo):
    before = G.tree_entries(repo, 'HEAD')
    _commit(repo, 'memo-new.txt', 'one\n')
    after = G.tree_entries(repo, 'HEAD')
    assert b'memo-new.txt' in after and b'memo-new.txt' not in before
    _git(repo, 'pack-refs', '--all')
    assert G.tree_entries(repo, 'HEAD') == G.entries(_git(repo, 'ls-tree', '-r', '-z', 'HEAD'))
    _git(repo, 'checkout', '-q', '--detach', 'HEAD~1')
    assert G.tree_entries(repo, 'HEAD') == before


def test_replacement_refs_invalidate_exact_object_listings(repo):
    _commit(repo, 'memo-a.txt', 'a\n')
    first = _git(repo, 'rev-parse', 'HEAD').decode().strip()
    _commit(repo, 'memo-b.txt', 'b\n')
    second = _git(repo, 'rev-parse', 'HEAD').decode().strip()
    original = G.tree_entries(repo, first)
    _git(repo, 'replace', first, second)
    assert G.tree_entries(repo, first) == G.entries(_git(repo, 'ls-tree', '-r', '-z', first)) != original


def test_index_listing_follows_index_bytes_and_returns_private_copies(repo):
    before = G.index_entries(repo)
    before[next(iter(before))]['oid'] = 'forged'
    assert 'forged' not in {e['oid'] for e in G.index_entries(repo).values()}
    (repo / 'memo-staged.txt').write_text('staged\n')
    _git(repo, 'add', 'memo-staged.txt')
    assert b'memo-staged.txt' in G.index_entries(repo)
    listed = G.tree_entries(repo, 'HEAD')
    listed[next(iter(listed))]['mode'] = 'forged'
    assert 'forged' not in {e['mode'] for e in G.tree_entries(repo, 'HEAD').values()}


def test_object_binding_follows_repository_config(repo):
    first = al_git_cache.object_binding(repo)
    assert al_git_cache.object_binding(repo) == first
    _git(repo, 'config', 'memo.key', 'value')
    assert al_git_cache.object_binding(repo) == al_git_cache._object_binding(repo)
    first.append('forged')
    assert 'forged' not in al_git_cache.object_binding(repo)


def test_implementation_digest_follows_module_changes(tmp_path, monkeypatch):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    (scripts / 'al_one.py').write_text('VALUE = 1\n')
    monkeypatch.setattr(R, '__file__', str(scripts / 'al_runtime_resources.py'))
    monkeypatch.setattr(R, '_CONTEXT', None)
    monkeypatch.setattr(R, '_DIGEST', None)
    first = R.implementation_digest()
    assert R.implementation_digest() == first
    (scripts / 'al_one.py').write_text('VALUE = 2\n')
    second = R.implementation_digest()
    assert second != first
    (scripts / 'al_two.py').write_text('VALUE = 3\n')
    assert R.implementation_digest() not in {first, second}


def test_repository_identity_follows_head_and_target(repo):
    import al_scope
    head = _git(repo, 'rev-parse', 'HEAD').decode().strip()
    first = al_scope.identity(head)
    assert al_scope.identity(head) == first and first['merge_base'] == head
    _commit(repo, 'memo-identity.txt', 'x\n')
    moved = al_scope.identity(head)
    assert moved['merge_base'] == head and moved['target_oid'] == head
    new_head = _git(repo, 'rev-parse', 'HEAD').decode().strip()
    _git(repo, 'checkout', '-q', '--orphan', 'memo-unrelated')
    _git(repo, 'rm', '-rq', '--cached', '.')
    _commit(repo, 'memo-orphan.txt', 'y\n')
    try:
        al_scope.identity(new_head)
    except SystemExit as exc:  # unrelated histories have no merge base; never a reused answer
        assert 'merge-base' in str(exc) or 'git failed' in str(exc)
    else:
        raise AssertionError('identity reused an answer for a different HEAD')
