"""The Git-tree capture layer equals AL's direct capture on every membership case (#25)."""
import hashlib
import json
import os
import subprocess

import pytest

import al_analysis_index as index
import al_repository_facts as F
import al_scope as S
import al_snapshot
from conftest import _git
from test_service_coverage import put, seed, unit


def _collect(repo, paths=('services/a/src/app.py',)):
    base = S.git_bytes('rev-parse', 'HEAD', root=repo).decode().strip()
    facts = F.collect(repo, base_oid=base, paths=list(paths), opaque=set())
    return json.dumps(facts, sort_keys=True)


def _direct(repo, monkeypatch, **kwargs):
    with monkeypatch.context() as patch:
        patch.setenv('AL_ANALYSIS_CACHE', '0')
        return _collect(repo, **kwargs)


@pytest.fixture
def vouching(monkeypatch):
    """Files written by the test are seconds old: vouch for them anyway, and count reads."""
    import al_repository_capture as capture
    monkeypatch.setattr(F, 'RACY_NS', -10**12)
    monkeypatch.setattr(capture, 'RACY_NS', -10**12)
    reads = []
    real = F.Snapshot.read_observed

    def read(self, path):
        reads.append(path)
        return real(self, path)

    monkeypatch.setattr(F.Snapshot, 'read_observed', read)
    return reads


def _fixture(repo):
    seed(repo)
    unit(repo, 'services/c', 'import common\n')
    put(repo, '.gitignore', '__pycache__/\nignored/\n*.log\n')
    put(repo, 'ignored/tool.py', 'IGNORED = 1\n')                  # ignored, untracked
    put(repo, 'services/a/src/kept.log', 'tracked but ignored\n')  # tracked-but-ignored
    os.symlink('app.py', repo / 'services/c/src/alias.py')          # symlink boundary
    nested = repo / 'services/nested'                               # nested repository
    nested.mkdir(parents=True)
    _git(nested, 'init', '-q')
    put(nested, 'inner.py', 'INNER = 1\n')
    _git(nested, 'add', '.')
    _git(nested, 'commit', '-qm', 'nested')
    _git(repo, 'add', '-f', 'services/a/src/kept.log')
    _git(repo, 'add', '.gitignore', 'packages', 'services/a', 'services/b', 'services/c')
    _git(repo, 'commit', '-qm', 'capture fixture')


def test_tree_capture_equals_direct_capture_on_membership_cases(repo, monkeypatch, vouching):
    _fixture(repo)
    put(repo, 'services/b/src/app.py', 'import common\nEDIT = 1\n')     # uncommitted change
    put(repo, 'services/b/src/untracked.py', 'UNTRACKED = 1\n')         # untracked file
    put(repo, 'services/a/src/kept.log', 'tracked but ignored, edited\n')
    assert _collect(repo) == _direct(repo, monkeypatch)
    # Once the per-blob facts exist, ordinary files are vouched for by the snapshot
    # tree instead of being read one by one; uncommitted edits still match.
    vouching.clear()
    index.reset()
    assert _collect(repo) == _direct(repo, monkeypatch)
    vouching.clear()
    _collect(repo)
    assert 'packages/shared/src/common.py' not in vouching


