"""Realistic selector bugs: later -c arguments must never replace startup syntax."""
import pytest
import al_shell

BAD=["bash --noprofile --norc -ic 'rm victim' -c 'git status'",
     "bash -lc 'rm victim' -c 'git status'", "sh -ic 'rm victim' -c 'git status'",
     "zsh --norc -c 'git status'", "dash +c 'git status'", "bash -- -c 'git status'",
     "bash script.sh -c 'git status'", "bash -s -c 'git status'", "bash -c",
     "bash --unknown -c 'git status'", "bash --norc --norc -c 'git status'",
     "bash -c -c 'git status'", "bash -c 'rm victim' -c 'git status'",
     "env SAFE=1 command bash -ic 'rm victim' -c 'git status'",
     "find . -exec bash -ic 'rm victim' -c 'git status' \\;",
     "find . -exec sh -c 'rg -- {} README.md' \\;",
     "find . -exec sh -c '{}' \\;", "bash -c 'env bash -ic rm -c true'"]
GOOD=["sh -c 'git status'", "bash --noprofile --norc -c 'rg -- -c README.md'",
      "bash --norc --noprofile -c 'git status' -c 'rm victim'", "zsh -c 'git status'",
      "dash -c 'git status'", "sh -c 'rg -- -c README.md' '--' '-c'",
      "env SAFE=1 command bash --norc -c 'git status'", "find . -exec sh -c 'git status' \\;",
      "find . -exec sh -c 'rg safe README.md' '{}' \\;", "rg 'bash -ic rm -c true' README.md"]

@pytest.mark.parametrize('command',BAD)
def test_startup_selector_refuses_unsupported_or_mutating_program(command):
    for policy in ('readonly','shared'):assert al_shell.check(command,policy),command

@pytest.mark.parametrize('command',GOOD)
def test_selected_script_and_trailing_positional_data_are_literal(command):
    for policy in ('readonly','shared'):assert al_shell.check(command,policy)==[],command


def test_selected_script_retains_environment_redirects_and_substitutions():
    for command in ["env GIT_PAGER=bad bash -c 'git status'", "bash -c 'git status' > output", "bash -c 'echo $(rm victim)'", "bash -c 'git status' $(rm victim)"]:
        for policy in ('readonly','shared'):assert al_shell.check(command,policy)
