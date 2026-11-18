"""Evidence argv boundaries remain explicit and diagnostics show usable syntax."""
import sys

import pytest


@pytest.fixture
def started(cli, state_file):
    result = cli('start', '--spec', 'Verify evidence command arguments stay literal and invalid requests do not execute checks.')
    assert result[0] == 0, result
    return cli, state_file


@pytest.mark.parametrize('action', ['plan', 'run'])
def test_missing_separator_explains_command_boundary_without_mutation(started, action):
    cli, state_file = started
    before = state_file().read_bytes()
    code, out, err = cli('evidence', action, '--area', 'unit', '--scope', 'svc',
                         sys.executable, '-m', 'pytest', '-q')
    assert code == 2 and not out
    assert 'unrecognized arguments:' in err
    assert 'put `--` before the check command' in err and 'Example:' in err
    assert 'evidence ' + action + ' --area unit --scope svc -- python3 -m pytest -q' in err
    assert state_file().read_bytes() == before


@pytest.mark.parametrize('tail', [(), ('--',)])
def test_plan_requires_nonempty_command_and_shows_example(started, tail):
    cli, state_file = started
    before = state_file().read_bytes()
    code, out, err = cli('evidence', 'plan', '--area', 'unit', '--scope', 'svc', *tail)
    assert code == 2 and not out
    assert 'check command' in err and 'Example:' in err
    assert '-- python3 -m pytest -q' in err
    assert state_file().read_bytes() == before


def test_explicit_empty_run_never_falls_back_to_planned_command(started, monkeypatch):
    import al_evidence
    cli, state_file = started
    assert cli('evidence', 'plan', '--area', 'unit', '--scope', 'svc', '--',
               sys.executable, '-m', 'compileall', '-q', 'svc')[0] == 0
    before = state_file().read_bytes()
    calls = []
    monkeypatch.setattr(al_evidence, 'cmd_evidence_run', lambda a, sid: calls.append(a.command_argv))
    code, out, err = cli('evidence', 'run', '--area', 'unit', '--')
    assert code == 2 and 'check command after `--` is empty' in err and not out
    assert calls == [] and state_file().read_bytes() == before


def test_plan_keeps_command_flags_spaces_empty_arguments_and_literal_shell_text(started):
    cli, _ = started
    command = [sys.executable, '-c', 'print("literal only")', '--scope', 'command scope',
               '--fresh', '--', '-1', '', 'space arg', '$(never execute)', 'semi;colon']
    result = cli('evidence', 'plan', '--area', 'unit', '--scope', 'svc', '--kind', 'check',
                 '--tier', 'focused', '--cwd', '.', '--env', 'ONE,TWO', '--', *command)
    assert result[0] == 0, result
    plan = cli.state()['evidence']['plan']['unit']
    assert plan['argv'] == command and plan['scope'] == ['svc']
    assert plan['kind'] == 'check' and plan['tier'] == 'focused' and plan['cwd'] == '.'
    assert plan['env'] == ['ONE', 'TWO']


def test_repeated_env_flags_and_comma_lists_merge_deterministically(started):
    cli, _ = started
    result = cli('evidence', 'plan', '--area', 'unit', '--scope', 'svc',
                 '--env', 'PYTHONPATH,SHARED', '--env', 'OTHER,SHARED', '--env', 'LAST',
                 '--', sys.executable, '-V')
    assert result[0] == 0, result
    assert cli.state()['evidence']['plan']['unit']['env'] == [
        'PYTHONPATH', 'SHARED', 'OTHER', 'LAST']


@pytest.mark.parametrize('action', ['plan', 'run'])
def test_command_options_cannot_satisfy_required_al_arguments(started, action):
    cli, state_file = started
    before = state_file().read_bytes()
    code, out, err = cli('evidence', action, '--scope', 'svc', '--',
                         sys.executable, '--area', 'command-area')
    assert code == 2 and '--area' in err and 'required' in err and not out
    assert state_file().read_bytes() == before


def test_run_preserves_planned_no_separator_and_exact_adhoc_argv(started, monkeypatch):
    import al_evidence
    cli, _ = started
    seen = []
    monkeypatch.setattr(al_evidence, 'cmd_evidence_run', lambda a, sid: seen.append(a))
    result = cli('evidence', 'run', '--area', 'stored', '--fresh')
    assert result[0] == 0 and seen[-1].command_argv == [] and seen[-1].fresh
    command = [sys.executable, '-m', 'pytest', '-q', '--area', 'command-area', '--fresh']
    result = cli('evidence', 'run', '--area', 'adhoc', '--scope', 'svc', '--tail', '0', '--', *command)
    assert result[0] == 0 and seen[-1].command_argv == command
    assert seen[-1].area == 'adhoc' and seen[-1].scope == 'svc'
    assert seen[-1].tail == 0 and not seen[-1].fresh


def test_missing_scope_has_a_specific_refusal_without_mutation(started):
    cli, state_file = started
    before = state_file().read_bytes()
    code, out, err = cli('evidence', 'plan', '--area', 'unit', '--', sys.executable, '-V')
    assert code != 0 and 'requires --scope PATH[,PATH...]' in str(code) and not out
    assert state_file().read_bytes() == before


@pytest.mark.parametrize('action', ['plan', 'run'])
@pytest.mark.parametrize('option', ['--area', '--scope'])
def test_missing_al_option_operand_still_refuses(started, action, option):
    cli, state_file = started
    before = state_file().read_bytes()
    code, out, err = cli('evidence', action, '--area', 'unit', '--scope', 'svc', option,
                         '--', sys.executable, '-V')
    assert code == 2 and 'expected one argument' in err and not out
    assert state_file().read_bytes() == before


def test_negative_al_operands_and_explicit_session_do_not_consume_command_flags(started, monkeypatch):
    import al_evidence
    cli, _ = started
    seen = []
    monkeypatch.setattr(al_evidence, 'cmd_evidence_run', lambda a, sid: seen.append((a, sid)))
    session = cli.state()['session_id']
    command = [sys.executable, '-V', '-1', '--session', 'command-session', '--tail', '99']
    result = cli('--session', session, 'evidence', 'run', '--area', 'unit', '--scope', 'svc',
                 '--timeout', '-1', '--tail', '-2', '--', *command)
    assert result[0] == 0, result
    args, sid = seen[-1]
    assert args.session == session and sid == session and args.command_argv == command
    assert args.timeout == -1 and args.tail == -2


def test_flag_shaped_al_operand_keeps_argparse_equals_syntax(started):
    cli, _ = started
    command = [sys.executable, '-V']
    result = cli('evidence', 'plan', '--area=-unit', '--scope=svc', '--', *command)
    assert result[0] == 0, result
    assert cli.state()['evidence']['plan']['-unit']['argv'] == command


def test_unrelated_command_unknown_option_keeps_standard_parser_error(started):
    cli, state_file = started
    before = state_file().read_bytes()
    code, out, err = cli('show', '--unknown-option')
    assert code == 2 and 'unrecognized arguments: --unknown-option' in err and not out
    assert 'put `--`' not in err and 'Example:' not in err
    assert state_file().read_bytes() == before


@pytest.mark.parametrize('action', ['plan', 'run'])
def test_help_includes_delimiter_and_example(started, action):
    cli, _ = started
    code, out, err = cli('evidence', action, '--help')
    assert code == 0 and not err
    assert '-- PROGRAM [ARG ...]' in out and 'Example:' in out