def test_assume_unchanged_and_skip_worktree_entries_are_checked_against_disk(repo, monkeypatch, vouching):
    _fixture(repo)
    _git(repo, 'update-index', '--assume-unchanged', 'services/b/src/app.py')
    _git(repo, 'update-index', '--skip-worktree', 'packages/shared/src/common.py')
    put(repo, 'services/b/src/app.py', 'VALUE=2\n')       # same size, hidden from git status
    put(repo, 'packages/shared/src/common.py', 'VALUE=3\n')
    assert _git_status(repo) == ''
    facts = json.loads(_collect(repo))
    assert facts == json.loads(_direct(repo, monkeypatch))
    hashes = {r['path']: r['sha256'] for r in facts['rule_sources'] + facts['scan_inputs'] if r.get('snapshot', 'current') == 'current'}
    assert hashes['services/b/src/app.py'] == hashlib.sha256(b'VALUE=2\n').hexdigest()
    assert hashes['packages/shared/src/common.py'] == hashlib.sha256(b'VALUE=3\n').hexdigest()
    # The user's index keeps its flags; only AL's private index was refreshed.
    flags = subprocess.run(['git', 'ls-files', '-v', 'services/b/src/app.py', 'packages/shared/src/common.py'],
                           cwd=repo, capture_output=True, text=True).stdout
    assert set(flags.splitlines()) == {'h services/b/src/app.py', 'S packages/shared/src/common.py'}


def _git_status(repo):
    return subprocess.run(['git', 'status', '--porcelain', '-uno'], cwd=repo, capture_output=True, text=True).stdout.strip()


def test_converted_files_are_read_directly(repo, monkeypatch, vouching):
    _fixture(repo)
    put(repo, '.gitattributes', '*.py text eol=crlf\n')
    _git(repo, 'add', '.gitattributes')
    _git(repo, 'commit', '-qm', 'attributes')
    (repo / 'services/c/src/app.py').write_bytes(b'import common\r\n')
    assert _collect(repo) == _direct(repo, monkeypatch)
    assert 'services/c/src/app.py' in vouching


def test_recent_files_are_never_vouched_for(repo, monkeypatch):
    _fixture(repo)
    reads = []
    real = F.Snapshot.read_observed
    monkeypatch.setattr(F.Snapshot, 'read_observed', lambda self, p: reads.append(p) or real(self, p))
    put(repo, 'services/c/src/app.py', 'import common\nRECENT = 1\n')
    assert _collect(repo) == _direct(repo, monkeypatch)
    assert 'services/c/src/app.py' in reads


def test_snapshot_never_touches_the_user_index_or_worktree(repo):
    _fixture(repo)
    put(repo, 'services/b/src/untracked.py', 'U = 1\n')
    index_file = repo / '.git/index'
    before = index_file.read_bytes(), _git_status(repo)
    tree = al_snapshot.snapshot(repo)
    assert (index_file.read_bytes(), _git_status(repo)) == before
    listing = al_snapshot.entries(repo, tree)
    assert 'services/b/src/untracked.py' in listing and 'ignored/tool.py' not in listing
    assert listing['services/c/src/alias.py'][0] == '120000'
    assert listing['services/nested'][1] == 'commit'


def test_tracked_files_in_generated_directories_are_members(repo):
    """Tracked files are always members; only untracked ones follow AL's exclusions."""
    _fixture(repo)
    put(repo, 'web/dist/bundle.js', 'v1\n')
    put(repo, 'web/node_modules/lib/index.js', 'v1\n')
    _git(repo, 'add', '-f', 'web/dist/bundle.js', 'web/node_modules/lib/index.js')
    _git(repo, 'commit', '-qm', 'tracked generated files')
    put(repo, 'web/node_modules/other/untracked.js', 'u\n')    # untracked: excluded by AL's rule
    put(repo, 'build/untracked.txt', 'u\n')
    listing = al_snapshot.entries(repo, al_snapshot.snapshot(repo))
    assert 'web/dist/bundle.js' in listing and 'web/node_modules/lib/index.js' in listing
    assert 'web/node_modules/other/untracked.js' not in listing and 'build/untracked.txt' not in listing
    before = (al_snapshot.input_tree({}, ['web/dist']), al_snapshot.input_tree({}, ['web/node_modules']))
    put(repo, 'web/dist/bundle.js', 'v2\n')
    put(repo, 'web/node_modules/lib/index.js', 'v2\n')
    after = (al_snapshot.input_tree({}, ['web/dist']), al_snapshot.input_tree({}, ['web/node_modules']))
    assert before[0] != after[0] and before[1] != after[1]
    # A file an earlier snapshot saw untracked does not linger once it is ignored.
    put(repo, 'services/b/src/later.py', 'L = 1\n')
    assert 'services/b/src/later.py' in al_snapshot.entries(repo, al_snapshot.snapshot(repo))
    put(repo, '.gitignore', '__pycache__/\nignored/\n*.log\nlater.py\n')
    assert 'services/b/src/later.py' not in al_snapshot.entries(repo, al_snapshot.snapshot(repo))


