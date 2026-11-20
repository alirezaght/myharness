"""Go summaries and unavailable launchers cannot manufacture verification passes."""
import copy
import shutil
from pathlib import Path

import pytest

import al_ledger as L
from evidence_helpers import entries, git, ok_cmd


@pytest.mark.parametrize('text,expected', [
    ('DONE 10 tests, 2 skipped in 0.3s', {'passed': 8, 'skipped': 2}),
    ('DONE 10 tests, 2 skipped, 1 failure in 0.3s', {'passed': 7, 'skipped': 2, 'failed': 1}),
    ('DONE 10 tests, 2 skipped, 1 error in 0.3s', {'passed': 8, 'skipped': 2, 'error': 1}),
    ('DONE 0 tests, 1 error in 0.3s', {'passed': 0, 'error': 1}),
    ('DONE 2 tests, 2 skipped in 0.3s', {'passed': 0, 'skipped': 2}),
    ('DONE 0 tests in 0.3s', {'passed': 0}),
    ('DONE 1 test in 1m2.3s', {'passed': 1}),
    ('\x1b[32mDONE\x1b[0m 10 tests, 2 skipped in 300ms', {'passed': 8, 'skipped': 2}),
    ('3 passed in 0.1s\nDONE 10 tests, 2 skipped in 0.3s', {'passed': 8, 'skipped': 2}),
    ('DONE 10 tests, 2 failures in 1s\nDONE 10 tests in 1s', {'passed': 10, 'failed': 2}),
])
def test_gotestsum_total_and_skip_failure_build_error_counts(text, expected):
    assert L.parse_counts(text) == expected


@pytest.mark.parametrize('kind', ['lint', 'check', 'test', 'build', 'typecheck'])
@pytest.mark.parametrize('code', [0, 1])
def test_missing_required_tool_diagnostic_is_infrastructure_even_when_wrapper_succeeds(kind, code):
    status, note = L.classify(kind, code, {'passed': 3}, False, 'same', 'same',
                              output='/bin/sh: gofmt: command not found\n')
    assert status == 'INFRA'
    assert 'unavailable executable' in note


@pytest.mark.parametrize('line', [
    '/bin/sh: 1: go: not found',
    '/bin/sh: find: command not found',
    '/bin/bash: line 2: gofmt: command not found',
    'zsh:1: command not found: go',
    'make: gofmt: No such file or directory',
    'make[1]: go: No such file or directory',
    "env: 'go': No such file or directory",
])
def test_closed_launcher_diagnostics(line):
    assert L.classify('check', 0, {}, False, 'same', 'same', output=line)[0] == 'INFRA'


@pytest.mark.parametrize('text', [
    'The guide explains command not found errors.',
    '# /bin/sh: gofmt: command not found',
    '"/bin/sh: gofmt: command not found"',
    'go: open fixture.go: No such file or directory',
    'find: optional-generated: No such file or directory',
    'format check completed',
])
def test_words_and_quoted_examples_are_not_launcher_failures(text):
    assert L.classify('check', 0, {}, False, 'same', 'same', output=text)[0] == 'PASS'


def test_missing_tool_does_not_hide_source_drift_or_timeout():
    output = '/bin/sh: go: not found'
    assert L.classify('check', 0, {}, False, 'old', 'new', output=output)[0] == 'TORN'
    assert L.classify('check', 0, {}, True, 'same', 'same', output=output) == ('INFRA', 'timed out')


@pytest.mark.parametrize('output', [
    'DONE 1 test, 2 skipped in 1s',
    'DONE 4 tests, 1 skipped, 1 skipped in 1s',
    'DONE 4 tests, unrecognised counts in 1s',
])
def test_invalid_recognised_go_summary_is_not_unknown_count_pass(output):
    assert L.classify('test', 0, L.parse_counts(output), False, 'same', 'same', output=output)[0] == 'INFRA'


def test_public_make_format_check_cannot_pass_when_gofmt_is_missing(cli, repo, tmp_path, state_file):
    make = shutil.which('make')
    assert make, 'make is a required fixture prerequisite'
    empty = tmp_path / 'empty-path'; empty.mkdir()
    (repo / 'Makefile').write_text('format-check:\n\t@test -z "$$(gofmt -l .)"\n')
    git(repo, 'add', 'Makefile'); git(repo, 'commit', '-qm', 'Add format-check fixture')
    raw = cli.raw
    assert raw('start', '--spec', 'Classify a real formatting wrapper with unavailable tooling.')[0] == 0
    code, out, err = raw('evidence', 'run', '--area', 'format', '--kind', 'lint',
                         '--scope', 'Makefile', '--', '/usr/bin/env', f'PATH={empty}', make, 'format-check')
    assert code != 0, (out, err)
    e = entries(state_file)[-1]
    assert e['exit'] == 0  # The wrapper really hid the subprocess failure.
    assert e['status'] == 'INFRA'
    assert raw('evidence', 'require', '--areas', 'format')[0] != 0


