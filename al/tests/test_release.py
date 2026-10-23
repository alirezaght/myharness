"""Release layout verification rejects incomplete runtime payloads."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_release_layout_command_accepts_complete_runtime_and_excludes_tests():
    al_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(al_root / "release_verify.py"), "--check-layout"],
        cwd=al_root.parent,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)["layout"]
    assert report["status"] == "PASS"
    assert report["platforms"] == ["codex", "claude"]
    assert report["files"] > 0 and report["bytes"] > 0


def test_selected_release_resources_do_not_include_installer_tests_or_caches():
    from al.installer.manifest import load_layout, selected

    al_root = Path(__file__).resolve().parents[1]
    layout = load_layout()
    for path in ("codex/skills/al/tests/test_example.py",
                 "claude/skills/al/__pycache__/al_state.cpython-313.pyc",
                 "codex/skills/al/reference/guide.pyc"):
        assert selected(path, layout) is None
    coverage_resources = {
        'al_architecture_check.py', 'al_ci_verification.py', 'al_python_imports.py',
        'al_repo_adapters.py', 'al_repo_contract.py', 'al_repo_python.py',
        'al_repository_facts.py', 'al_service_coverage.py',
    }
    assert coverage_resources <= set(layout['required_scripts'])
    assert all((al_root / platform / 'skills/al/scripts' / name).is_file()
               for platform in ('codex', 'claude') for name in coverage_resources)


def test_tagged_payload_verification_binds_manifest_to_selected_tag(tmp_path):
    import hashlib
    import os
    from al.tests.test_manifest import git_test, make_source

    source = make_source(tmp_path)
    # A committed cache entry must be excluded alongside committed tests.
    cache = source / 'al/codex/skills/al/__pycache__'
    cache.mkdir()
    (cache / 'never.pyc').write_bytes(b'not runtime')
    git_test(source, 'add', '.')
    git_test(source, 'commit', '-qm', 'excluded cache fixture')
    git_test(source, 'tag', '-f', 'v1.0.0')
    commit = git_test(source, 'rev-parse', 'v1.0.0^{commit}')
    resource = source / 'al/codex/skills/al/SKILL.md'
    committed_hash = hashlib.sha256(resource.read_bytes()).hexdigest()
    dirty = b'dirty checkout bytes must not enter tagged payload\n'
    resource.write_bytes(dirty)
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    environment.pop('AL_RUNTIME_DIR', None)
    environment.pop('AL_DISTRIBUTION_STATE', None)
    command = Path(__file__).resolve().parents[1] / 'release_verify.py'
    result = subprocess.run([sys.executable, '-B', str(command), '--check-release',
                             '--source', str(source), '--version', '1.0.0'],
                            cwd=tmp_path, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report['kind'] == 'tagged-payload'
    manifest = report['manifest']
    assert manifest['commit'] == commit
    assert manifest['files']['codex/skills/al/SKILL.md']['sha256'] == committed_hash
    assert committed_hash != hashlib.sha256(dirty).hexdigest()
    assert all('/tests/' not in path and '/__pycache__/' not in path for path in manifest['files'])
    assert resource.read_bytes() == dirty


import pytest
from al import release_verify as verifier
from al.tests.test_manifest import git_test, make_source, tag_second


def _report(capsys):
    output=capsys.readouterr().out
    try:value=json.loads(output)
    except json.JSONDecodeError:value=json.loads(output.splitlines()[0])
    if 'report' in value:return json.loads(Path(value['report']).read_bytes())
    return value


@pytest.fixture
def release_checkout(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    monkeypatch.setattr(verifier, 'ROOT', source / 'al')
    for key in ('GITHUB_REF', 'GITHUB_SHA', 'GITHUB_ACTIONS', 'GITHUB_REF_TYPE'):
        monkeypatch.delenv(key, raising=False)
    calls = []
    monkeypatch.setattr(verifier, '_run', lambda argv, cwd: calls.append((argv, cwd)))
    def provenance_suites(**options):
        failures=[]
        for suite in ('installer','codex','claude'):
            try:verifier._run([sys.executable,'-m','pytest',suite],source)
            except subprocess.CalledProcessError as exc:failures.append(exc.returncode)
        return {'reconciliation':{'complete':not failures,'selected_success':not failures,'executed_passed':3-len(failures),'executed_failed':len(failures),'executed_errors':0},
                'report_path':str(tmp_path/'provenance-report.json'),'collected_obligations':3,'equivalent_obligations':0}
    monkeypatch.setattr(verifier,'full',provenance_suites)
    return source, calls


def test_full_wiring_is_complete_and_propagates_failure(tmp_path, monkeypatch):
    from al.tests.verification_helpers import source, declarations
    from al.verification_profile import write_json
    root=source(tmp_path,{'installer':'def test_a():\n    assert False\n'})
    write_json(root/'al/verification-obligations.json',declarations(root))
    monkeypatch.setattr(verifier,'ROOT',root/'al')
    result=verifier.full(workers=3,report_path=tmp_path/'full.json')
    assert not result['reconciliation']['complete']
    assert result['reconciliation']['executed_passed']==2
    assert result['reconciliation']['executed_failed']==1
    assert {s['assignment']['suite'] for s in result['shards']}=={'installer','codex','claude'}


@pytest.mark.parametrize('version', [None, '', '1.2', 'v1.0.0-rc.1', '9.9.9'])
def test_payload_requires_existing_explicit_stable_version(release_checkout, capsys, version):
    source, calls = release_checkout
    argv = ['--check-release', '--source', str(source)]
    if version is not None:
        argv += ['--version', version]
    assert verifier.main(argv) == 1
    assert _report(capsys)['status'] == 'FAIL'
    assert not calls


def test_payload_rejects_network_source_without_fetch(capsys):
    assert verifier.main(['--check-release', '--source', 'https://example.invalid/release', '--version', '1.0.0']) == 1
    assert 'local Git source' in _report(capsys)['error']


def test_exact_ci_tag_wins_over_newer_unrelated_tag(release_checkout, monkeypatch, capsys):
    source, calls = release_checkout
    first = git_test(source, 'rev-parse', 'v1.0.0^{commit}')
    tag_second(source)
    git_test(source, 'checkout', '--detach', 'v1.0.0')
    monkeypatch.setenv('GITHUB_REF', 'refs/tags/v1.0.0')
    monkeypatch.setenv('GITHUB_SHA', first)
    assert verifier.main(['--full']) == 0
    report = _report(capsys)
    assert report['kind'] == 'tagged-release'
    assert report['manifest']['tag'] == 'v1.0.0'
    assert report['manifest']['commit'] == first
    assert report['context_before'] == report['context_after']
    assert len(calls) == 3


@pytest.mark.parametrize('sha', ['', '1234567', 'not-a-sha', '0' * 40])
def test_ci_sha_must_match_full_head(release_checkout, monkeypatch, capsys, sha):
    source, calls = release_checkout
    monkeypatch.setenv('GITHUB_REF', 'refs/tags/v1.0.0')
    monkeypatch.setenv('GITHUB_SHA', sha)
    assert verifier.main(['--full']) == 1
    assert 'GITHUB_SHA' in _report(capsys)['error']
    assert not calls


@pytest.mark.parametrize('ref', ['refs/tags/v1.0', 'refs/tags/1.0.0', 'refs/tags/v1.0.0-rc.1', 'refs/tags/', 'refs/unsupported/x'])
def test_invalid_ci_tag_context_fails_closed(release_checkout, monkeypatch, capsys, ref):
    source, calls = release_checkout
    monkeypatch.setenv('GITHUB_REF', ref)
    monkeypatch.setenv('GITHUB_SHA', git_test(source, 'rev-parse', 'HEAD'))
    assert verifier.main(['--full']) == 1
    assert _report(capsys)['status'] == 'FAIL'
    assert not calls


def test_missing_ci_tag_reference_fails_closed(release_checkout, monkeypatch, capsys):
    source, calls = release_checkout
    monkeypatch.setenv('GITHUB_REF_TYPE', 'tag')
    assert verifier.main(['--full']) == 1
    assert 'CI tag reference' in _report(capsys)['error']
    assert not calls


@pytest.mark.parametrize('ci', [False, True])
def test_selected_tag_must_match_tested_head(release_checkout, monkeypatch, capsys, ci):
    source, calls = release_checkout
    tag_second(source)
    if ci:
        monkeypatch.setenv('GITHUB_REF', 'refs/tags/v1.0.0')
        monkeypatch.setenv('GITHUB_SHA', git_test(source, 'rev-parse', 'HEAD'))
    assert verifier.main(['--full', '--version', '1.0.0']) == 1
    assert 'checkout HEAD' in _report(capsys)['error']
    assert not calls


@pytest.mark.parametrize('untracked', [False, True])
def test_dirty_tagged_source_refused_before_suites(release_checkout, capsys, untracked):
    source, calls = release_checkout
    path = source / 'untracked.txt' if untracked else source / 'al/codex/skills/al/SKILL.md'
    path.write_text('local bytes')
    assert verifier.main(['--full', '--version', '1.0.0']) == 1
    assert 'clean tracked and untracked' in _report(capsys)['error']
    assert not calls


@pytest.mark.parametrize('mutation', ['head', 'tag', 'tracked', 'untracked'])
def test_candidate_mutation_during_suites_prevents_release_verdict(release_checkout, monkeypatch, capsys, mutation):
    source, calls = release_checkout
    def mutate(argv, cwd):
        calls.append((argv, cwd))
        if len(calls) != 1:
            return
        if mutation == 'head':
            git_test(source, 'commit', '--allow-empty', '-qm', 'changed during tests')
        elif mutation == 'tag':
            git_test(source, 'tag', '-a', '-f', 'v1.0.0', '-m', 'changed tag object')
        else:
            path = source / 'untracked.txt' if mutation == 'untracked' else source / 'al/codex/skills/al/SKILL.md'
            path.write_text('changed during tests')
    monkeypatch.setattr(verifier, '_run', mutate)
    assert verifier.main(['--full', '--version', '1.0.0']) == 1
    report = _report(capsys)
    assert report['status'] == 'FAIL' and 'changed during suites' in report['error']
    assert 'manifest' not in report
    assert len(calls) == 3


@pytest.mark.parametrize('ref', [None, 'refs/heads/main'])
def test_local_and_manual_branch_checks_are_honest_development(release_checkout, monkeypatch, capsys, ref):
    source, calls = release_checkout
    tag_second(source)
    (source / 'al/codex/skills/al/SKILL.md').write_text('dirty development bytes')
    if ref:
        monkeypatch.setenv('GITHUB_REF', ref)
        monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    assert verifier.main(['--full']) == 0
    report = _report(capsys)
    assert report['kind'] == 'development' and 'manifest' not in report
    assert report['context_before']['head'] == git_test(source, 'rev-parse', 'HEAD')
    assert report['context_before']['dirty'] is True
    assert len(calls) == 3


def test_explicit_full_tag_succeeds_and_external_source_refuses(release_checkout, capsys):
    source, calls = release_checkout
    assert verifier.main(['--full', '--version', '1.0.0']) == 0
    report = _report(capsys)
    assert report['kind'] == 'tagged-release'
    assert report['manifest']['commit'] == git_test(source, 'rev-parse', 'HEAD')
    calls.clear()
    assert verifier.main(['--full', '--source', str(source), '--version', '1.0.0']) == 1
    assert '--source' in _report(capsys)['error']
    assert not calls


def test_full_failure_is_not_successful_verification(release_checkout, monkeypatch, capsys):
    source, calls = release_checkout
    def fail(argv, cwd):
        calls.append((argv, cwd))
        raise subprocess.CalledProcessError(9, argv)
    monkeypatch.setattr(verifier, '_run', fail)
    assert verifier.main(['--full', '--version', '1.0.0']) == 1
    assert _report(capsys)['status'] == 'FAIL'
    assert len(calls) == 3