def test_input_tree_is_a_content_identity_of_the_scoped_paths(repo):
    _fixture(repo)
    import al_store
    first = al_snapshot.input_tree({}, ['services/a', 'packages/shared/src/common.py'])
    put(repo, 'services/b/src/app.py', 'OUTSIDE = 1\n')
    assert al_snapshot.input_tree({}, ['services/a', 'packages/shared/src/common.py']) == first
    put(repo, 'services/a/src/app.py', 'INSIDE = 1\n')
    second = al_snapshot.input_tree({}, ['services/a', 'packages/shared/src/common.py'])
    assert second != first
    put(repo, 'services/a/src/app.py', 'import common\n')
    assert al_snapshot.input_tree({}, ['services/a', 'packages/shared/src/common.py']) == first
    assert al_snapshot.input_tree({}, ['.']) == al_snapshot.snapshot(al_store.REPO_ROOT)


def test_pinned_snapshot_refs_are_removed_and_swept(repo):
    _fixture(repo)
    tree = al_snapshot.snapshot(repo)
    al_snapshot.pin('run1', 'start', tree, repo)
    al_snapshot.pin('run2', 'start', tree, repo)
    assert al_snapshot.pinned(repo) == ['refs/al/snapshots/run1/start', 'refs/al/snapshots/run2/start']
    al_snapshot.unpin('run1', repo)
    assert al_snapshot.pinned(repo) == ['refs/al/snapshots/run2/start']
    import time
    al_snapshot.sweep(root=repo)
    assert al_snapshot.pinned(repo) == ['refs/al/snapshots/run2/start']   # pinned recently
    al_snapshot.sweep(root=repo, now=time.time() + al_snapshot.ORPHAN_SECONDS + 60)
    assert al_snapshot.pinned(repo) == []
    with pytest.raises(ValueError):
        al_snapshot.pin('../x', 'start', tree, repo)


def test_busy_private_index_falls_back_to_direct_reads(repo, monkeypatch, vouching):
    _fixture(repo)
    lock = al_snapshot.index_path(repo).with_name(al_snapshot.INDEX + '.lock')
    al_snapshot.snapshot(repo)
    lock.write_text('')
    try:
        assert _collect(repo) == _direct(repo, monkeypatch)
        assert 'packages/shared/src/common.py' in vouching
    finally:
        lock.unlink()


def test_disabled_store_reads_directly(repo, monkeypatch, vouching):
    _fixture(repo)
    monkeypatch.setenv('AL_ANALYSIS_CACHE', '0')
    _collect(repo)
    assert 'packages/shared/src/common.py' in vouching
    assert not index.index_root().exists() or not list(index.index_root().rglob('*.pack'))


def _cold_tree(repo):
    """The snapshot tree a private index seeded afresh from the real index produces."""
    target = al_snapshot.index_path(repo)
    for path in (target, target.with_name(al_snapshot.INDEX + '.seed')):
        if path.exists():
            path.unlink()
    return al_snapshot.snapshot(repo)


