"""One inventory per discovery, lock-free analysis, extension and bounded reach (#8)."""
import json
import threading
import traceback

import pytest

import al_artifacts as A
import al_gate_discovery as D
import al_gate_inventory as V
import al_store
from conftest import _git
from hook_regression_helpers import invoke
from test_service_coverage import put, seed, start, unit


def _fixture(repo):
    seed(repo)
    unit(repo, 'services/c', 'import common\n')
    put(repo, 'services/a/src/extra.py', 'from common import VALUE\n')
    put(repo, 'services/a/docs/notes.md', '# notes\n')
    put(repo, '.gitlab-ci.yml', 'include:\n  - local: ci/jobs.yml\n')
    jobs = []
    for name in ('a', 'b', 'c'):
        jobs += [f'{name}-check:', '  stage: test', f'  script: [python3 -m compileall -q services/{name}]',
                 '  rules:', '    - changes:', f'        - services/{name}/**/*', '        - packages/shared/**/*', '']
    put(repo, 'ci/jobs.yml', '\n'.join(jobs))
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'reuse fixture')


def _paths(tmp, values, name='paths.json'):
    path = tmp / name
    path.write_text(json.dumps(values))
    return path


def _count_attempts(monkeypatch, only_discover=True):
    calls = []
    original = V._inventory_attempt

    def counted(*args, **kwargs):
        # Only inventories computed by `gates discover` itself (the harness may admit afterwards).
        if not only_discover or any(frame.name == '_discover' for frame in traceback.extract_stack()):
            calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(V, '_inventory_attempt', counted)
    return calls


MIXED = [['services/a', 'packages/shared/src/common.py'],
         ['services/a/src', 'services/b/src/app.py', 'docs'],
         ['packages/shared/src/common.py']]


@pytest.mark.parametrize('declared', MIXED)
def test_discovery_runs_one_inventory_and_validation_matches_a_fresh_one(cli, repo, tmp_path, monkeypatch, declared):
    _fixture(repo)
    start(cli)
    calls = _count_attempts(monkeypatch)
    invoke(cli, 'gates', 'discover', '--paths-file', str(_paths(tmp_path, declared)))
    st = json.loads(cli('show')[1])
    manifest = A.read_json(st['gate_manifests'][-1])
    assert manifest['service_coverage']['capture']['discarded_attempts'] == []
    assert len(calls) == 1, 'discover and its validation share one inventory'
    # The reused validation equals a validation computed afresh, without the memo.
    fresh = V.inventory(st, manifest['paths'], manifest.get('ci_context'))
    with V.reuse_inventories():
        expanded = V.discovery_paths(st, declared)
        reused = V.inventory(st, expanded)
        again = V.inventory(st, reused['paths'])
    assert again == reused
    for key in ('paths', 'units', 'rules', 'services', 'ci', 'service_coverage', 'relevance_projection', 'auxiliary'):
        assert fresh[key] == reused[key], key
    assert V.gaps(st, manifest, admission=False) == []


def test_validation_of_a_different_question_is_never_served_from_the_memo(cli, repo, tmp_path, monkeypatch):
    _fixture(repo)
    st = start(cli)
    calls = _count_attempts(monkeypatch, only_discover=False)
    with V.reuse_inventories():
        V.inventory(st, ['services/a/src/app.py'])
        V.inventory(st, ['services/b/src/app.py'])
        V.inventory(st, ['services/a/src/app.py'])
    assert len(calls) == 2
    # Outside the scope the question is answered from the run's verified record of that
    # capture (#25), identical to a fresh inventory with the record store disabled.
    recorded = V.inventory(st, ['services/a/src/app.py'])
    assert len(calls) == 2
    monkeypatch.setenv('AL_ANALYSIS_CACHE', '0')
    fresh = V.inventory(st, ['services/a/src/app.py'])
    assert len(calls) == 3
    assert json.dumps(recorded, sort_keys=True) == json.dumps(fresh, sort_keys=True)
    monkeypatch.delenv('AL_ANALYSIS_CACHE')
    # A changed captured file is a new capture, never the record.
    put(repo, 'services/a/src/app.py', 'import common\nVALUE = 2\n')
    V.inventory(st, ['services/a/src/app.py'])
    assert len(calls) == 4


def test_discovery_analyses_without_the_run_lock_and_writers_proceed(cli, repo, tmp_path, monkeypatch):
    _fixture(repo)
    st = start(cli)
    entered, release = threading.Event(), threading.Event()
    original = D.discover

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(30)
        return original(*args, **kwargs)

    monkeypatch.setattr(D, 'discover', slow)
    result = {}
    paths = _paths(tmp_path, ['services/a/src/app.py'])
    thread = threading.Thread(target=lambda: result.setdefault('r', cli('gates', 'discover', '--paths-file', str(paths))))
    thread.start()
    assert entered.wait(30)
    # Another command takes the run lock while discovery analyses.
    acquired = threading.Event()
    def other():
        with al_store.locked(st['session_id']):
            acquired.set()
    worker = threading.Thread(target=other)
    worker.start()
    assert acquired.wait(5), 'run lock is free while discovery analyses'
    worker.join(5)
    release.set()
    thread.join(120)
    assert result['r'][0] == 0, result['r']


