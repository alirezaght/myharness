"""Executable option/script positions versus harmless command data."""
import json
import pytest
from al_shell import check
from hook_regression_helpers import invoke


@pytest.mark.parametrize('command',[
    'pnpm format','pnpm run format','pnpm --filter @acme/web run format',
    'pnpm -w generate:contracts','npm --workspace web run generate:contracts',
    'yarn workspace web format','bun run generate:contracts','pnpm --dir apps/web format',
    'git grep --open-files-in-pager pattern','git grep --open-files-in-pager=cp pattern',
    'git grep -O pattern','git grep -Ocp pattern','git grep -n -O cp pattern','git grep -nOcp pattern','git grep -nO cp pattern',
])
@pytest.mark.parametrize('policy',['readonly','shared'])
def test_actual_writer_and_pager_positions_are_denied(command,policy):
    assert check(command,policy),command


@pytest.mark.parametrize('command',[
    "pnpm --filter @acme/web run format:check",'pnpm -w run format:check',
    "npm --workspace web run test -- --grep format",'yarn workspace web run format:check',
    "rg 'pnpm format, generate:contracts'",'git diff -O orderfile',
    "git grep -e --open-files-in-pager",'git grep -e -O -- file',
    'git grep pattern -- --open-files-in-pager','git grep pattern -- -O','git grep -ne-O','git grep -ne--open-files-in-pager','git grep -n -f pager-patterns',
    "git -c color.ui=never grep -e 'pnpm format'",
])
@pytest.mark.parametrize('policy',['readonly','shared'])
def test_search_and_check_arguments_remain_data(command,policy):
    assert check(command,policy)==[],command


@pytest.mark.parametrize('command',['pnpm --filter web run format','git grep -O pattern'])
def test_public_owner_rejection_records_denial_without_phase_advance(cli,repo,state_file,command):
    invoke(cli,'start','--spec','Do not admit commands that mutate a peer file')
    before=state_file().read_bytes()
    code,_,_=cli('ownership','--engineer','svc/app.py','--builder','web/app.js','--engineer-commands',command)
    after=json.loads(state_file().read_text());original=json.loads(before)
    assert code!=0 and after['write_ownership']['PLANNING']['shared_state']
    assert after['phase']==original['phase'] and after['completed_phases']==original['completed_phases']


def test_each_declared_command_is_checked(cli,repo,state_file):
    invoke(cli,'start','--spec','Check a quoted search followed by a real writer')
    before=state_file().read_bytes()
    code,_,_=cli('ownership','--engineer','svc/app.py','--builder','web/app.js','--engineer-commands',"rg 'format, generate:contracts', pnpm format")
    after=json.loads(state_file().read_text());original=json.loads(before)
    assert code!=0 and after['write_ownership']['PLANNING']['shared_state']
    assert after['phase']==original['phase'] and after['completed_phases']==original['completed_phases']
