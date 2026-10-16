"""Executable filter options and Poetry argv positions, without raw-text bans."""
import json
import pytest
from al_shell import check
from hook_regression_helpers import invoke


@pytest.mark.parametrize('policy',['readonly','shared'])
@pytest.mark.parametrize('command',[
 'git cat-file --filters HEAD:payload',
 'git cat-file --batch --filters',
 'git cat-file --batch-check --filters HEAD:payload',
 'git cat-file --batch-command --filters',
 'git cat-file blob --filters HEAD:payload',
 'git cat-file --filt HEAD:payload',
 'git cat-file --textc HEAD:payload',
 'git cat-file --batch-c',
 'git cat-file --path',
 'poetry run git reset --hard',
 'poetry run -- git cat-file --filters HEAD:payload',
 'poetry run env GIT_EXTERNAL_DIFF=evil git diff',
 'env GIT_CONFIG_COUNT=1 poetry run git status',
 'poetry run -- cp owned peer',
 'poetry --directory . run git status',
 'poetry run --directory . git status',
 'poetry run --', 'poetry run', 'poetry install',
 'poetry run -- -- git reset --hard',
])
def test_real_filters_nested_writes_and_unsupported_forms_are_denied(command,policy):
    assert check(command,policy)


@pytest.mark.parametrize('policy',['readonly','shared'])
@pytest.mark.parametrize('command',[
 'git cat-file -p HEAD:payload','git cat-file -t HEAD:payload',
 'git cat-file -s HEAD:payload','git cat-file -e HEAD:payload',
 'git cat-file --batch','git cat-file --batch-check --buffer',
 "git cat-file --batch-check='%(objectname) --filters'",
 'git cat-file --path --filters -p HEAD:payload',
 'git cat-file --path=--filters -p HEAD:payload',
 'git cat-file -- --filters',
 "git log --grep='cat-file --filters'",
 "rg 'poetry run git reset --hard, --filters' .",
 "poetry run rg 'git reset --hard, --filters' -- --write",
 "poetry run -- rg 'git reset --hard, --filters' -- --write",
 'poetry run -- env MODE=test git status', 'uv run pytest -q',
])
def test_literal_option_operands_and_nested_searches_remain_data(command,policy):
    assert check(command,policy)==[]


def test_public_owner_diagnostics_do_not_authorize_filters_or_poetry_writes(cli):
    invoke(cli,'start','--spec','Refuse executable filters and Poetry writes before ownership admission.')
    before=json.loads(cli('show')[1])
    for command in ['git cat-file --batch-command --filters','poetry run -- git reset --hard']:
        invoke(cli,'ownership','--engineer','svc/app.py','--builder','docs/readme.md','--engineer-commands',command,ok=False)
        state=json.loads(cli('show')[1]);assert state['phase']==before['phase'] and state['completed_phases']==before['completed_phases']
        assert state['write_ownership']['PLANNING']['shared_state']
    invoke(cli,'ownership','--engineer','svc/app.py','--builder','docs/readme.md',
           '--engineer-commands',"poetry run -- rg 'git reset --hard, --filters' .")