def test_kept_private_index_equals_a_fresh_seed_on_every_change(repo):
    """The private index is kept while the real index is unchanged; every tree it gives equals
    the tree of a private index seeded afresh (#25)."""
    _fixture(repo)
    steps = [
        lambda: put(repo, 'services/a/src/app.py', 'VALUE = 41\n'),          # modified tracked
        lambda: put(repo, 'services/a/src/new.py', 'NEW = 1\n'),             # new untracked member
        lambda: put(repo, '.gitignore', '__pycache__/\nignored/\n*.log\nservices/a/src/new.py\n'),  # member now ignored
        lambda: os.unlink(repo / 'packages/shared/src/common.py'),           # deleted tracked
        lambda: (repo / 'services/b/src/app.py').chmod(0o755),               # mode change
        lambda: _git(repo, 'add', 'services/a/src/app.py'),                  # real index changed: reseed
        lambda: _git(repo, 'update-index', '--assume-unchanged', 'services/c/src/app.py'),
        lambda: put(repo, 'services/c/src/app.py', 'import common\nCHANGED = 1\n'),
        lambda: (os.unlink(repo / 'services/a/src/new.py'), put(repo, '.gitignore', '__pycache__/\nignored/\n*.log\n')),
    ]
    al_snapshot.snapshot(repo)
    for step in steps:
        step()
        kept = al_snapshot.snapshot(repo)
        assert kept == _cold_tree(repo)
        assert al_snapshot.snapshot(repo) == kept


def test_unchanged_working_copy_reuses_the_recorded_tree_after_a_probe(repo, monkeypatch):
    """A snapshot of an unchanged working copy is the recorded tree after a read-only probe
    (#36); any change the probe must see (a same-size edit within the same second, a new
    untracked file, the nested repository moving, a changed private index) takes the full
    snapshot, whose tree equals a cold seed."""
    _fixture(repo)
    calls = []
    real = al_snapshot._run

    def counting(root, args, **kwargs):
        calls.append(next(a for a in args if not a.startswith('-') and '=' not in a))
        return real(root, args, **kwargs)
    monkeypatch.setattr(al_snapshot, '_run', counting)
    first = al_snapshot.snapshot(repo)

    def taken():
        calls.clear()
        tree = al_snapshot.snapshot(repo)
        return tree, set(calls)
    tree, used = taken()
    assert tree == first and not used & {'add', 'rm', 'write-tree', 'update-index'}
    nested = repo / 'services/nested'
    for step in (lambda: put(repo, 'services/a/src/app.py', put_same_size(repo, 'services/a/src/app.py')),
                 lambda: put(repo, 'services/a/src/fresh.py', 'FRESH = 1\n'),
                 lambda: (put(nested, 'inner.py', 'INNER = 2\n'), _git(nested, 'commit', '-qam', 'moved'))):
        step()
        tree, used = taken()
        assert 'write-tree' in used and tree == _cold_tree(repo)
        assert taken() == (tree, {'ls-files'})
    # A record of an earlier private index never vouches for the current one.
    record = al_snapshot.index_path(repo).with_name(al_snapshot.TREE_RECORD)
    earlier = record.read_bytes()
    put(repo, 'services/a/src/fresh.py', 'FRESH = 2\n')
    current = al_snapshot.snapshot(repo)
    record.write_bytes(earlier)
    assert al_snapshot.snapshot(repo) == current == _cold_tree(repo) != tree


def put_same_size(repo, path):
    old = (repo / path).read_text()
    return old[:-2] + ('X' if old[-2] != 'X' else 'Y') + old[-1]


def test_concurrent_snapshots_of_one_worktree_wait_for_each_other(repo):
    """A snapshot taken while another holds the private index (the service's background
    refresh) waits for it instead of failing into the slow direct capture (#35)."""
    import fcntl
    import threading
    import time
    _fixture(repo)
    expected = al_snapshot.snapshot(repo)
    lock = al_snapshot.index_path(repo).parent / 'snapshot.lock'
    fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    released = []
    def release():
        time.sleep(0.3)
        released.append(time.monotonic())
        os.close(fd)
    worker = threading.Thread(target=release)
    worker.start()
    try:
        assert al_snapshot.snapshot(repo) == expected
        assert released and time.monotonic() >= released[0]
    finally:
        worker.join()
