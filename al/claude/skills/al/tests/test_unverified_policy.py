import pytest

import al_python_imports as Imports
import al_repo_python as Python

FILES = [
    'apps/web/package.json', 'apps/admin/package.json', 'apps/web/src/index.ts',
    'packages/ui/package.json', 'packages/tools/lint/package.json',
    'packages/ui/node_modules/dep/package.json', 'services/api/pyproject.toml',
]


def test_workspace_globs_expand_to_manifest_directories():
    assert Python.expand_members('apps/*', FILES) == {'apps/web', 'apps/admin'}
    assert Python.expand_members('packages/**', FILES) == {'packages/ui', 'packages/tools/lint'}
    assert Python.expand_members('services/*', FILES) == {'services/api'}


@pytest.mark.parametrize('pattern', ['apps/we*', 'packages/**/lint', 'apps/[ab]*'])
def test_partial_globs_stay_unsupported(pattern):
    with pytest.raises(ValueError):
        Python.expand_members(pattern, FILES)


def test_import_digest_changes_with_any_import_and_ignores_order():
    before = {('base', 'a.py', 'os', 'static'), ('current', 'a.py', 'os', 'static')}
    assert Imports._targets_digest(before) == Imports._targets_digest(set(reversed(sorted(before))))
    changed = before | {('current', 'a.py', 'json', 'static')}
    assert Imports._targets_digest(changed) != Imports._targets_digest(before)
    dynamic = {('base', 'a.py', 'os', 'static'), ('current', 'a.py', 'os', 'dynamic')}
    assert Imports._targets_digest(dynamic) != Imports._targets_digest(before)


def _task(status, files, revision=1):
    return {'status': status, 'files': files, 'revision': revision,
            'progress': {'completed': 1, 'total': 1, 'unit': 'task'}, 'completed_at': 1.0,
            'admission': {'revision': revision, 'graph_revision': 1}}


def test_rework_reopens_only_completed_tasks_owning_the_reworked_files():
    import al_rework
    st = {'integration': {'tasks': {
        'T1': _task('Completed', ['tests/test_export.py', 'demo']),
        'T2': _task('Completed', ['README.md']),
        'T3': _task('Running', ['demo']),
    }}}
    assert al_rework._reopen_tasks(st, ['demo/export.py']) == ['T1']
    tasks = st['integration']['tasks']
    assert tasks['T1']['status'] == 'Waiting' and tasks['T1']['revision'] == 2
    assert 'admission' not in tasks['T1'] and 'completed_at' not in tasks['T1']
    assert tasks['T2']['status'] == 'Completed' and tasks['T3']['status'] == 'Running'
    assert [(e['kind'], e['payload']['status']) for e in st['integration']['pending']] == [('task.updated', 'Waiting')]


def test_rework_without_files_reopens_every_completed_task_and_ignores_unbound_runs():
    import al_rework
    st = {'integration': {'tasks': {'T1': _task('Completed', ['a']), 'T2': _task('Completed', ['b'])}}}
    assert al_rework._reopen_tasks(st, []) == ['T1', 'T2']
    assert al_rework._reopen_tasks({}, ['a']) == []


@pytest.mark.parametrize('config,expected', [
    ({'project': {'dependencies': ['import-linter==2.15']}}, []),
    ({'dependency-groups': {'dev': ['import-linter']}}, []),
    ({'dependency-groups': {'lint': ['import_linter>=2']}}, ['--group', 'lint']),
    ({'project': {'optional-dependencies': {'dev': ['import-linter==2.15']}}}, ['--extra', 'dev']),
    ({'project': {'optional-dependencies': {'docs': ['sphinx']}}}, []),
])
def test_import_linter_runs_from_the_extra_or_group_that_declares_it(config, expected):
    assert Python.linter_selection(config) == expected


def test_scopes_compact_to_complete_directories_and_never_to_the_root():
    import al_gate_inventory as V
    universe = {'svc/a.py', 'svc/b.py', 'svc/sub/c.py', 'lib/x.py', 'lib/y.py', 'pyproject.toml'}
    assert V.compact({'svc/a.py', 'svc/b.py', 'svc/sub/c.py', 'lib/x.py', 'pyproject.toml'}, universe) == ['lib/x.py', 'pyproject.toml', 'svc']
    assert V.compact(universe, universe) == ['lib', 'pyproject.toml', 'svc']


