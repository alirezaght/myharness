"""Observational planning queries and exact measurement need no inline execution."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

import al_shell


@pytest.mark.parametrize('command', [
    'git branch --show-current',
    'git --no-pager --no-optional-locks branch --show-current',
    "sh -c 'git branch --show-current'",
    "sh -c 'wc -l -- README.md'",
])
def test_observational_branch_and_literal_shell_wrapper_are_allowed(command):
    assert al_shell.check(command, 'readonly') == []


@pytest.mark.parametrize('command', [
    'git branch new-branch',
    'git branch --show-current new-branch',
    'git branch --show-current --delete old-branch',
    'git branch --show-current --set-upstream-to=origin/main',
    "sh -c 'git branch --show-current; touch changed'",
    "sh -c 'git branch --show-current &'",
    "python3 -I -S -B -c 'open(\"changed\",\"w\").write(\"x\")'",
    "python3 -I -S -B - <<'PY'\nprint(1)\nPY",
])
def test_mutations_background_and_arbitrary_inline_execution_remain_refused(command):
    assert al_shell.check(command, 'readonly')


def helper():
    return Path(__file__).resolve().parents[1] / 'scripts/al_readonly_lines.py'


def run_measure(paths, cwd, env=None):
    return subprocess.run([sys.executable, '-I', '-S', '-B', str(helper()), '--', *map(str, paths)], cwd=cwd, env=env, text=True, capture_output=True, timeout=10)


def test_isolated_measurement_counts_complete_files_without_site_or_checkout_imports(tmp_path):
    import os
    empty, newline, final = [tmp_path / name for name in ('empty.py', 'newline.py', 'final.py')]
    empty.write_bytes(b'')
    newline.write_bytes(b'x\n' * 300)
    final.write_bytes(b'x\n' * 299 + b'x')
    marker = tmp_path / 'unexpected-site-write'
    (tmp_path / 'sitecustomize.py').write_text(f'open({str(marker)!r},"w").write("bad")')
    names_before = {p.name for p in tmp_path.iterdir()}
    result = run_measure([empty, newline, final], tmp_path, dict(os.environ, PYTHONPATH=str(tmp_path)))
    assert result.returncode == 0, result.stderr
    assert [row['lines'] for row in json.loads(result.stdout)['files']] == [0, 300, 300]
    assert not marker.exists()
    assert {p.name for p in tmp_path.iterdir()} == names_before


def test_measurement_rejects_links_and_missing_inputs_instead_of_estimating(tmp_path):
    source = tmp_path / 'source.py'
    source.write_text('x\n')
    link = tmp_path / 'link.py'
    link.symlink_to(source)
    result = run_measure([link, tmp_path / 'missing.py'], tmp_path)
    assert result.returncode == 1
    rows = json.loads(result.stdout)['files']
    assert [row['status'] for row in rows] == ['aliased-path', 'unavailable']
    assert all('lines' not in row for row in rows)


def test_measurement_helper_invocation_is_allowed_without_general_inline_permission():
    import shlex
    command = shlex.join(['python3', '-I', '-S', '-B', str(helper()), '--', 'README.md'])
    assert al_shell.check(command, 'readonly') == []


def counter_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location('readonly_source_counter_under_test', helper())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_failure_after_read_still_consumes_aggregate_budget(tmp_path, monkeypatch, capsys):
    module = counter_module()
    first, second = tmp_path / 'first.py', tmp_path / 'second.py'
    first.write_bytes(b'abcd')
    second.write_bytes(b'efgh')
    actual_stat = module.os.stat

    def fail_final_stat(name, *args, **kwargs):
        if name == first.name and kwargs.get('dir_fd') is not None:
            first.unlink()
            raise FileNotFoundError('synthetic post-read removal')
        return actual_stat(name, *args, **kwargs)

    monkeypatch.setattr(module.os, 'stat', fail_final_stat)
    monkeypatch.setattr(module, 'MAX_TOTAL_BYTES', 6)
    assert module.main([str(first), str(second)]) == 1
    rows = json.loads(capsys.readouterr().out)['files']
    assert [row['status'] for row in rows] == ['unavailable', 'read-budget-exceeded']
    assert all('lines' not in row for row in rows)


@pytest.mark.parametrize('replace_after_open', [False, True])
def test_parent_replacement_cannot_produce_an_aliased_measurement(tmp_path, monkeypatch, replace_after_open):
    module = counter_module()
    selected, original, other = [tmp_path / name for name in ('selected', 'original', 'other')]
    selected.mkdir()
    other.mkdir()
    source = selected / 'source.py'
    source.write_bytes(b'original\n')
    (other / source.name).write_bytes(b'replacement\n' * 100)
    actual_open, actual_read = module.os.open, module.os.read
    reads, replaced = [], False

    def replace():
        nonlocal replaced
        if not replaced:
            selected.rename(original)
            selected.symlink_to(other, target_is_directory=True)
            replaced = True

    def observed_open(name, flags, *args, **kwargs):
        if not replace_after_open and name == selected.name and kwargs.get('dir_fd') is not None:
            replace()
        return actual_open(name, flags, *args, **kwargs)

    def observed_read(descriptor, count):
        block = actual_read(descriptor, count)
        reads.append(block)
        if replace_after_open:
            replace()
        return block

    monkeypatch.setattr(module.os, 'open', observed_open)
    monkeypatch.setattr(module.os, 'read', observed_read)
    row, consumed = module.measure(str(source), module.MAX_TOTAL_BYTES)
    assert replaced and row['status'] != 'measured' and 'lines' not in row
    assert reads == ([b'original\n'] if replace_after_open else [])
    assert consumed == (9 if replace_after_open else 0)
