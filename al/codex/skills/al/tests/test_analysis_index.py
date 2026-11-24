"""Per-repository content-addressed fact store: reuse equals recomputation (#25)."""
import json
from pathlib import Path
import os
import stat
import threading

import pytest

import al_analysis_index as index
import al_blob_analysis as blob
import al_repository_facts as F
import al_scope as S
from conftest import _git
from test_service_coverage import put, seed, unit


def _fixture(repo):
    seed(repo)
    unit(repo, 'services/c', 'import common\nfrom common import VALUE\n')
    put(repo, 'services/a/src/extra.py', 'import importlib\nimportlib.import_module("common")\n')
    put(repo, 'services/a/data/rows.json', '[]\n')
    put(repo, 'services/b/src/reader.py', 'PATH = "data/rows.json"\n')
    os.symlink('app.py', repo / 'services/b/src/link.py')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'index fixture')


def _collect(repo, paths=('services/a/data/rows.json', 'packages/shared/src/common.py')):
    base = S.git_bytes('rev-parse', 'HEAD', root=repo).decode().strip()
    facts = F.collect(repo, base_oid=base, paths=list(paths), opaque=set())
    facts.pop('capture')  # identities of this observation, not analysis results
    return json.dumps(facts, sort_keys=True)


def _cold(repo, monkeypatch, **kwargs):
    with monkeypatch.context() as patch:
        patch.setenv('AL_ANALYSIS_CACHE', '0')
        return _collect(repo, **kwargs)


def _counting(monkeypatch):
    calls = {'events': 0, 'runs': 0}
    events, runs = blob.import_events, blob.reference_runs

    def counted_events(*args):
        calls['events'] += 1
        return events(*args)

    def counted_runs(*args):
        calls['runs'] += 1
        return runs(*args)

    monkeypatch.setattr(blob, 'import_events', counted_events)
    monkeypatch.setattr(blob, 'reference_runs', counted_runs)
    return calls


def _packs():
    return sorted((index.index_root()).rglob('*.pack'))


def _forget_process_state():
    """A later command: nothing in memory, only the shared store on disk."""
    index.reset()
    import al_python_imports as imports
    imports.GRAPHS.clear()


def test_warm_facts_are_byte_identical_to_a_cold_disabled_run_across_changes(repo, monkeypatch):
    _fixture(repo)
    calls = _counting(monkeypatch)
    assert _collect(repo) == _cold(repo, monkeypatch)
    assert calls['events'] and calls['runs'] and _packs()
    _forget_process_state()
    calls.update(events=0, runs=0)
    assert _collect(repo) == _cold(repo, monkeypatch)
    # The cold disabled run computes; the warm run computes nothing.
    disabled = dict(calls)
    calls.update(events=0, runs=0)
    _forget_process_state()
    assert _collect(repo) == _cold(repo, monkeypatch) and calls['events'] == disabled['events']
    # Changes between runs: an edited, a new and a deleted source.
    put(repo, 'services/c/src/app.py', 'import common\nimport json\n')
    put(repo, 'services/c/src/new.py', 'from common import VALUE\n')
    (repo / 'services/a/src/extra.py').unlink()
    _forget_process_state()
    assert _collect(repo) == _cold(repo, monkeypatch)


def test_changed_blob_recomputes_only_that_blob(repo, monkeypatch):
    _fixture(repo)
    _collect(repo)
    _forget_process_state()
    calls = _counting(monkeypatch)
    put(repo, 'services/c/src/app.py', 'import common\nimport os\n')
    warm = _collect(repo)
    assert calls['events'] == 1 and calls['runs'] == 1
    assert warm == _cold(repo, monkeypatch)


def test_changed_tree_layout_equals_a_cold_run(repo, monkeypatch):
    _fixture(repo)
    _collect(repo)
    # Same blobs, different layout: a moved module and a new unit owning existing bytes.
    (repo / 'services/c/src/app.py').rename(repo / 'services/c/src/moved.py')
    unit(repo, 'services/d', 'import common\nfrom common import VALUE\n')
    _forget_process_state()
    calls = _counting(monkeypatch)
    warm = _collect(repo)
    assert warm == _cold(repo, monkeypatch)
    assert '"services/d"' in warm and 'services/c/src/moved.py' in warm
    # Identical bytes under another module identity are a different import fact.
    assert calls['events'] >= 1


