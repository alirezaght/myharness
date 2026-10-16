"""Executable selectors and exact Git options cannot be hidden in literal arguments."""
import shlex
import subprocess
import sys
import pytest
from al_shell import check
from hook_regression_helpers import invoke

DENIED=[
 'git grep --open-files-in-page=touch -e text', 'git grep --open-files-in-pager -e text',
 'git grep -nOtouch text','git grep --unknown text','git grep --line-number=1 text',
 'git grep --regexp','git grep -ne','git grep --threads',
 'git diff --out=file','git diff --ext-dif','git log --textcon','git show --output=file',
 'git status --porc','git ls-files --unknown','git ls-tree --form=bad',
 'git rev-parse --exec-path','git merge-base --unknown HEAD HEAD',
 'git blame --ext-diff file','git describe --unknown','git check-ignore --unknown file',
 'git config --get --unknown','git config --get key --add key value', 'git log --max-count',
 'python3 -cprint(1)','python3 -Icprint(1)','python3 -c print -m pytest',
 'python3 -m os -m pytest','python3 -Imos','python3 -m pytest.bad',
 'python3 -m','python3 -W','python3 -X presite=evil -m pytest',
 'python3 --unknown -m pytest','python3 -i -m pytest','python3 -','python3',
 'node -eprocess.exit(0)','node -peprocess.exit(0)','node --eval=process.exit(0)',
 'ruby -eputs','ruby -weputs','perl -eprint','perl -weprint','perl -Eprint',
 'node --require evil script.js','ruby --unknown script.rb','perl -MModule script.pl',
]
ALLOWED=[
 'git grep -e --open-files-in-page','git grep -ne--open-files-in-page',
 'git grep --regexp=--open-files-in-page -- --literal',
 'git grep --regexp --open-files-in-pager -- --literal','git grep -n -e text -- --literal',
 'git grep --ignore-case --word-regexp --line-number --context 2 -e text',
 'git log --grep --open-files-in-page','git log -1 --format=%s',
 'git diff -O orderfile -- --output=file','git status --porcelain=v1',
 'git config --get -- --unknown','git ls-files --exclude --output=file',
 'git blame -L 1,2 file','git describe --match --output=file',
 'python3 -m pytest --version','python3 -Impytest --version','python3 -B -mcompileall --help',
 'python3 -W ignore -X dev -m pytest --version',
 'python3 script.py -c literal -m pytest','python3 -- -c literal',
 'node script.js -e literal','ruby script.rb -e literal','perl script.pl -e literal',
]


@pytest.mark.parametrize('policy',['readonly','shared'])
@pytest.mark.parametrize('command',DENIED)
def test_real_selectors_and_unknown_git_options_are_denied(command,policy):
    assert check(command,policy),command


@pytest.mark.parametrize('policy',['readonly','shared'])
@pytest.mark.parametrize('command',ALLOWED)
def test_consumed_operands_and_actual_safe_selectors_remain_data(command,policy):
    assert check(command,policy)==[],command


def test_real_safe_selectors_and_script_arguments_execute_without_reclassification(repo):
    for argv in [[sys.executable,'-Impytest','--version'],[sys.executable,'-B','-mcompileall','--help']]:
        assert subprocess.run(argv,cwd=repo,capture_output=True).returncode==0
    script=repo/'script.py';script.write_text('import sys\nprint(repr(sys.argv[1:]))\n')
    argv=[sys.executable,str(script),'-c','literal','-m','pytest']
    assert check(shlex.join(argv),'shared')==[]
    result=subprocess.run(argv,cwd=repo,capture_output=True,text=True)
    assert result.returncode==0 and result.stdout.strip()=="['-c', 'literal', '-m', 'pytest']"


@pytest.mark.parametrize('command',['git grep --open-files-in-page=touch -e text','python3 -Icx -m pytest'])
def test_public_ownership_refuses_admission_but_records_negative_diagnostic(cli,command):
    invoke(cli,'start','--spec','A substantive ownership selector regression task.')
    invoke(cli,'ownership','--engineer','svc/app.py','--builder','web/app.js','--engineer-commands',command,ok=False)
    import json
    state=json.loads(cli('show')[1]);assert state['write_ownership']['PLANNING']['shared_state']['al-engineer']
    assert state['phase']=='PLANNING' and not state['writers']
