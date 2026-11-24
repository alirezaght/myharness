"""In-memory data the resident AL service hands a command (#27) equals what it would compute."""
import importlib
import os
import subprocess

import al_analysis_index as index
import al_service_cache as C
import al_snapshot


def test_excluded_directories_match_the_snapshot():
    assert C.EXCLUDED == al_snapshot.EXCLUDED


def test_relevance_follows_what_git_add_records():
    root, git_dir = '/w', '/w/.git'
    dirs = [git_dir]
    assert C.relevant('/w/src/app.py', root, dirs)
    assert C.relevant('/w', root, dirs)
    assert C.relevant('/w/.gitignore', root, dirs)
    assert not C.relevant('/w/node_modules/x/y.js', root, dirs)
    assert not C.relevant('/w/.codex/al-runtime/s.json', root, dirs)
    assert not C.relevant('/w/sub/build/out.o', root, dirs)
    assert C.relevant('/w/.git/info/exclude', root, dirs)
    assert C.relevant('/w/.git/config', root, dirs)
    assert not C.relevant('/w/.git/objects/ab/cd', root, dirs)
    assert not C.relevant('/w/.git/al-snapshot.index', root, dirs)
    assert not C.relevant('/w/.git/' + C.COOKIE + '1.2', root, dirs)
    assert not C.relevant('/elsewhere/a.py', root, dirs)
    assert not C.relevant('/wx/a.py', root, dirs)


def _fresh(name):
    module = importlib.import_module(name)
    return importlib.reload(module)


def test_snapshot_hook_answers_only_for_its_environment(repo, monkeypatch):
    snapshot = _fresh('al_snapshot')
    try:
        expected = snapshot.snapshot(repo)
        asked = []

        def current(path):
            asked.append(os.path.realpath(path))
            return 'f' * 40

        C.install(snapshot, {'current': current, 'environment': C.environment_key(os.environ)})
        assert snapshot.snapshot(repo) == 'f' * 40
        assert asked == [os.path.realpath(str(repo))]
        snapshot = _fresh('al_snapshot')
        C.install(snapshot, {'current': lambda path: None, 'environment': C.environment_key(os.environ)})
        assert snapshot.snapshot(repo) == expected
        snapshot = _fresh('al_snapshot')
        monkeypatch.setenv('GIT_INDEX_VERSION', '4')
        C.install(snapshot, {'current': current, 'environment': []})
        assert snapshot.snapshot(repo) == expected
    finally:
        _fresh('al_snapshot')


def test_pack_headers_are_reused_only_for_the_same_file(tmp_path):
    module = _fresh('al_analysis_index')
    try:
        directory = tmp_path / 'store'
        directory.mkdir(mode=0o700)
        store = module.Store(str(directory))
        store.put_many({'a' * 64: b'one', 'b' * 64: b'two'})
        store.flush()
        headers = C.pack_headers(module, str(directory), {})
        assert len(headers) == 1
        path = next(iter(headers))
        fresh = module.Pack.open(path)
        assert headers[path][1:] == (fresh.entries, fresh.offset)
        C.install(module, {'packs': headers})
        opened = module.Pack.open(path)
        assert (opened.entries, opened.offset) == (fresh.entries, fresh.offset)
        assert opened.read({'a' * 64}) == {'a' * 64: b'one'}
        # A different file at the same path is parsed afresh.
        stale = {path: ((0, 0), {'x': [0, 1, 'y']}, 0)}
        module = _fresh('al_analysis_index')
        C.install(module, {'packs': stale})
        assert module.Pack.open(path).entries == fresh.entries
        assert C.pack_headers(module, str(directory), headers) == headers
    finally:
        _fresh('al_analysis_index')
        index.reset()


def test_snapshot_of_a_clean_worktree_is_stable(repo):
    first = al_snapshot.snapshot(repo)
    assert al_snapshot.snapshot(repo) == first
    (repo / 'new.txt').write_text('x')
    assert al_snapshot.snapshot(repo) != first
    subprocess.run(['git', 'status', '--short'], cwd=repo, check=True, capture_output=True)