def test_changed_analyser_code_is_a_miss(repo, monkeypatch):
    _fixture(repo)
    before = _collect(repo)
    _forget_process_state()
    calls = _counting(monkeypatch)
    _collect(repo)
    assert calls['events'] == 0
    # Another release of the per-blob analyser: every one of its facts is recomputed;
    # facts produced by unchanged code stay valid.
    monkeypatch.setitem(index._SOURCES, 'al_blob_analysis', index._module_bytes('al_blob_analysis') + b'\n# release\n')
    monkeypatch.setattr(index, '_DIGESTS', {})
    _forget_process_state()
    assert _collect(repo) == before
    assert calls['events'] > 0 and calls['runs'] > 0


def test_unrelated_module_change_keeps_per_blob_facts(repo, monkeypatch):
    _fixture(repo)
    _collect(repo)
    unrelated = next(name for name in sorted(index._modules()) if name not in {'al_blob_analysis'})
    monkeypatch.setitem(index._SOURCES, unrelated, index._module_bytes(unrelated) + b'\n# release\n')
    monkeypatch.setattr(index, '_DIGESTS', {})
    _forget_process_state()
    calls = _counting(monkeypatch)
    _collect(repo)
    assert calls['events'] == 0 and calls['runs'] == 0


def test_corrupt_entries_and_packs_are_misses(repo, monkeypatch):
    _fixture(repo)
    before = _collect(repo)
    for pack in _packs():
        parsed = index.Pack.open(str(pack))
        data = bytearray(pack.read_bytes())
        for start, length, _ in parsed.entries.values():
            if length:
                data[parsed.offset + start] ^= 0xFF  # every entry's bytes, header intact
        pack.write_bytes(bytes(data))
    _forget_process_state()
    calls = _counting(monkeypatch)
    assert _collect(repo) == before and calls['events'] + calls['runs'] > 0
    for pack in _packs():
        pack.write_bytes(b'garbage')
    _forget_process_state()
    assert _collect(repo) == before


def test_other_worktree_reuses_the_repository_store(repo, tmp_path, monkeypatch):
    _fixture(repo)
    _collect(repo)
    other = tmp_path / 'other'
    _git(repo, 'worktree', 'add', '-q', str(other))
    _forget_process_state()
    calls = _counting(monkeypatch)
    monkeypatch.chdir(other)
    warm = _collect(other)
    assert calls['events'] == 0 and calls['runs'] == 0
    assert warm == _cold(other, monkeypatch)
    assert len({p.parent for p in _packs()}) == 1


def test_uncommitted_and_untracked_content_is_hashed(repo, monkeypatch):
    _fixture(repo)
    _collect(repo)
    put(repo, 'services/b/src/app.py', 'import common\nEDITED = 1\n')  # uncommitted edit
    put(repo, 'services/b/src/untracked.py', 'PATH = "rows.json"\n')  # untracked file
    _forget_process_state()
    calls = _counting(monkeypatch)
    warm = _collect(repo)
    assert calls['events'] == 2 and calls['runs'] == 2
    assert 'services/b/src/untracked.py' in warm and warm == _cold(repo, monkeypatch)


def test_entry_evicted_mid_run_is_recomputed(repo, monkeypatch):
    _fixture(repo)
    before = _collect(repo)
    _forget_process_state()
    real = index.Store.get_many

    def evicting(self, identities):
        for pack in _packs():
            pack.unlink()
        return real(self, identities)

    monkeypatch.setattr(index.Store, 'get_many', evicting)
    calls = _counting(monkeypatch)
    assert _collect(repo) == before and calls['events'] > 0


def test_store_is_private_atomic_and_refuses_symlinks(repo, tmp_path, monkeypatch):
    _fixture(repo)
    _collect(repo)
    packs = _packs()
    assert packs and all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in packs)
    assert stat.S_IMODE(packs[0].parent.stat().st_mode) == 0o700
    assert not list(index.index_root().rglob('.tmp.*'))
    real = tmp_path / 'elsewhere'
    real.mkdir(mode=0o700)
    link = tmp_path / 'linked-index'
    link.symlink_to(real)
    monkeypatch.setenv('AL_ANALYSIS_INDEX_DIR', str(link))
    index.reset()
    assert index.store(repo) is None
    monkeypatch.setenv('AL_ANALYSIS_INDEX_DIR', str(tmp_path / 'fresh'))
    monkeypatch.setenv('AL_ANALYSIS_CACHE', '0')
    index.reset()
    assert index.store(repo) is None


