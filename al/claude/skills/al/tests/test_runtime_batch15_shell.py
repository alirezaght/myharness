"""Find substitutions and installing manager dispatch require positional refusal."""
import pytest
from al_shell import check
from hook_regression_helpers import invoke

DYNAMIC=["{}","./{}/rg","env MODE=test {}","command -p {}","timeout 2 nice -n 1 {}",
         "pnpm exec -- {}","uv run -- env {}","yarn exec -- {}",
         "sh -c 'rg {} file'","env sh -c '{}'","sh -c 'pnpm exec {}'"]
DENIED=["pnpm dlx prisma generate","pnpm --filter web dlx echo harmless","yarn --cwd . dlx prisma migrate deploy",
        "yarn workspace web dlx echo harmless","env MODE=test pnpm dlx echo harmless","uv run -- yarn dlx echo harmless"]
SAFE=["find . -exec rg '{}' file \\;","find . -execdir rg '{}' file +","find . -exec sh -c 'rg \"$1\" file' _ '{}' \\;",
      "rg 'pnpm dlx {} prisma generate' file","pnpm run verify dlx '{}'","uv run -- pytest -q","pnpm run dlx harmless","yarn workspace web run dlx harmless"]


@pytest.mark.parametrize('action',['-exec','-execdir','-ok','-okdir'])
@pytest.mark.parametrize('inner',DYNAMIC)
@pytest.mark.parametrize('end',['\\;','+'])
def test_dynamic_find_effective_executables_are_denied_through_wrappers(action,inner,end):
    for policy in ['readonly','shared']:assert check('find . '+action+' '+inner+' '+end,policy)


@pytest.mark.parametrize('command',DENIED+['pnpm --unknown dlx echo ok','yarn dlx','pnpm --filter'])
def test_installing_dlx_and_unknown_wrappers_refuse_both_policies_and_ownership(command,cli):
    assert check(command,'readonly') and check(command,'shared')
    invoke(cli,'start','--spec','Reject installing dispatch before parallel ownership admission.')
    code,_,_=invoke(cli,'ownership','--engineer-commands',command,ok=False);assert code==5


@pytest.mark.parametrize('command',SAFE)
def test_literal_placeholder_and_dlx_arguments_remain_safe(command):
    assert check(command,'readonly')==[] and check(command,'shared')==[]


def test_dynamic_find_public_ownership_refuses_but_search_argument_is_safe(cli):
    invoke(cli,'start','--spec','Validate find executable positions without rejecting search data.')
    for inner in ['{}','env {}','pnpm exec -- {}',"sh -c '{}' "]:
        code,_,_=invoke(cli,'ownership','--engineer-commands','find . -exec '+inner+' \\;',ok=False);assert code==5
    invoke(cli,'ownership','--engineer-commands',SAFE[0])