def test_discovery_refuses_when_its_run_state_changed_and_names_the_rerun(cli, repo, tmp_path, monkeypatch):
    _fixture(repo)
    st = start(cli)
    invoke(cli, 'gates', 'discover', '--paths-file', str(_paths(tmp_path, ['services/a/src/app.py'], 'first.json')))
    original = D.discover

    def racing(state, *args, **kwargs):
        manifest = original(state, *args, **kwargs)
        # A concurrent discovery published a wider plan while this one analysed.
        sid = st['session_id']
        with al_store.locked(sid):
            current = al_store.load(sid)
            wider = A.read_json(current['gate_manifests'][-1])
            wider['paths'] = sorted(set(wider['paths']) | {'services/c/src/app.py'})
            current['gate_manifests'] = current['gate_manifests'] + [
                A.snapshot_bytes(sid, json.dumps(wider, sort_keys=True).encode(), 'gate-manifest', 'concurrent.json')]
            al_store.save(sid, current)
        return manifest

    monkeypatch.setattr(D, 'discover', racing)
    paths = _paths(tmp_path, ['services/b/src/app.py'])
    code, out, err = cli('gates', 'discover', '--paths-file', str(paths), '--base', 'pinned-base')
    assert code != 0
    message = str(code) + out + err
    assert 'run state changed while gates discover was analysing' in message
    assert f'Re-run: gates discover --paths-file {paths} --base pinned-base' in message


def test_unrelated_state_changes_do_not_invalidate_discovery(cli, repo, tmp_path, monkeypatch):
    _fixture(repo)
    st = start(cli)
    original = D.discover

    def noisy(state, *args, **kwargs):
        manifest = original(state, *args, **kwargs)
        with al_store.locked(st['session_id']):
            current = al_store.load(st['session_id'])
            # A save outside discovery's inputs (e.g. a writer's bookkeeping) is not a conflict.
            al_store.save(st['session_id'], current)
        return manifest

    monkeypatch.setattr(D, 'discover', noisy)
    invoke(cli, 'gates', 'discover', '--paths-file', str(_paths(tmp_path, ['services/a/src/app.py'])))


def test_extend_adds_paths_to_the_current_manifest(cli, repo, tmp_path):
    _fixture(repo)
    start(cli)
    invoke(cli, 'gates', 'discover', '--paths-file', str(_paths(tmp_path, ['services/a/src/app.py'], 'first.json')))
    invoke(cli, 'gates', 'discover', '--extend', 'current', '--paths-file', str(_paths(tmp_path, ['services/b/src/app.py'])))
    st = json.loads(cli('show')[1])
    manifest = A.read_json(st['gate_manifests'][-1])
    assert {'services/a/src/app.py', 'services/b/src/app.py'} <= set(manifest['paths'])
    assert manifest['revision'] == 2
    code, out, err = cli('gates', 'discover', '--extend', 'no-such-manifest', '--paths-file', str(tmp_path / 'paths.json'))
    assert code != 0 and 'no readable gate manifest' in str(code) + out + err


def test_bounded_discovery_selects_reached_unit_checks_from_consumers(cli, repo, tmp_path):
    _fixture(repo)
    start(cli)
    paths = _paths(tmp_path, ['packages/shared/src/common.py'])
    invoke(cli, 'gates', 'discover', '--paths-file', str(paths), '--bounded')
    st = json.loads(cli('show')[1])
    manifest = A.read_json(st['gate_manifests'][-1])
    assert manifest['discovery_mode'] == 'bounded'
    # Consumers in reached units still select those units and their sourced CI checks.
    assert {'services/a', 'services/c'} <= set(manifest['service_coverage']['assessment']['selected_services'])
    jobs = {g['source']['job'] for g in manifest['gates'] if g['category'] == 'ci' and g.get('source')}
    assert {'a-check', 'c-check'} <= jobs
    # Its own validation and later currency checks re-ask the bounded question.
    assert V.gaps(st, manifest, admission=False) == []


def test_phase_readiness_answers_one_inventory_question_once(cli, repo, tmp_path, monkeypatch):
    _fixture(repo)
    start(cli)
    invoke(cli, 'gates', 'discover', '--paths-file', str(_paths(tmp_path, ['services/a/src/app.py'])))
    calls = _count_attempts(monkeypatch, only_discover=False)
    invoke(cli, 'phase', 'IMPLEMENTATION')
    # Gate currency and service coverage re-ask the manifest's question inside one
    # stability-checked decision window; discovery recorded the inventory of that question,
    # so an unchanged capture answers it without a new inventory (#25).
    assert len(calls) == 0
    put(repo, 'services/a/src/app.py', 'import common\nVALUE = 3\n')
    st = json.loads(cli('show')[1])
    import al_gate_currency
    al_gate_currency.check(st)
    # A changed captured file: one new inventory, once per window.
    assert len(calls) == 1


