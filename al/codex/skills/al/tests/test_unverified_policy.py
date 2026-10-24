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
    # A missing tool is AL's environment gap even when the base run happened not to need it.
    assert 'environment gap' in G.accepted_failure(st('E5'), {}, touched, 'candidate', runs['E4'], run, 'f')
    assert 'environment gap' in G.accepted_failure(st('E6'), {}, touched, 'base', runs['E6'], run, 'f')
    assert G.accepted_failure(st('E1'), {}, untouched, 'candidate', runs['E2'], dict(run, fingerprint='old'), 'f') is None


def test_disposable_checkouts_link_the_working_trees_node_modules(tmp_path, monkeypatch):
    import subprocess
    import al_checkout_paths as P
    import al_checkouts as C
    import al_store
    work, root = tmp_path / 'work', tmp_path / 'checkout'
    for base in (work, root):
        (base / 'apps/web').mkdir(parents=True)
        (base / 'package.json').write_text('{}');(base / 'apps/web/package.json').write_text('{}')
    (work / 'node_modules/.bin').mkdir(parents=True);(work / 'apps/web/node_modules').mkdir()
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    monkeypatch.setattr(al_store, 'REPO_ROOT', work)
    assert sorted(C.link_dependencies(root)) == ['.', 'apps/web']
    assert (root / 'node_modules').is_symlink() and (root / 'node_modules/.bin').is_dir()
    P.validate(root)
    status = subprocess.run(['git', 'status', '--porcelain'], cwd=root, capture_output=True, text=True).stdout
    assert 'node_modules' not in status


def test_a_touched_gate_reports_only_failures_the_base_did_not_already_have(tmp_path, monkeypatch):
    import al_gates as G
    import al_ledger as L
    base = 'FAILED tests/test_a.py::test_old - AssertionError\n1 failed in 2.31s'
    runs = {'B': _run(tmp_path, 'B', 'FAIL', 1, base),
            'same': _run(tmp_path, 'same', 'FAIL', 1, base.replace('2.31s', '9.02s')),
            'new': _run(tmp_path, 'new', 'FAIL', 1, base + '\nFAILED tests/test_b.py::test_new - KeyError')}
    monkeypatch.setattr(L, 'find', lambda st, eid: runs.get(eid))
    monkeypatch.setattr(G, 'touched_units', lambda st, m: {'.'})
    st = {'gate_runs': [{'gate': 'g', 'stage': 'base', 'manifest': 'm', 'evidence': 'B'}]}
    run, gate = {'fingerprint': 'f', 'manifest': 'm'}, {'id': 'g', 'units': ['.']}
    assert 'every failure' in G.accepted_failure(st, {}, gate, 'candidate', runs['same'], run, 'f')
    assert G.accepted_failure(st, {}, gate, 'candidate', runs['new'], run, 'f') is None
    st['gate_decisions'] = {'g': {'action': 'fix'}}
    assert G.accepted_failure(st, {}, gate, 'candidate', runs['same'], run, 'f') is None


@pytest.mark.parametrize('text,gap', [
    ('sh: 1: turbo: not found', True),
    ('.husky/pre-commit: 2: .: cannot open /tmp/al-gate/checkout/.husky/_/husky.sh: No such file', True),
    ('make: ./node_modules/.bin/eslint: No such file or directory', True),
    ('src/app.py: No such file or directory', False),
    ('AssertionError: expected 2', False),
])
def test_a_missing_tool_or_ignored_install_is_an_environment_gap_in_any_ecosystem(tmp_path, monkeypatch, text, gap):
    import subprocess
    import al_gates as G
    import al_store
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    (tmp_path / '.gitignore').write_text('node_modules/\n.husky/_/\n')
    monkeypatch.setattr(al_store, 'REPO_ROOT', tmp_path)
    assert G.environment_gap(_run(tmp_path, 'E', 'FAIL', 1, text)) is gap


@pytest.mark.parametrize('command,denied', [
    ('sort -u notes.txt', False), ('diff -u a b', False), ('uniq -u x', False),
    ('python3 -m unittest discover', False), ('python3 -m mypy src', False), ('python3 -m ruff check .', False),
    ('python3 -m ruff format .', True), ('jest -u', True), ('eslint --fix .', True), ('sort -o out.txt in.txt', True),
])
def test_read_only_guard_knows_what_short_flags_mean_for_each_tool(command, denied):
    import al_shell
    assert bool(al_shell.check(command, 'private')) is denied


def test_a_project_manifest_of_any_ecosystem_makes_its_directory_a_unit(tmp_path, monkeypatch):
    import al_gate_inventory as V
    import al_store
    for manifest in ('cmd/tool/go.mod', 'crates/core/Cargo.toml', 'java/api/pom.xml', 'dotnet/Web/Web.csproj'):
        (tmp_path / manifest).parent.mkdir(parents=True);(tmp_path / manifest).write_text('')
    (tmp_path / 'docs').mkdir()
    monkeypatch.setattr(al_store, 'REPO_ROOT', tmp_path)
    assert V.classify_unit('cmd/tool/internal/x.go', set()) == 'cmd/tool'
    assert V.classify_unit('crates/core/src/lib.rs', set()) == 'crates/core'
    assert V.classify_unit('java/api/src/main/A.java', set()) == 'java/api'
    assert V.classify_unit('dotnet/Web/Program.cs', set()) == 'dotnet/Web'
    assert V.classify_unit('docs/readme.md', set()) == '.'


def test_too_many_github_workflows_are_left_to_the_pipeline(tmp_path):
    import al_ci_inventory as CI
    workflows = tmp_path / '.github/workflows';workflows.mkdir(parents=True)
    for index in range(129):(workflows / f'w{index}.yml').write_text('on: push\njobs: {}\n')
    result = CI.inventory(tmp_path, ['src/a.py'])
    assert [(o['job'], o['applicability']) for o in result['obligations']] == [('<unresolved-workflows>', 'UNRESOLVED')]
