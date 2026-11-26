"""Development change verification: affected-test selection and the parallel runner path."""
import json
from pathlib import Path

import pytest

from al.verification_affected import select
from al.verify_changed import main
from al.tests.verification_helpers import source

HOST = 'al/codex/skills/al'


def tree(tmp_path):
    root = tmp_path / 'tree'
    files = {
        HOST + '/scripts/al_base.py': 'VALUE = 1\n',
        HOST + '/scripts/al_user.py': 'from al_base import VALUE\n',
        HOST + '/scripts/al_loader.py': 'import importlib\nMODULE = importlib.import_module("al_dynamic")\n',
        HOST + '/scripts/al_dynamic.py': 'X = 1\n',
        HOST + '/scripts/al_named.py': 'Y = 1\n',
        HOST + '/reference/guide.md': '# guide\n',
        HOST + '/tests/conftest.py': 'import al_base\n\ndef fixture():\n    from fixture_helpers import make\n',
        HOST + '/tests/fixture_helpers.py': 'def make():\n    return 1\n',
        HOST + '/tests/shared_helpers.py': 'def helper():\n    return 1\n',
        HOST + '/tests/test_base.py': 'import al_base\n',
        HOST + '/tests/test_user.py': 'import al_user\n',
        HOST + '/tests/test_loader.py': 'import al_loader\n',
        HOST + '/tests/test_named.py': 'def test_named():\n    pass\n',
        HOST + '/tests/test_guide.py': 'GUIDE = "reference/guide.md"\n',
        HOST + '/tests/test_helper_user.py': 'from shared_helpers import helper\n',
        HOST + '/tests/test_parity.py': 'def test_parity():\n    pass\n',
        'al/claude/skills/al/tests/test_parity.py': 'def test_parity():\n    pass\n',
        'al/claude/skills/al/tests/test_other.py': 'def test_other():\n    pass\n',
        'al/installer/pin.py': 'from .store import save\n',
        'al/installer/store.py': 'def save():\n    pass\n',
        'al/installer/__init__.py': '',
        'al/tests/test_pin.py': 'from al.installer.pin import save\n',
        'al/tests/test_unrelated.py': 'def test_unrelated():\n    pass\n',
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def chosen(root, *changed, depth=1):
    result = select(root, list(changed), depth=depth)
    return {suite + ':' + path for suite, paths in result['tests'].items() for path in paths}, result


GUARDS = {'codex:tests/test_parity.py', 'claude:tests/test_parity.py'}


def test_imports_depth_and_guards_select_affected_host_tests(tmp_path):
    root = tree(tmp_path)
    direct, _ = chosen(root, HOST + '/scripts/al_base.py', depth=0)
    assert direct == {'codex:tests/test_base.py'} | GUARDS
    reached, _ = chosen(root, HOST + '/scripts/al_base.py', depth=1)
    assert reached == {'codex:tests/test_base.py', 'codex:tests/test_user.py'} | GUARDS


def test_a_changed_claude_copy_of_a_shared_script_selects_the_single_run_codex_tests(tmp_path):
    # Host-neutral tests run once, in the Codex suite (#36); a byte-identical Claude copy of a
    # shared script is the same code, so changing it selects them too. A differing copy does not.
    root = tree(tmp_path)
    claude = root / 'al/claude/skills/al/scripts/al_base.py'
    claude.parent.mkdir(parents=True, exist_ok=True)
    claude.write_text('VALUE = 1\n')
    shared, _ = chosen(root, 'al/claude/skills/al/scripts/al_base.py', depth=0)
    assert {'codex:tests/test_base.py'} | GUARDS <= shared
    claude.write_text('VALUE = 2\n')
    differing, _ = chosen(root, 'al/claude/skills/al/scripts/al_base.py', depth=0)
    assert 'codex:tests/test_base.py' not in differing


def test_string_references_naming_convention_and_helpers(tmp_path):
    root = tree(tmp_path)
    assert chosen(root, HOST + '/scripts/al_dynamic.py')[0] == {'codex:tests/test_loader.py'} | GUARDS
    assert chosen(root, HOST + '/scripts/al_named.py')[0] == {'codex:tests/test_named.py'} | GUARDS
    assert chosen(root, HOST + '/reference/guide.md')[0] == {'codex:tests/test_guide.py'} | GUARDS
    assert chosen(root, HOST + '/tests/shared_helpers.py')[0] == {'codex:tests/test_helper_user.py'} | GUARDS
    assert chosen(root, HOST + '/tests/test_named.py')[0] == {'codex:tests/test_named.py'} | GUARDS


def test_installer_relative_imports_resolve(tmp_path):
    root = tree(tmp_path)
    assert chosen(root, 'al/installer/store.py')[0] == {'installer:al/tests/test_pin.py'}


@pytest.mark.parametrize('changed,suites', [
    (HOST + '/tests/conftest.py', ['codex']),
    (HOST + '/tests/fixture_helpers.py', ['codex']),
    ('pyproject.toml', ['claude', 'codex', 'installer']),
    (HOST + '/scripts/al_removed.py', ['codex']),
    ('al/installer/unreached.py', ['claude', 'codex', 'installer']),
])
def test_infrastructure_deleted_and_unreached_files_fall_back_to_complete_suites(tmp_path, changed, suites):
    root = tree(tmp_path)
    if changed == 'al/installer/unreached.py':
        (root / changed).write_text('UNUSED = 1\n')
    selected, result = chosen(root, changed)
    assert result['full'] == suites
    for suite in suites:
        everything = {p.name for p in (root / ('al/tests' if suite == 'installer' else 'al/' + suite + '/skills/al/tests')).glob('test_*.py')}
        assert {Path(s.split(':', 1)[1]).name for s in selected if s.startswith(suite + ':')} == everything


def test_changes_outside_al_select_nothing(tmp_path):
    selected, result = chosen(tree(tmp_path), 'harness/cli.py', 'docs/notes.md')
    assert selected == set() and result['full'] == []


def test_runs_selected_files_in_parallel_and_reports_failures(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr('tempfile.tempdir', str(tmp_path / 'tmp'))
    (tmp_path / 'tmp').mkdir()
    root = source(tmp_path, {'codex': 'import pure\n\ndef test_a():\n    assert pure.VALUE == 3\n',
                             'claude': 'import pure\n\ndef test_a():\n    assert pure.VALUE == 2\n'})
    for suite in ('codex', 'claude'):
        (root / 'al' / suite / 'skills/al/tests/conftest.py').write_text(
            'import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "scripts"))\n')
    report = tmp_path / 'reports' / 'report.json'
    code = main(['--path', 'al/codex/skills/al/scripts/pure.py', '--path', 'al/claude/skills/al/scripts/pure.py',
                 '--report', str(report), '--workers', '2'], root=root)
    out = capsys.readouterr().out
    assert code == 1
    assert 'FAILED codex:tests/test_a.py::test_a' in out
    assert 'assert 2 == 3' in out
    assert '1 passed, 1 failed' in out
    assert 'selected (fast tier): installer=0, codex=1, claude=1' in out
    assert report.is_file()

    (root / 'al/codex/skills/al/tests/test_a.py').write_text('import pure\n\ndef test_a():\n    assert pure.VALUE == 2\n')
    code = main(['--path', 'al/codex/skills/al/tests/test_a.py', '--report', str(tmp_path / 'again' / 'report.json')], root=root)
    out = capsys.readouterr().out
    assert code == 0 and '1 passed, 0 failed' in out and out.rstrip().splitlines()[-1].startswith('PASS')


@pytest.mark.parametrize('suite,groups', [('installer', 1), ('codex', 3)])
def test_split_preserves_installer_whole_files_and_splits_ordinary_host_files(tmp_path, monkeypatch, capsys, suite, groups):
    monkeypatch.setattr('tempfile.tempdir', str(tmp_path / 'tmp'))
    (tmp_path / 'tmp').mkdir()
    body = 'import os\nPID = os.getpid()\n' + ''.join(f'def test_{i}():\n    assert PID == os.getpid()\n' for i in range(5))
    root = source(tmp_path, {suite: body})
    changed = 'al/tests/test_a.py' if suite == 'installer' else HOST + '/tests/test_a.py'
    report = tmp_path / 'reports' / 'report.json'
    assert main(['--path', changed, '--split', '2', '--report', str(report)], root=root) == 0
    value = json.loads(report.read_text())
    assert len(value['verification']['assignments']) == groups
    assert value['verification']['reconciliation']['executed_passed'] == 5
    assert main(['--path', changed, '--split', '0', '--report', str(tmp_path / 'whole' / 'r.json')], root=root) == 0
    assert len(json.loads((tmp_path / 'whole' / 'r.json').read_text())['verification']['assignments']) == 1
    capsys.readouterr()


def test_report_inside_the_checkout_is_refused(tmp_path):
    root = source(tmp_path)
    with pytest.raises(SystemExit):
        main(['--path', 'al/tests/test_a.py', '--report', str(root / 'report.json')], root=root)


def test_fast_tier_defers_slow_files_and_always_runs_the_guards(tmp_path, capsys):
    root = tree(tmp_path)
    (root / 'al/verification-tiers.json').write_text(json.dumps({'schema_version': 1, 'slow': {'codex:tests/test_user.py': {}}}))
    assert main(['--path', HOST + '/scripts/al_base.py', '--list'], root=root) == 0
    fast = capsys.readouterr().out
    assert 'codex:tests/test_base.py' in fast and 'codex:tests/test_user.py' not in fast
    assert 'codex:tests/test_parity.py' in fast and '1 affected slow test file(s) deferred' in fast
    assert main(['--path', 'al/installer/store.py', '--list'], root=root) == 0
    assert 'claude:tests/test_parity.py' in capsys.readouterr().out
    assert main(['--path', HOST + '/scripts/al_base.py', '--list', '--tier', 'all'], root=root) == 0
    assert 'codex:tests/test_user.py' in capsys.readouterr().out


@pytest.mark.parametrize('profile', [False, True])
def test_profile_is_forwarded_and_reported_without_a_release_claim(tmp_path, monkeypatch, capsys, profile):
    import al.verification_runner as runner
    root = source(tmp_path)
    observed = []
    original = runner.run
    def run(*args, **kwargs):
        observed.append(kwargs.copy())
        return original(*args, **kwargs)
    monkeypatch.setattr(runner, 'run', run)
    report = tmp_path / 'reports' / 'profile.json'
    args = ['--path', 'al/tests/test_a.py', '--report', str(report)]
    if profile:args.append('--profile')
    assert main(args, root=root) == 0
    value = json.loads(report.read_text())
    assert observed[0]['profile'] is profile
    assert observed[0]['cache_mode'] == 'cold'
    assert value['profile'] is profile
    assert value['release_claim'] is False and value['source_stable'] is True
    assert value['verification']['reconciliation']['executed_passed'] == 1
    assert 'development check, not a release result' in capsys.readouterr().out


@pytest.mark.parametrize('condition', ['timing-only', 'failed', 'missing', 'source-movement', 'reconciliation-error'])
def test_controlled_timing_report_preserves_functional_guards(tmp_path, monkeypatch, capsys, condition):
    import al.verify_changed as verifier
    import al.verification_plan as planning
    import al.verification_runner as runner
    from al.verification_profile import capture_source_binding

    root = source(tmp_path)
    temporary = tmp_path / 'tmp';temporary.mkdir()
    monkeypatch.setattr('tempfile.tempdir', str(temporary))
    node = 'installer:al/tests/test_a.py::test_a'
    plan = {'execution_assignments': [{'id': node}], 'shared_equivalents': [],
            'collected_obligations': [node], 'intentionally_unselected': [], 'complete_required': False}
    binding = capture_source_binding(root)
    monkeypatch.setattr(verifier, '_plan', lambda *a, **kw: (plan, binding))
    log = tmp_path / 'controlled.log';log.write_text('controlled verification report\n')
    durations = {node: 75.0}

    def controlled_run(*args, **kwargs):
        outcomes = {} if condition == 'missing' else {node: 'failed' if condition == 'failed' else 'passed'}
        shard = {'outcomes': outcomes, 'durations': durations,
                 'errors': ['controlled reconciliation failure'] if condition == 'reconciliation-error' else [],
                 'log': str(log)}
        if condition == 'source-movement':
            (root / 'al/tests/test_a.py').write_text('def test_a():\n    assert False\n')
        return {'shards': [shard], 'reconciliation': planning.reconcile(plan, [shard])}

    monkeypatch.setattr(runner, 'run', controlled_run)
    # This report-seam test makes no process-settlement claim.
    monkeypatch.setattr(planning, 'finalize_verification_storage', lambda *a: {'complete': True})
    report = tmp_path / 'reports/timing.json'
    expected = condition == 'timing-only'
    assert main(['--tier', 'all', '--path', 'al/tests/test_a.py', '--report', str(report)], root=root) == (0 if expected else 1)
    value = json.loads(report.read_text())
    assert value['status'] == ('PASS' if expected else 'FAIL')
    assert value['release_claim'] is False
    assert value['over_budget'] == [
        'file 75.0 s > 60 s: installer:al/tests/test_a.py',
        'test 75.0 s > 5 s: installer:al/tests/test_a.py::test_a',
    ]
    assert value['verification']['shards'][0]['durations'] == durations
    assert value['source_stable'] is (condition != 'source-movement')
    if condition == 'missing':
        assert value['verification']['reconciliation']['missing'] == [node]
    if condition == 'failed':
        assert value['verification']['reconciliation']['failed'] == [node]
    if condition == 'reconciliation-error':
        assert value['verification']['reconciliation']['errors'] == ['controlled reconciliation failure']
    assert 'reported only; functional status unchanged' in capsys.readouterr().out
