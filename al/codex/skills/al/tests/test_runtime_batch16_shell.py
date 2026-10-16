"""Config query operations and shell reserved words are positional syntax."""
import pytest
from al_shell import check
from hook_regression_helpers import invoke

CONFIG_BAD=['git config -- --list','git config --local -- core.hooksPath --get','git config core.hooksPath --get','git config --file --get core.hooksPath value',
            'git config --local core.hooksPath value --list','git config --get --list key','git config --get','git config --list key',
            'git config --get key value extra','git config --local --global --get key','git config --unknown --get key','git config get key']
CONFIG_SAFE=['git config -fsettings.cfg --list','git config --local --get core.hooksPath','git config --get-all key','git config --local --list','git config -l',
             "git config --file '--get' --list","git config --file '--list' --get key",'git config --get -- key --list',
             "git config --default '--get' --get key",'git config --get key --list','git config --type bool --get key']
CONTROL=['if true; then cp owned peer; fi','for x in 1; do rm peer; done','while true; do rm peer; done','until false; do cp owned peer; done',
         'case x in x) cp owned peer;; esac','function f { cp owned peer; }','f() { cp owned peer; }','{ cp owned peer; }',
         'then cp owned peer','do rm peer','echo ok; else cp owned peer','! cp owned peer']
CONTROL_SAFE=["rg 'if true; then cp owned peer; fi' file",'echo if then elif else fi for in do done while until case esac function',
              "rg 'for x in 1; do rm peer; done' file","rg '{ cp owned peer; }' file",'echo ok | grep if; printf done',
              '"if" literal-argument']


@pytest.mark.parametrize('command',CONFIG_BAD+CONTROL)
def test_unsafe_positional_forms_refuse_policies_and_public_ownership(cli,command):
    assert check(command,'readonly') and check(command,'shared')
    invoke(cli,'start','--spec','Reject actual mutation syntax without rejecting literal query/search data.')
    code,_,_=invoke(cli,'ownership','--engineer-commands',command,ok=False);assert code==5


@pytest.mark.parametrize('command',CONFIG_SAFE+CONTROL_SAFE)
def test_query_values_and_reserved_words_as_data_remain_allowed(command):
    assert check(command,'readonly')==[] and check(command,'shared')==[]


@pytest.mark.parametrize('program',CONTROL)
def test_shell_wrappers_and_substitutions_reject_recursive_controls(program):
    import shlex
    for command in ['env MODE=test sh -c '+shlex.quote(program),'pnpm exec -- sh -c '+shlex.quote(program),'rg "$('+program+')" file']:
        assert check(command,'readonly') and check(command,'shared')