def _wide_fixture(repo, directories):
    _fixture(repo)
    for index in range(directories):
        put(repo, f'services/a/src/area{index}/mod.py', 'VALUE = 1\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'many directories')


def test_plan_spawns_a_bounded_number_of_listing_processes(cli, repo, tmp_path, monkeypatch):
    import al_repository_facts as F
    _wide_fixture(repo, 60)
    start(cli)
    target = tmp_path / 'manifest.json'
    invoke(cli, 'gates', 'discover', '--paths-file', str(_paths(tmp_path, ['services/a'])), '--output', str(target))
    manifest = json.loads(target.read_text())
    assert sum(1 for p in manifest['paths'] if p.startswith('services/a/src/area')) >= 120
    spawned = []
    original = F.git_records

    def counted(root, args, limits):
        spawned.append(tuple(args[:2]))
        return original(root, args, limits)

    monkeypatch.setattr(F, 'git_records', counted)
    invoke(cli, 'gates', 'plan', '--file', str(target))
    # One listing process per planned directory made large plans spawn thousands of
    # processes; batched pathspecs keep the count independent of the directory count.
    assert len(spawned) < 40, len(spawned)


def test_output_writes_the_manifest_and_prints_one_summary_line(cli, repo, tmp_path):
    import hashlib
    _fixture(repo)
    start(cli)
    target = tmp_path / 'out' / 'manifest.json'
    target.parent.mkdir()
    code, out, err = invoke(cli, 'gates', 'discover', '--paths-file', str(_paths(tmp_path, ['services/a/src/app.py'])),
                            '--output', str(target))
    lines = out.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith(f'gate manifest {target} sha256=')
    assert hashlib.sha256(target.read_bytes()).hexdigest() in lines[0]
    manifest = json.loads(target.read_text())
    assert f"gates={len(manifest['gates'])}" in lines[0]
    assert [p.name for p in target.parent.iterdir()] == ['manifest.json']
    plan_target = tmp_path / 'planned.json'
    code, out, err = invoke(cli, 'gates', 'plan', '--file', str(target), '--output', str(plan_target))
    assert out.strip().splitlines()[0].startswith(f'gate manifest {plan_target} sha256=')
    assert json.loads(plan_target.read_text())['gates'] == manifest['gates']


def test_output_is_accepted_on_every_discover_path(cli, repo, tmp_path):
    """--output works with --bounded, with --extend and with both, through the pinned run path (#23)."""
    _fixture(repo)
    start(cli)
    first = tmp_path / 'bounded.json'
    code, out, err = invoke(cli, 'gates', 'discover', '--paths-file', str(_paths(tmp_path, ['packages/shared/src/common.py'])),
                            '--bounded', '--output', str(first))
    assert out.strip().startswith(f'gate manifest {first} sha256=')
    assert json.loads(first.read_text())['discovery_mode'] == 'bounded'
    extended = tmp_path / 'extended.json'
    code, out, err = invoke(cli, 'gates', 'discover', '--output', str(extended), '--extend', 'current', '--bounded',
                            '--paths-file', str(_paths(tmp_path, ['services/b/src/app.py'], 'more.json')))
    assert out.strip().startswith(f'gate manifest {extended} sha256=')
    manifest = json.loads(extended.read_text())
    assert {'packages/shared/src/common.py', 'services/b/src/app.py'} <= set(manifest['paths'])
    plain = tmp_path / 'extended-default.json'
    invoke(cli, 'gates', 'discover', '--extend', str(extended), '--paths-file', str(_paths(tmp_path, ['services/a/src/app.py'], 'third.json')),
           '--output', str(plain))
    assert 'services/a/src/app.py' in json.loads(plain.read_text())['paths']


def test_moved_base_ref_names_the_pinned_sha_and_the_rerun(cli, repo, tmp_path):
    _fixture(repo)
    st = start(cli)
    pinned = st['identity']['merge_base']
    put(repo, 'later.txt', 'moved\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'moved on')
    _git(repo, 'branch', '-f', 'moved-ref', 'HEAD')
    paths = _paths(tmp_path, ['services/a/src/app.py'])
    code, out, err = cli('gates', 'discover', '--paths-file', str(paths), '--base', 'moved-ref')
    message = str(code) + out + err
    assert code != 0 and f'pinned to merge base {pinned}' in message
    assert f'Re-run: gates discover --paths-file {paths} --base {pinned}' in message
    code, out, err = cli('gates', 'discover', '--paths-file', str(paths), '--base', 'no-such-ref')
    assert code != 0 and 'does not resolve to a commit' in str(code) + out + err
