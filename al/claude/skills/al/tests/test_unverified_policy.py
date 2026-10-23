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
