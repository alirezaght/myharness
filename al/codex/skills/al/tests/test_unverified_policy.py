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
