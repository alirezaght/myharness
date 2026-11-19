"""CI-derived check recipes retain their targets, dependencies, cwd and setup."""
import json
import shutil
import sys
from pathlib import Path

import al_ledger as L
import pytest
from evidence_helpers import entries
from test_al_usability import pending

SERVICE = 'services/user-listing-service'
MYPYPATH = 'src:../../packages/pyshared/src:../../packages/contracts/python'
RECIPE_SCOPE = [
    'ci/scripts/fast-check-python.sh',
    'ci/scripts/fast-check-lib.sh',
    f'{SERVICE}/pyproject.toml',
    f'{SERVICE}/uv.lock',
    f'{SERVICE}/src',
    'packages/tooling/ruff/ruff.toml',
    'packages/tooling/mypy/mypy.ini',
    'packages/pyshared/pyproject.toml',
    'packages/pyshared/src',
    'packages/contracts/python/pyproject.toml',
    'packages/contracts/python/src',
]
PROBE = (
    'import json, os, sys; '
    'json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), '
    '"MYPYPATH": os.environ.get("MYPYPATH"), '
    '"UV_NO_SYNC": os.environ.get("UV_NO_SYNC")}, '
    'open(os.environ["RECIPE_PROBE_OUT"], "w"))'
)


@pytest.fixture
def repo(tmp_path, seed_repo):
    root = tmp_path / 'repository with spaces'
    shutil.copytree(seed_repo, root, symlinks=True)
    inputs = {
        'ci/scripts/fast-check-python.sh': '# source-backed fixture\n',
        'ci/scripts/fast-check-lib.sh': '# source-backed wrapper fixture\n',
        f'{SERVICE}/pyproject.toml': '[project]\nname="fixture-service"\n',
        f'{SERVICE}/uv.lock': 'version = 1\n',
        f'{SERVICE}/src/app.py': 'answer = 42\n',
        'packages/tooling/ruff/ruff.toml': 'line-length = 100\n',
        'packages/tooling/mypy/mypy.ini': '[mypy]\n',
        'packages/pyshared/pyproject.toml': '[project]\nname="pyshared"\n',
        'packages/pyshared/src/pyshared.py': 'shared = True\n',
        'packages/contracts/python/pyproject.toml': '[project]\nname="contracts"\n',
        'packages/contracts/python/src/contracts.py': 'contract = True\n',
    }
    for name, content in inputs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def _plan(cli, area, command, *, kind='lint', scope=RECIPE_SCOPE, cwd=SERVICE, env=()):
    args = ['evidence', 'plan', '--area', area, '--kind', kind, '--tier', 'focused',
            '--cwd', cwd, '--scope', ','.join(scope)]
    for name in env:
        args.extend(['--env', name])
    return cli(*args, '--', *command)


def _probe_command(output, *tool_args):
    return ['env', 'UV_NO_SYNC=1', f'MYPYPATH={MYPYPATH}',
            f'RECIPE_PROBE_OUT={output}', sys.executable, '-c', PROBE, *tool_args]


def test_source_recipe_handoff_runs_src_only_with_exact_cwd_and_environment(
        cli, tmp_path, repo, monkeypatch, state_file):
    pending(cli, tmp_path, repo)
    result = cli('writer', 'start', '--id', 'recipe-writer', '--role', 'al-engineer',
                 '--files', f'{SERVICE}/src/app.py', '--degraded',
                 '--reason', 'Exercise the source-bound evidence handoff in an owned fixture.')
    assert result[0] == 0, result

    output = tmp_path / 'probe result.json'
    command = _probe_command(output, 'check', 'src', '--config',
                             '../../packages/tooling/ruff/ruff.toml')
    area = 'user-listing-ci-ruff'
    result = _plan(cli, area, command)
    assert result[0] == 0, result
    plan = cli.state()['evidence']['plan'][area]
    assert plan['argv'] == command
    assert plan['cwd'] == SERVICE
    assert plan['scope'] == RECIPE_SCOPE
    assert 'src' in plan['argv']
    assert not any(target in plan['argv'] for target in ('tests', 'alembic'))

    monkeypatch.setenv('MYPYPATH', 'ambient-wrong-value')
    monkeypatch.setenv('UV_NO_SYNC', '0')
    body = tmp_path / 'source recipe handoff.md'
    body.write_text(
        'CI source: ci/scripts/fast-check-python.sh and ci/scripts/fast-check-lib.sh.\n'
        f'Check cwd: {SERVICE}.\n'
        'Required prepared setup: uv sync --extra dev --frozen.\n'
        f'Child environment: UV_NO_SYNC=1 and MYPYPATH={MYPYPATH}.\n'
        'This component area targets src only; scope separately covers CI, config, lock, '
        'service source, and imported shared/contracts inputs.\n'
    )
    handoff = cli('writer', 'handoff', '--id', 'recipe-writer', '--body-file', str(body))
    assert handoff[0] == 0, handoff
    assert body.read_text() in handoff[1]
    assert f'Area {area}: tier=focused' in handoff[1]
    assert f'evidence run --writer recipe-writer --area {area}' in handoff[1]

    result = cli('evidence', 'run', '--writer', 'recipe-writer', '--area', area)
    assert result[0] == 0, result
    observed = json.loads(output.read_text())
    assert observed == {
        'argv': ['check', 'src', '--config', '../../packages/tooling/ruff/ruff.toml'],
        'cwd': str(repo / SERVICE),
        'MYPYPATH': MYPYPATH,
        'UV_NO_SYNC': '1',
    }
    entry = entries(state_file)[-1]
    assert entry['status'] == 'PASS' and entry['argv'] == command
    assert set(entry['scope']) == set(RECIPE_SCOPE)