def test_lru_eviction_bounds_packs_without_rewriting(repo, monkeypatch):
    _fixture(repo)
    monkeypatch.setenv('AL_ANALYSIS_INDEX_PACKS', '2')
    target = index.store(repo)
    for number in range(5):
        target.put_many({index.identity('t', 'c', number): b'%d' % number})
        target.flush()
    assert len(_packs()) == 2


def test_concurrent_writers_of_the_same_facts_race_harmlessly(repo, monkeypatch):
    _fixture(repo)
    directory = index.store(repo).directory
    errors = []

    def write():
        try:
            mine = index.Store(directory)
            mine.put_many({index.identity('t', 'c', n): b'value-%d' % n for n in range(200)})
            mine.flush()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    reader = index.Store(directory)
    found = reader.get_many({index.identity('t', 'c', n) for n in range(200)})
    assert found == {index.identity('t', 'c', n): b'value-%d' % n for n in range(200)}


_COLLECT = '''
import json, sys
sys.path.insert(0, sys.argv[1])
import al_repository_facts as F, al_scope as S, al_analysis_index as index
repo = sys.argv[2]
base = S.git_bytes('rev-parse', 'HEAD', root=repo).decode().strip()
facts = F.collect(repo, base_oid=base, paths=['services/a/data/rows.json', 'packages/shared/src/common.py'], opaque=set())
facts.pop('capture')
index.flush()
sys.stdout.write(json.dumps(facts, sort_keys=True))
'''


def _processes(roots, env):
    import subprocess
    import sys
    scripts = str(Path(index.__file__).parent)
    procs = [subprocess.Popen([sys.executable, '-c', _COLLECT, scripts, str(root)], cwd=root, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) for root in roots]
    out = [p.communicate(timeout=240) for p in procs]
    assert all(p.returncode == 0 for p in procs), [o[1][-2000:] for o in out]
    return [o[0].decode() for o in out]


@pytest.mark.parametrize('branches', ['same', 'different'])
def test_concurrent_discovery_in_two_worktrees_is_byte_identical(repo, tmp_path, branches):
    _fixture(repo)
    first, second = tmp_path / 'one', tmp_path / 'two'
    _git(repo, 'worktree', 'add', '-q', '--detach', str(first))
    if branches == 'same':
        _git(repo, 'worktree', 'add', '-q', '--detach', str(second))
    else:
        _git(repo, 'worktree', 'add', '-q', '-b', 'other', str(second))
        put(second, 'services/c/src/app.py', 'import common\nOTHER = 1\n')
        _git(second, 'add', '.')
        _git(second, 'commit', '-qm', 'other branch')
    env = dict(os.environ)
    shared = _processes([first, second], env)
    cold = _processes([first, second], dict(env, AL_ANALYSIS_CACHE='0'))
    assert shared == cold
    assert (shared[0] == shared[1].replace(str(second), str(first))) == (branches == 'same')
    # Both commands published into the one repository store without waiting on each other.
    assert len({p.parent for p in _packs()}) == 1


def test_parallel_recomputation_equals_serial_and_is_bounded(repo, monkeypatch):
    import al_python_imports as imports
    _fixture(repo)
    monkeypatch.setenv('AL_ANALYSIS_JOBS', '1')
    serial = _cold(repo, monkeypatch)
    pools = []
    import multiprocessing
    original = multiprocessing.get_context

    def counted(method):
        context = original(method)
        real_pool = context.Pool

        def pool(processes, *args, **kwargs):
            pools.append(processes)
            return real_pool(processes, *args, **kwargs)

        context.Pool = pool
        return context

    monkeypatch.setattr(multiprocessing, 'get_context', counted)
    monkeypatch.setattr(imports, 'PARALLEL_MINIMUM', 1)
    monkeypatch.setenv('AL_ANALYSIS_JOBS', '2')
    assert _cold(repo, monkeypatch) == serial
    assert pools and set(pools) == {2}


def test_reference_runs_answer_every_safe_token_like_the_bytes():
    import random
    rng = random.Random(25)
    alphabet = b'ab/."\'. \n(#'
    for _ in range(3000):
        data = bytes(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        runs = blob.reference_runs(data)
        for _ in range(5):
            token = bytes(rng.choice(alphabet) for _ in range(rng.randint(1, 4)))
            if blob.token_safe(token):
                assert (token in data) == (token in runs), (data, token)
