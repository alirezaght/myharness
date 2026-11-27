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


def test_concurrent_commit_cannot_poison_an_exact_tree_listing(repo, monkeypatch):
    first = _git(repo, 'rev-parse', 'HEAD').decode().strip()
    real = G.git
    moved = []

    def racing(root, *args):
        if args[:1] == ('ls-tree',) and not moved:
            moved.append(True)
            _commit(repo, 'memo-race.txt', 'race\n')  # HEAD moves between key resolution and the read
        return real(root, *args)
    monkeypatch.setattr(G, 'git', racing)
    G._TREES.clear()
    assert G.tree_entries(repo, 'HEAD') == G.entries(_git(repo, 'ls-tree', '-r', '-z', first))
    monkeypatch.setattr(G, 'git', real)
    assert G.tree_entries(repo, first) == G.entries(_git(repo, 'ls-tree', '-r', '-z', first))
    assert b'memo-race.txt' in G.tree_entries(repo, 'HEAD')


def test_repository_selecting_git_environment_disables_reuse(repo, monkeypatch):
    monkeypatch.setenv('GIT_DIR', str(repo / '.git'))
    assert G.plain_metadata(repo) is None and G._index_key(repo) is None


def test_blob_content_hashes_are_computed_once_per_process(repo, monkeypatch):
    import al_scope as S
    oid = subprocess.run(['git', 'rev-parse', 'HEAD:svc/app.py'], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    S._BLOB_SHA256.clear()
    requested = []
    real = S._blob_hashes_uncached
    def counting(root, oids):
        requested.append(list(oids))
        return real(root, oids)
    monkeypatch.setattr(S, '_blob_hashes_uncached', counting)
    first = S._blob_hashes(repo, [oid])
    second = S._blob_hashes(repo, [oid, oid])
    assert first == second and len(first[oid]) == 64
    assert requested == [[oid], []]


def test_implementation_digest_reads_changed_bytes_when_file_metadata_collides(tmp_path, monkeypatch):
    """Equal coarse metadata must not certify unchanged ordinary module bytes."""
    import hashlib
    import json
    from pathlib import Path
    from types import SimpleNamespace

    assert Path(R.__file__).resolve() == Path(__file__).resolve().parents[1] / 'scripts/al_runtime_resources.py'
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    member = scripts / 'al_one.py'
    before_bytes, after_bytes = b'VALUE = 1\n', b'VALUE = 2\n'
    assert len(before_bytes) == len(after_bytes)
    member.write_bytes(before_bytes)
    real_os = R.os
    before_stat = real_os.stat(member)
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')

    def metadata(value):
        return tuple(getattr(value, field) for field in fields)

    def coarse_stat(path):
        # Controlled filesystem-view model: retain actual identity/size, expose equal
        # timestamps for these same-length writes. This is not E27 native cause proof.
        current = real_os.stat(path)
        assert Path(path) == member
        return SimpleNamespace(st_dev=current.st_dev, st_ino=current.st_ino,
                               st_size=current.st_size, st_mtime_ns=before_stat.st_mtime_ns,
                               st_ctime_ns=before_stat.st_ctime_ns)

    monkeypatch.setattr(R, '__file__', str(scripts / 'al_runtime_resources.py'))
    monkeypatch.setattr(R, '_CONTEXT', None)
    monkeypatch.setattr(R, '_DIGEST', None)
    monkeypatch.setattr(R, '_USED', False)
    # Keep real byte reads and directory enumeration; model only stat timestamps.
    monkeypatch.setattr(R, 'os', SimpleNamespace(scandir=real_os.scandir, stat=coarse_stat, path=real_os.path))
    exposed_before = metadata(R.os.stat(member))
    first = R.implementation_digest()
    assert member.read_bytes() == before_bytes
    assert first == hashlib.sha256(before_bytes).hexdigest()
    assert R.implementation_digest() == first
    member.write_bytes(after_bytes)
    assert member.read_bytes() == after_bytes != before_bytes
    after_stat = real_os.stat(member)
    exposed_after = metadata(R.os.stat(member))
    assert exposed_after == exposed_before
    expected_new = hashlib.sha256(after_bytes).hexdigest()
    assert expected_new != first
    (tmp_path / 'metadata-collision.json').write_text(json.dumps({
        'model': 'controlled equal metadata; actual byte writes and reads',
        'fields': fields, 'actual_before': metadata(before_stat), 'actual_after': metadata(after_stat),
        'exposed_before': exposed_before, 'exposed_after': exposed_after,
        'first_digest': first, 'expected_new_digest': expected_new,
    }, indent=2))
    assert R.implementation_digest() == expected_new


def _ordinary_digest_modules(tmp_path, monkeypatch, contents):
    scripts = tmp_path / 'scripts'
    scripts.mkdir(parents=True)
    for name, raw in contents.items():
        (scripts / name).write_bytes(raw)
    monkeypatch.setattr(R, '__file__', str(scripts / 'al_runtime_resources.py'))
    monkeypatch.setattr(R, '_CONTEXT', None)
    monkeypatch.setattr(R, '_DIGEST', None)
    monkeypatch.setattr(R, '_USED', False)
    return scripts


def test_implementation_digest_follows_module_deletion(tmp_path, monkeypatch):
    import hashlib

    first, second = b'ONE = 1\n', b'TWO = 2\n'
    scripts = _ordinary_digest_modules(tmp_path, monkeypatch, {'al_one.py': first, 'al_two.py': second})
    assert R.implementation_digest() == hashlib.sha256(first + second).hexdigest()
    (scripts / 'al_two.py').unlink()
    assert R.implementation_digest() == hashlib.sha256(first).hexdigest()


def test_implementation_digest_propagates_real_read_errors_after_enumeration(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    import pytest

    real_os = R.os
    for transition in ('missing', 'directory'):
        scripts = _ordinary_digest_modules(tmp_path / transition, monkeypatch, {'al_one.py': b'ONE = 1\n'})
        member = scripts / 'al_one.py'
        R.implementation_digest()  # A previous successful digest must not hide the read failure.

        @contextmanager
        def changed_after_listing(path, target=member, action=transition):
            with real_os.scandir(path) as listing:
                yield listing
            target.unlink()
            if action == 'directory':
                target.mkdir()

        proxy = SimpleNamespace(**vars(real_os))
        proxy.scandir = changed_after_listing
        monkeypatch.setattr(R, 'os', proxy)
        with pytest.raises(OSError) as failure:
            R.implementation_digest()
        assert failure.value.filename == str(member)
        monkeypatch.setattr(R, 'os', real_os)


def test_implementation_digest_uses_memoized_captured_bytes_despite_physical_edit(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    before, after = b'VALUE = 1\n', b'VALUE = 2\n'
    scripts = _ordinary_digest_modules(tmp_path, monkeypatch, {'al_one.py': before})
    digest = hashlib.sha256(before).hexdigest()
    supplied = {'identities': {'al_one.py': 'fixture:al_one.py'}, 'bytes': {'al_one.py': before},
                'platform': 'codex', 'closure_digest': digest, 'origin_digest': digest}
    R._bind_verified_context(supplied)
    actual_sha256 = hashlib.sha256
    hashed = []

    def observed_sha256(raw):
        hashed.append(raw)
        return actual_sha256(raw)

    monkeypatch.setattr(R, 'hashlib', SimpleNamespace(sha256=observed_sha256))
    assert R.implementation_digest() == digest
    (scripts / 'al_one.py').write_bytes(after)
    supplied['bytes']['al_one.py'] = after  # The actual binder owns an immutable copied mapping.
    assert (scripts / 'al_one.py').read_bytes() == after
    assert R.implementation_digest() == digest
    assert hashed == [before]  # Real hash work is reused for the bound immutable context.


def test_proof_policy_identity_follows_actual_runtime_module_bytes(tmp_path, monkeypatch):
    import al_new_origin as Origin

    scripts = _ordinary_digest_modules(tmp_path, monkeypatch, {'al_one.py': b'VALUE = 1\n'})
    original = Origin.policy()
    assert Origin.policy() == original
    (scripts / 'al_one.py').write_bytes(b'VALUE = 2\n')
    changed = Origin.policy()
    assert changed['id'] == original['id'] and changed['version'] == original['version']
    assert changed['digest'] != original['digest']