def test_env_name_only_fingerprints_but_does_not_provision_missing_value(
        cli, repo, monkeypatch, state_file):
    monkeypatch.delenv('MYPYPATH', raising=False)
    result = cli('start', '--spec', 'Check that evidence env declarations do not assign child values.')
    assert result[0] == 0, result
    scope = [f'{SERVICE}/src']
    command = [sys.executable, '-c',
               'import os; assert "MYPYPATH" not in os.environ; print("unprovided")']
    result = _plan(cli, 'names-only-env', command, kind='check', scope=scope,
                   cwd=SERVICE, env=('MYPYPATH',))
    assert result[0] == 0, result
    result = cli('evidence', 'run', '--area', 'names-only-env')
    assert result[0] == 0, result
    entry = entries(state_file)[-1]
    assert entry['env'] == ['MYPYPATH'] and entry['status'] == 'PASS'
    assert 'MYPYPATH' not in entry['argv']


def test_ci_and_config_scope_changes_stale_pass_and_changed_argv_runs_again(
        cli, repo, state_file):
    result = cli('start', '--spec', 'Bind CI recipe results to declared sources and exact argv.')
    assert result[0] == 0, result
    area = 'user-listing-ci-ruff'
    old_command = _probe_command(repo / 'old.json', 'check', 'src', '--config',
                                 '../../packages/tooling/ruff/ruff.toml')
    assert _plan(cli, area, old_command)[0] == 0
    assert cli('evidence', 'run', '--area', area)[0] == 0
    original_entry = entries(state_file)[-1]
    original_log = Path(original_entry['log']).read_bytes()

    (repo / 'ci/scripts/fast-check-python.sh').write_text('# changed CI recipe source\n')
    code, out, err = cli('evidence', 'require', '--areas', area)
    assert code == 13 and 'STALE' in out + err
    assert original_entry['status'] == 'PASS'
    assert Path(original_entry['log']).read_bytes() == original_log

    assert cli('evidence', 'run', '--area', area)[0] == 0
    second_entry = entries(state_file)[-1]
    assert second_entry['id'] != original_entry['id']
    (repo / 'packages/tooling/ruff/ruff.toml').write_text('line-length = 99\n')
    code, out, err = cli('evidence', 'require', '--areas', area)
    assert code == 13 and 'STALE' in out + err

    new_command = _probe_command(repo / 'new.json', 'check', 'src', '--config',
                                 '../../packages/tooling/ruff/ruff.toml', '--output-format', 'concise')
    assert _plan(cli, area, new_command)[0] == 0
    result = cli('evidence', 'run', '--area', area)
    assert result[0] == 0 and 'reused' not in result[1]
    all_entries = entries(state_file)
    assert len(all_entries) == 3
    assert all_entries[-1]['argv'] == new_command
    assert json.loads((repo / 'new.json').read_text())['argv'] == [
        'check', 'src', '--config', '../../packages/tooling/ruff/ruff.toml',
        '--output-format', 'concise']
    assert L.entry_verdict(cli.state(), original_entry)[0] != 'VALID'


def test_mistaken_wide_fail_is_retired_without_rewriting_its_receipt(
        cli, repo, state_file):
    result = cli('start', '--spec', 'Retain the wide failed check while recording its scoped replacement.')
    assert result[0] == 0, result
    scope = [f'{SERVICE}/src', 'packages/tooling/ruff/ruff.toml']
    wide = [sys.executable, '-c', 'print("1 failed"); raise SystemExit(1)']
    assert _plan(cli, 'wide-lint', wide, kind='lint', scope=scope)[0] == 0
    assert cli('evidence', 'run', '--area', 'wide-lint')[0] != 0
    failed = entries(state_file)[-1]
    assert failed['status'] == 'FAIL'
    failed_log = Path(failed['log']).read_bytes()

    component = [sys.executable, '-c', 'print("component source check passed")']
    assert _plan(cli, 'component-lint', component, kind='lint', scope=scope)[0] == 0
    assert cli('evidence', 'run', '--area', 'component-lint')[0] == 0
    replacement = entries(state_file)[-1]
    assert replacement['status'] == 'PASS'
    result = cli('evidence', 'retire', '--area', 'wide-lint',
                 '--reason', 'The original command targeted a wider set than the source-backed component recipe.',
                 '--replaced-by', replacement['id'])
    assert result[0] == 0, result
    current = cli.state()
    retained = next(item for item in current['evidence']['entries'] if item['id'] == failed['id'])
    assert retained['status'] == 'FAIL'
    assert Path(retained['log']).read_bytes() == failed_log
    assert current['evidence']['retired'][-1]['replaced_by'] == [replacement['id']]
    assert 'wide-lint' not in current['evidence']['plan']
