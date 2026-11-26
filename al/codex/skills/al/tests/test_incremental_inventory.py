"""Incremental inventories after content-only changes (#35).

A recorded inventory whose capture re-observes with only file content changed starts the new
capture from the fresh listing and the unchanged files' recorded hashes, and recomputes only
the facts of the changed blobs. Every result is compared byte for byte with a cold,
cache-disabled inventory; structural changes take the full inventory."""
import json

import pytest

import al_blob_analysis as blob
import al_gate_inventory as V
import al_repository_facts as F
from conftest import _git
from test_discovery_reuse import _fixture
from test_service_coverage import put, start

PATHS = ['services/a/src/app.py', 'packages/shared/src/common.py']


def _same(a, b):
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _cold(monkeypatch, st, paths=PATHS):
    with monkeypatch.context() as patch:
        patch.setenv('AL_ANALYSIS_CACHE', '0')
        return V.inventory(st, paths)


@pytest.fixture
def inventory_state(cli, repo, run_state, request):
    structural = request.node.name.startswith("test_structural_changes")
    def build():
        _fixture(repo)
        if structural:
            put(repo, '.gitignore', '*.log\n')
            _git(repo, 'add', '.')
            _git(repo, 'commit', '-qm', 'ignore rules')
        return start(cli)
    return run_state("incremental-repository-" + ("ignore" if structural else "base"), build)


@pytest.fixture
def spies(monkeypatch, inventory_state):
    """Snapshots built (with or without a known capture), files read, blobs analysed."""
    seen = {'known': [], 'reads': [], 'analysed': []}
    init, read, events = F.Snapshot.__init__, F.Snapshot.read_observed, blob.import_events

    def snapshot(self, *args, **kwargs):
        if not kwargs.get('listing_only'):
            seen['known'].append(kwargs.get('known') is not None)
        return init(self, *args, **kwargs)

    def observed(self, path):
        seen['reads'].append(path)
        return read(self, path)

    def analysed(data, module, package):
        seen['analysed'].append(module)
        return events(data, module, package)

    monkeypatch.setattr(F.Snapshot, '__init__', snapshot)
    monkeypatch.setattr(F.Snapshot, 'read_observed', observed)
    monkeypatch.setattr(blob, 'import_events', analysed)
    # Nanosecond timestamps: files written by the fixture are settled at once.
    monkeypatch.setattr(F, 'RACY_NS', 0)
    monkeypatch.setattr(__import__('al_repository_capture'), 'RACY_NS', 0)
    return seen


def _reset(seen):
    for values in seen.values():
        values.clear()


def test_a_content_change_recomputes_only_the_changed_blob(cli, repo, monkeypatch, spies, inventory_state):
    st = inventory_state
    V.inventory(st, PATHS)
    _reset(spies)
    put(repo, 'services/a/src/app.py', 'import common\nVALUE = 2\n')
    warm = V.inventory(st, PATHS)
    assert spies['known'] == [True], 'the capture starts from the recorded one'
    # No unchanged member is read for its hash (the analysis still reads the task paths it
    # classifies), and only the changed blob is analysed.
    members = {p for p in spies['reads'] if p.endswith('.py')}
    assert 'services/a/src/app.py' in members and members <= set(PATHS), spies['reads']
    assert spies['analysed'] == ['app'], spies['analysed']
    _same(warm, _cold(monkeypatch, st))
    # The incremental inventory is itself recorded: asked again, it is the answer.
    _reset(spies)
    assert V.inventory(st, PATHS) == warm and spies['known'] == []


def test_a_change_of_imports_is_re_derived_exactly(cli, repo, monkeypatch, spies, inventory_state):
    st = inventory_state
    V.inventory(st, PATHS)
    # A changed blob whose facts differ (an import of another unit) changes the answer.
    put(repo, 'packages/shared/src/common.py', 'import extra\nVALUE = 1\n')
    warm = V.inventory(st, PATHS)
    assert spies['known'][-1] is True
    _same(warm, _cold(monkeypatch, st))


@pytest.mark.parametrize('change', ['added', 'removed', 'manifest', 'rule', 'ignore', 'kind'])
def test_structural_changes_take_the_full_inventory(cli, repo, monkeypatch, spies, change, inventory_state):
    st = inventory_state
    V.inventory(st, PATHS)
    _reset(spies)
    if change == 'added':
        put(repo, 'services/a/src/new.py', 'VALUE = 3\n')
    elif change == 'removed':
        (repo / 'services/a/src/extra.py').unlink()
    elif change == 'manifest':
        put(repo, 'services/b/pyproject.toml', '[project]\nname="b"\ndependencies=["shared"]\n')
    elif change == 'rule':
        put(repo, 'ci/jobs.yml', (repo / 'ci/jobs.yml').read_text() + '\n# reviewed\n')
    elif change == 'ignore':
        put(repo, '.gitignore', '*.log\n*.tmp\n')
    else:
        (repo / 'services/a/src/extra.py').unlink()
        (repo / 'services/a/src/extra.py').symlink_to('app.py')
    warm = V.inventory(st, PATHS)
    assert spies['known'] == [False], change
    _same(warm, _cold(monkeypatch, st))


def test_drift_during_an_incremental_capture_retries_in_full(cli, repo, monkeypatch, spies, inventory_state):
    st = inventory_state
    V.inventory(st, PATHS)
    _reset(spies)
    put(repo, 'services/a/src/app.py', 'import common\nVALUE = 2\n')
    original = V._inventory_attempt
    moved = []

    def racing(*args, **kwargs):
        result = original(*args, **kwargs)
        if not moved:
            moved.append(True)
            put(repo, 'services/a/src/app.py', 'import common\nVALUE = 3\n')
        return result

    monkeypatch.setattr(V, '_inventory_attempt', racing)
    warm = V.inventory(st, PATHS)
    # The first, incremental attempt drifted; the retry captured afresh.
    assert spies['known'] == [True, False]
    assert warm['capture']['discarded_attempts'] if 'capture' in warm else \
        warm['service_coverage']['capture']['discarded_attempts']
    monkeypatch.setattr(V, '_inventory_attempt', original)
    cold = _cold(monkeypatch, st)
    for key in ('paths', 'units', 'rules', 'services', 'ci', 'relevance_projection', 'auxiliary', 'import_dependencies'):
        assert warm[key] == cold[key], key