def test_execution_key_binds_the_scope_by_digest_not_by_copy():
    import al_gate_reuse as R
    gate = {'argv': ['true'], 'cwd': '.', 'execution_kind': 'local', 'scope': ['svc/a.py'] * 1000, 'env_names': []}
    key = R.execution_key({'run_id': 'r'}, {'revision': 1, 'base_oid': 'b'}, 'm', gate, 'base', {'fingerprint': 'f'}, 'e', None)
    assert 'scope' not in key and len(key['scope_sha256']) == 64
    other = R.execution_key({'run_id': 'r'}, {'revision': 1, 'base_oid': 'b'}, 'm', dict(gate, scope=['svc/b.py']), 'base', {'fingerprint': 'f'}, 'e', None)
    assert other['scope_sha256'] != key['scope_sha256']


def test_a_single_new_git_location_is_hook_state_but_git_metadata_is_not():
    import al_gate_reuse as R
    before = {'metadata': {'.': ['dir', 493], 'HEAD': ['file', 420, 'a']}, 'work': {'a.py': ['file', 420, 'x']}}
    hook = {'metadata': {**before['metadata'], 'codex-review': ['dir', 493], 'codex-review/s.json': ['file', 384, 'b']}, 'work': before['work']}
    assert R.hook_state(before, hook) == 'codex-review'
    assert any('.git/codex-review' in line for line in R.drift_report(before, hook))
    owned = {'metadata': {**before['metadata'], 'HEAD': ['file', 420, 'changed']}, 'work': before['work']}
    assert R.hook_state(before, owned) is None
    two = {'metadata': {**hook['metadata'], 'other': ['dir', 493]}, 'work': before['work']}
    assert R.hook_state(before, two) is None
    edited = {'metadata': hook['metadata'], 'work': {'a.py': ['file', 420, 'y']}}
    assert R.hook_state(before, edited) is None


def _run(tmp_path, name, status, exit_code, text):
    log = tmp_path / (name + '.log');log.write_text(text)
    return {'id': name, 'status': status, 'exit': exit_code, 'log': str(log)}


def test_pre_existing_failures_and_environment_gaps_are_reported_not_blocking(tmp_path, monkeypatch):
    import al_gates as G
    import al_ledger as L
    runs = {'E1': _run(tmp_path, 'E1', 'FAIL', 1, 'broken contract'), 'E2': _run(tmp_path, 'E2', 'FAIL', 1, 'broken contract'),
            'E3': _run(tmp_path, 'E3', 'FAIL', 1, 'turbo: command not found'), 'E4': _run(tmp_path, 'E4', 'FAIL', 1, 'turbo: command not found'),
            'E5': _run(tmp_path, 'E5', 'PASS', 0, 'ok'), 'E6': _run(tmp_path, 'E6', 'FAIL', 127, '')}
    monkeypatch.setattr(L, 'find', lambda st, eid: runs.get(eid))
    monkeypatch.setattr(G, 'touched_units', lambda st, m: {'services/touched'})
    def st(base_evidence):
        return {'gate_runs': [{'gate': 'g', 'stage': 'base', 'manifest': 'm', 'evidence': base_evidence}]}
    run = {'fingerprint': 'f', 'manifest': 'm'}
    untouched, touched = {'id': 'g', 'units': ['services/other']}, {'id': 'g', 'units': ['services/touched']}
    assert 'pre-existing' in G.accepted_failure(st('E1'), {}, untouched, 'candidate', runs['E2'], run, 'f')
    assert G.accepted_failure(st('E1'), {}, touched, 'candidate', runs['E2'], run, 'f') is None
    assert G.accepted_failure(st('E5'), {}, untouched, 'candidate', runs['E2'], run, 'f') is None
    assert 'environment gap' in G.accepted_failure(st('E3'), {}, touched, 'candidate', runs['E4'], run, 'f')
    assert G.accepted_failure(st('E5'), {}, touched, 'candidate', runs['E4'], run, 'f') is None
    assert 'environment gap' in G.accepted_failure(st('E6'), {}, touched, 'base', runs['E6'], run, 'f')
    assert G.accepted_failure(st('E1'), {}, untouched, 'candidate', runs['E2'], dict(run, fingerprint='old'), 'f') is None