def test_public_gotestsum_summary_has_passes_and_skips(cli, state_file):
    raw = cli.raw
    assert raw('start', '--spec', 'Parse a standard Go runner summary without losing actual counts.')[0] == 0
    code, out, err = raw('evidence', 'run', '--area', 'go-summary', '--scope', 'svc',
                         '--', *ok_cmd('DONE 10 tests, 2 skipped in 0.3s'))
    assert code == 0, (out, err)
    e = entries(state_file)[-1]
    assert e['status'] == 'PASS' and e['counts'] == {'passed': 8, 'skipped': 2}


@pytest.mark.parametrize('body,kind,counts,expected', [
    ('/bin/sh: gofmt: command not found\n', 'lint', {}, 'INFRA'),
    ('DONE 10 tests, 2 skipped, 1 failure in 1s\n', 'test', {'passed': 10}, 'FAIL'),
])
def test_historical_false_pass_is_unusable_without_rewriting_receipt(tmp_path, monkeypatch, body, kind, counts, expected):
    log = tmp_path / 'old.log'
    log.write_text('$ make format-check\n# cwd: .\n# started: fixture time\n\n' + body + '\n# termination: {}\n')
    e = {'status': 'PASS', 'kind': kind, 'argv': ['make', 'format-check'], 'cwd': '.',
         'exit': 0, 'counts': counts, 'log': str(log), 'log_sha256': L.file_sha(log),
         'scope': ['svc'], 'fp_before': 'same', 'fp_after': 'same'}
    before = copy.deepcopy(e)
    monkeypatch.setattr(L, 'scope_fingerprint', lambda *args, **kwargs: 'same')
    assert L.assess(e)[0] == expected
    assert e == before


def test_command_header_cannot_supply_test_counts_during_reassessment(tmp_path, monkeypatch):
    argv = ['printf', 'DONE 4 tests, 1 failure in 1s']
    log = tmp_path / 'header.log'
    log.write_text('$ ' + ' '.join(argv) + '\n# cwd: .\n# started: fixture time\n\n3 passed in 1s\n\n# termination: {}\n')
    e = {'status': 'PASS', 'kind': 'test', 'argv': argv, 'cwd': '.', 'exit': 0,
         'counts': {'passed': 3}, 'log': str(log), 'log_sha256': L.file_sha(log),
         'scope': ['svc'], 'fp_before': 'same', 'fp_after': 'same'}
    monkeypatch.setattr(L, 'scope_fingerprint', lambda *args, **kwargs: 'same')
    assert L.assess(e)[0] == 'VALID'


def test_package_semantics_cannot_verify_a_masked_missing_tool():
    import al_package_profiles as Profiles
    profile = {'semantic_coverage': {'records': [], 'kind': 'lint', 'status': 'SUPPORTED'},
               'intent': 'lint'}
    result = Profiles.result(profile, 0, '/bin/sh: gofmt: command not found\n')
    assert result['execution'] == 'PASS' and result['exit'] == 0
    assert result['semantic_verdict'] == 'INFRA'


def test_large_invalid_go_count_fails_closed_without_integer_exception():
    text = 'DONE ' + '9' * 5000 + ' tests in 1s'
    assert L.parse_counts(text) == {}
    assert L.classify('test', 0, {}, False, 'same', 'same', output=text)[0] == 'INFRA'


def test_container_output_does_not_require_host_go():
    assert L.classify('lint', 0, {}, False, 'same', 'same',
                      output='container formatting check completed\n')[0] == 'PASS'


def test_access_time_change_does_not_invalidate_executable_identity(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import al_verification_inputs as Inputs
    tool = tmp_path / 'tool'
    tool.write_bytes(b'#!/bin/sh\nexit 0\n'); tool.chmod(0o755)
    original_stat = Path.stat
    calls = []
    def observe(path, *args, **kwargs):
        observed = original_stat(path, *args, **kwargs)
        if path != tool:
            return observed
        fields = {key: getattr(observed, key) for key in dir(observed) if key.startswith('st_')}
        calls.append(True)
        if len(calls) > 1:
            fields['st_atime'] += 1
            fields['st_atime_ns'] += 1_000_000_000
        return SimpleNamespace(**fields)
    monkeypatch.setattr(Path, 'stat', observe)
    assert Inputs.executable(str(tool))['path'] == str(tool)


def test_executable_content_mutation_during_read_is_still_rejected(tmp_path, monkeypatch):
    import al_verification_inputs as Inputs
    tool = tmp_path / 'tool'
    tool.write_bytes(b'#!/bin/sh\nexit 0\n'); tool.chmod(0o755)
    original_read = Path.read_bytes
    def read(path):
        raw = original_read(path)
        if path == tool:
            tool.write_bytes(raw + b'# changed\n')
        return raw
    monkeypatch.setattr(Path, 'read_bytes', read)
    with pytest.raises(ValueError, match='unstable or nonexecutable'):
        Inputs.executable(str(tool))
